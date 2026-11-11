"""파트장 API(P-11, B-04 · B-05 · B-06 · B-08).

승인 대기 · 검토 결정 · 이력 변환(R12) · 호출 답 · 권한 등급(R13) · 감사 조회.

파트장 역할만(앱 표시 · 쿠키 · 계정 역할 대조는 current_user). 쓰기는 업무 기록 + 감사, 거부는 감사 행을 따로 남긴다.
계산 정의는 사용자 결정 018(권한 위반 · 등급 표 범위 · 직접 확인 · 숙련자 인계 답 · 이력 변환).
"""

import json
import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Connection, select, text

from c1a15 import checkrecord, config, redact
from c1a15.api import deps, final, flow, precheck, responses, screen, sessions
from c1a15.api.deps import User, conn, current_user
from c1a15.api.schemas import AnswerBody, DecisionBody, EditsBody, GradeBody
from c1a15.db import models as m
from c1a15.deploy import inbox, runs
from c1a15.engine import session as session_engine
from c1a15.llm import grounding
from c1a15.llm.base import Draft

router = APIRouter(prefix="/api/lead")
_NUM_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:[A-Za-z%°]+)?")
GRADE_LABEL = {"A": "단독 가능", "B": "확인 후", "C": "승인 필요", "D": "범위 밖"}
CALL_OPTIONS = {  # 1.4 · L04: 파트장 호출 3종(D는 승인 후 진행 없음 — AC-23-C3), 숙련자 인계 2종
    "approval": [
        ("approved", "승인 후 진행", "정비자 앱에 그 C등급 점검 지시가 열립니다"),
        ("daytime", "주간 처리", "저부하 감시 운전, 보고서 미확인 · 요청에 기록"),
        ("direct", "직접 확인", "설비 정지 대기, 처리 결과를 보고서에 기록"),
    ],
    "handover": [
        ("assign", "주간 숙련자 배정", "주간 근무 숙련자에게 넘깁니다"),
        ("direct", "직접 확인", "파트장이 확인하고 결과를 보고서에 기록"),
    ],
}
REVIEW_FIELDS = ["cause", "check_path", "action", "evidence_records", "unresolved"]  # L03 R-REPORTVIEW 다섯 행
REVIEW_LABEL = {"unresolved": "미확인"}


def lead_user(user: User = Depends(current_user)) -> User:
    if user.app != "lead":
        raise HTTPException(status_code=403, detail="lead_only")
    return user


def _deny(user: User, reason: str, target: str, **detail) -> None:
    sessions.audit_rejection(user.user_id, "reject", target, {"reason": reason, **detail})
    raise HTTPException(status_code=409, detail=reason)


def _names(c: Connection) -> dict[str, str]:
    return dict(c.execute(select(m.Account.user_id, m.Account.display_name)).all())


def _iso(at: datetime | None) -> str | None:
    return at.isoformat() if at else None


# ---------------------------------------------------------------- 승인 대기(L02 · L03)


def violations(c: Connection, session_id: int) -> int:
    """결정 018 ①: 그 세션 업무 기록에서 지시로 열리지 않은 절의 결과 · 조치 수(확정 전 확인 ②와 같은 계산)."""
    events = sessions.events_of(c, session_id)
    opened: set[str] = set()
    count = 0
    for e in events:
        if e["type"] == "step_opened" and e["decision"].get("kind") in {
            "instruct",
            "instruct_after_checklist",
            "action",
            "action_after_checklist",
        }:
            opened.add(e["decision"]["section"])
        if e["type"] in {"result", "action_done"} and not e.get("void") and e.get("section") not in opened:
            count += 1
    return count


def _report_rows(c: Connection, status: str = "awaiting_approval") -> list:
    return c.execute(
        select(
            m.Report.__table__, m.DiagSession.alarm_id, m.Alarm.equipment_id, m.Alarm.symptom_group, m.Alarm.occurred_at
        )
        .join(m.DiagSession, m.DiagSession.session_id == m.Report.session_id)
        .join(m.Alarm, m.Alarm.alarm_id == m.DiagSession.alarm_id)
        .where(m.Report.status == status)
        .order_by(m.Report.confirmed_at.desc())
    ).all()


def _field(r, key: str) -> str:
    return next((f["text"] for f in r.fields if f["key"] == key), "")


@router.get("/overview", response_model=responses.LeadOverview)
def overview(user: User = Depends(lead_user), c: Connection = Depends(conn, scope="function")) -> dict:
    """L02: 요약 타일 3개(실제 건수 — AC-27-C1) · 승인 대기 행.

    행 = 설비 · 알람 · 확정 원인 · 작성 · 확정 시각 · 근거 기록 수 · 근거 적음 · 권한 위반.
    """
    names = _names(c)
    rows = []
    for r in _report_rows(c):
        evidence = [x for x in _field(r, "evidence_records").split(" · ") if x.strip()]
        rows.append(
            {
                "report_id": r.report_id,
                "session_id": r.session_id,
                "equipment_id": r.equipment_id,
                "group_name": deps.group_names()[r.symptom_group],
                "cause": _field(r, "cause"),
                "line": sessions.report_line(c, r.session_id),  # D-642 R2
                "author_name": names.get(r.author, r.author),
                "confirmed_at": _iso(r.confirmed_at),
                "evidence_count": len(evidence),
                "low_evidence": r.low_evidence,
                "violations": violations(c, r.session_id),
            }
        )
    calls_open = c.execute(text("select count(*) from ops.calls where answer is null")).scalar()
    return {
        "acted": acted_rows(c),  # 약속 v9 '호출 없이 조치함'
        "prechecks": precheck.candidates(c),  # D-642 E 사전 점검 후보(모든 설비 · 담당자 · 그래프 반영 신호 뒤 다시 셈)
        "now": datetime.now(UTC).isoformat(),
        "tiles": {
            "awaiting": len(rows),
            "calls_open": calls_open,
            "violations": sum(1 for x in rows if x["violations"]),
        },
        "rows": rows,
    }


def acted_rows(c: Connection, limit: int = 20) -> list[dict]:
    """약속 v9 '호출 없이 조치함': 판정이 lead_notice acted_without_call을 낸 세션(첫 그 단계 시각) — 최근 순."""
    rows = c.execute(
        text(
            "select distinct on (e.session_id) e.session_id, e.created_at, a.equipment_id, a.symptom_group, "
            "u.display_name from ops.session_events e join ops.diag_sessions s on s.session_id = e.session_id "
            "join ops.alarms a on a.alarm_id = s.alarm_id left join ops.accounts u on u.user_id = s.user_id "
            "where e.type = 'step_opened' and e.payload->'decision'->>'lead_notice' = 'acted_without_call' "
            "order by e.session_id, e.seq"
        )
    ).all()
    out = [
        {
            "session_id": r.session_id,
            "equipment_id": r.equipment_id,
            "group_name": deps.group_names()[r.symptom_group],
            "tech_name": r.display_name,
            "at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    return sorted(out, key=lambda x: x["at"], reverse=True)[:limit]


def _report(c: Connection, report_id: int):
    r = c.execute(
        select(
            m.Report.__table__,
            m.DiagSession.alarm_id,
            m.Alarm.equipment_id,
            m.Alarm.symptom_group,
            m.Alarm.occurred_at,
            m.Alarm.eval_mode,
        )
        .join(m.DiagSession, m.DiagSession.session_id == m.Report.session_id)
        .join(m.Alarm, m.Alarm.alarm_id == m.DiagSession.alarm_id)
        .where(m.Report.report_id == report_id)
    ).first()
    if r is None:
        raise HTTPException(status_code=404, detail="no_report")
    return r


@router.get("/reports/{report_id}", response_model=responses.LeadReport)
def report_detail(
    report_id: int, user: User = Depends(lead_user), c: Connection = Depends(conn, scope="function")
) -> dict:
    """L03 R-REPORTVIEW: 제목(설비 · 알람 · 시각 · 작성자) · 확정 원인 · 점검 경로 · 조치 · 근거 사례 · 미확인."""
    r = _report(c, report_id)
    names = _names(c)
    by_key = {f["key"]: f for f in r.fields}
    return {
        "report_id": r.report_id,
        "session_id": r.session_id,
        "status": r.status,
        "equipment_id": r.equipment_id,
        "group_name": deps.group_names()[r.symptom_group],
        "occurred_at": _iso(r.occurred_at),
        "author_name": names.get(r.author, r.author),
        "low_evidence": r.low_evidence,
        "fields": [
            {"key": k, "label": REVIEW_LABEL.get(k, by_key[k]["label"]), "text": by_key[k]["text"]}
            for k in REVIEW_FIELDS
        ],
        "violations": violations(c, r.session_id),
    }


@router.get("/reports/{report_id}/final", response_model=responses.FinalReport)
def report_final(
    report_id: int, user: User = Depends(lead_user), c: Connection = Depends(conn, scope="function")
) -> dict:
    """D-528 보고서 검토 = 최종본 문서(구역 1 ~ 5 · 읽기만)."""
    out = final.final_report(c, report_id)
    if out is None:
        raise HTTPException(status_code=404, detail="no_report")
    return out


def _edit_grounding(c: Connection, r) -> grounding.Grounding:
    """수정 문장 R8 원천(L03 / edit_failed: '세션 입력 · 매뉴얼 원문').

    확정한 보고서 문장 · 세션 입력 · 센서 값 · 그 세션 절의 매뉴얼 원문.
    """
    row = sessions._row(c, r.session_id)
    events = sessions.events_of(c, r.session_id)
    k = flow.knowledge_of(events)
    facts = {f["key"]: f["text"] for f in r.fields}
    g = flow._report_grounding(row, events, facts)
    secs = set(g.allowed_sections)
    inputs = [str(e.get(x, "")) for e in events for x in ("memo", "values", "value") if e.get(x)]
    manual = [k.sections[s]["text"] for s in secs if s in k.sections]
    alarm = flow.sessions_alarm(c, row)
    sensor = [str(v) for v in alarm.sensor_snapshot.values()] + [
        str(alarm.alarm.get("value")),
        str(alarm.alarm.get("boundary")),
    ]
    nums = g.source_numbers | grounding.numbers_in(*inputs, *manual, *sensor)
    return grounding.Grounding(
        allowed_sections=g.allowed_sections, source_numbers=nums, top_record_ids=g.top_record_ids
    )


def check_edits(c: Connection, r, edits: dict[str, str]) -> dict:
    """수정 후 승인 문장 검사(R8, AC-06 P-11 부분): 실패 행 · 문구 — 실패가 있으면 결정 저장 잠김."""
    g = _edit_grounding(c, r)
    out = []
    for key, txt in edits.items():
        if key not in flow.FIELD_KEYS:
            raise HTTPException(status_code=422, detail="invalid_report_field")
        ok = grounding.is_grounded(Draft(txt), g)
        missing = sorted(grounding.numbers_in(txt) - g.source_numbers)
        out.append({"key": key, "ok": ok, "unsupported_numbers": missing})
    fails = [x for x in out if not x["ok"]]

    def quote(key: str, nums: list[str]) -> str:  # L03 / edit_failed: '"1,850 rpm" 수치가 …' — 수치가 든 원래 표현
        found = [m.group(0).strip() for m in _NUM_TOKEN.finditer(edits[key]) if any(n in m.group(0) for n in nums)]
        return ", ".join(dict.fromkeys(found)) or ", ".join(nums)

    messages = [
        f'"{quote(x["key"], x["unsupported_numbers"])}" 수치가 세션 입력·매뉴얼 원문에 없습니다. '
        "문장을 고쳐야 승인할 수 있습니다."
        if x["unsupported_numbers"]
        else "절 번호 · 근거 기록이 세션 기록에 없습니다. 문장을 고쳐야 승인할 수 있습니다."
        for x in fails
    ]
    return {"fields": out, "ok": not fails, "messages": messages}


@router.post("/reports/{report_id}/check", response_model=responses.EditCheck)
def report_check(
    report_id: int, body: EditsBody, user: User = Depends(lead_user), c: Connection = Depends(conn, scope="function")
) -> dict:
    return check_edits(c, _report(c, report_id), body.edits)


def history_record(c: Connection, r, fields: list[dict], approval: dict) -> dict:
    """결정 018 ⑤ · R12: 승인 보고서 → 정비 이력 한 건(기록 번호 'R-' + 보고서 번호)."""
    events = sessions.events_of(c, r.session_id)
    k = flow.knowledge_of(events)
    alarm = flow.sessions_alarm(c, sessions._row(c, r.session_id))
    names = _names(c)
    f = {x["key"]: x["text"] for x in fields}
    state = sessions.session.fold(
        k,
        alarm.symptom_group,
        sessions.similar_of(sessions._row(c, r.session_id)),
        sessions.fold_inputs(events),
    )
    steps = []
    for e in events:
        if e.get("void"):
            continue
        t = e["type"]
        if t == "lock":
            steps.append(
                {"at": e["_at"], "kind": "lock", "section": "1.2", "name": k.sections["1.2"]["title"], "result": "완료"}
            )
        elif t == "result" and e.get("value"):
            st = {
                "at": e["_at"],
                "kind": "check",
                "section": e["section"],
                "name": k.sections[e["section"]]["title"],
                "result": e["value"],
            }
            if e.get("memo"):
                st["note"] = e["memo"]
            cf = e.get("corrected_from") or {}
            if cf:  # 결정 019 ④: 최신 값 + 정정 사실만(원래 값은 감사 기록)
                st["corrections"] = cf.get("count", 1)
            steps.append(st)
        elif t == "action_done":
            steps.append(
                {
                    "at": e["_at"],
                    "kind": "action",
                    "section": e["section"],
                    "name": k.sections[e["section"]]["title"],
                    "result": "완료",
                }
            )
        elif t == "restart_result":
            steps.append(
                {
                    "at": e["_at"],
                    "kind": "restart",
                    "section": "6.1",
                    "name": k.sections["6.1"]["title"],
                    "result": "경계 이내" if e["outcome"] == "within" else "여전히 밖",
                }
            )
    for s in steps:
        s["by"] = names.get(r.author, r.author)
    cause_name = f["cause"]
    # 결정 019 ①: 정정 호출에 주간 처리 · 직접 확인이면 확정 원인 '원인 미상'(판정 상태의 확정 원인은 쓰지 않음)
    cause_code = state.confirmed if cause_name not in set(flow.OUTCOME_CAUSE.values()) else None
    outcome = (
        {"type": "원인 확정", "cause_code": cause_code, "cause_name": cause_name}
        if cause_code
        else {  # 생성 이력과 같은 결과 종류 이름(screen.OUTCOME_LABEL)
            "type": "이상 없음·감시" if cause_name.replace(" ", "") == "이상없음·감시" else "원인 미상·숙련자 인계",
            "cause_code": None,
            "cause_name": None,
        }
    )
    downtime = int("".join(ch for ch in f["downtime"] if ch.isdigit()) or 0)
    rec = {
        "record_id": f"R-{r.report_id:04d}",
        "equipment_id": alarm.equipment_id,
        "equipment_type": deps.equipment_types().get(alarm.equipment_id),
        "occurred_at": alarm.occurred_at.isoformat(),
        "closed_at": _iso(r.confirmed_at),
        "symptom_group": {"code": alarm.symptom_group, "name": deps.group_names()[alarm.symptom_group]},
        "alarm": alarm.alarm,
        "sensor_snapshot": alarm.sensor_snapshot,
        "steps": steps,
        "outcome": outcome,
        "action": f["action"],
        "downtime_min": downtime,
        "unconfirmed": [] if f["unresolved"] == "없음" else [f["unresolved"]],
        "approval": {"status": "승인", "by": names.get(approval["by"], approval["by"]), "at": approval["at"]},
        "source": {"report_id": r.report_id},
        "provenance": "approved_report",
        "narrative": " ".join(
            x.rstrip(".") + "."
            for x in (
                f["equipment_alarm"],
                f["sensor_evidence"],
                f["check_path"],
                f"{f['cause']}로 판단",
                f["action"],
                f"정지 {downtime}분",
            )
            if x
        ),
    }
    ex = flow.exception_of(
        events
    )  # D-538: 확정 때 함께 보낸 예외 상황 기록 — 이력 원본 층에 같이(서술 · 벡터 글 그대로)
    if ex:
        rec["exception_record"] = ex
    return rec


def exception_record_list(c: Connection) -> dict:
    """D-538: 승인된 예외 상황 기록 — 정비 이력 원본 층(승인 보고서 이력)의 record.exception_record만, 발생 순."""
    rows = c.execute(
        text(
            "select record_id, record from knowledge.history_records "
            "where source = 'approved_report' and record->'exception_record' is not null "
            "order by occurred_at, record_id"
        )
    ).all()
    return {
        "items": [
            {
                "record_id": r.record_id,
                "report_id": r.record["source"]["report_id"],
                "approved_at": (r.record.get("approval") or {}).get("at"),
                "exception_record": flow.record_out(r.record["exception_record"]),
            }
            for r in rows
        ]
    }


def _add_history(c: Connection, rec: dict, symptom_group: str, eval_mode: bool) -> bool:
    """R12: 지식 schema의 이력 한 건으로 넣는다(평가 모드는 하지 않음 — AC-09-C2).

    다음 진단 후보에 드는 것은 배포 서버 R12(받은 편지 → r12-update → 새 이력 벡터 DB version) 몫이다(EV-001 CH-32).
    """
    if eval_mode:
        return False
    c.execute(
        text(
            "insert into knowledge.history_records (record_id, equipment_id, symptom_group, occurred_at, narrative, "
            "sensor_snapshot, record, source, provenance) values (:id, :eq, :g, :at, :n, cast(:s as jsonb), "
            "cast(:r as jsonb), 'approved_report', :p)"
        ),
        {
            "id": rec["record_id"],
            "eq": rec["equipment_id"],
            "g": symptom_group,
            "at": rec["occurred_at"],
            "n": rec["narrative"],
            "s": json.dumps(rec["sensor_snapshot"]),
            "r": json.dumps(rec, ensure_ascii=False),
            "p": f"approved_report:{rec['source']['report_id']}",
        },
    )
    return True


def _to_inbox(c: Connection, rec: dict, approved_at: str, eval_mode: bool) -> None:
    """R12 받은 편지(EV-001 CH-23): 설정 폴더가 있으면 승인 commit 뒤 파일 하나 — 실패해도 승인 · 이력 그대로(감사)."""
    folder = config.r12_inbox()
    if folder is None:
        return
    target = f"report:{rec['source']['report_id']}"

    def deliver() -> None:
        try:
            doc = None if eval_mode else inbox.document(rec, approved_at)
            done = "eval_mode" if eval_mode else "missing_sensor" if doc is None else inbox.write(folder, doc)
        except Exception as e:  # noqa: BLE001 — 받은 편지 실패는 승인과 따로(PR-21)
            sessions.audit_rejection("system", "r12_inbox_failed", target, {"error": type(e).__name__})
            return
        if done != "written":
            sessions.audit_rejection("system", "r12_inbox_skipped", target, {"reason": done})
        else:
            runs.audit_names("inbox", target, redact.redact(rec["narrative"])[1])  # 거른 수만(001-63)

    c.info["after_commit"].append(deliver)


def _to_kg_inbox(c: Connection, rec: dict, approved_at: str, eval_mode: bool) -> None:
    """그래프 적재기 받은 편지함(D-543 · inbox-fit): C1A15_KG_INBOX가 있으면 승인 commit 뒤 파일 하나
    (일반 · 예외 같은 꼴) — 실패해도 승인 · 이력 그대로(감사) · 평가 모드 0.
    적재기 부르기는 c1a15-pack 꼴이 오면(D-546 덧 5)."""
    folder = config.kg_inbox()
    if folder is None:
        return
    target = f"report:{rec['source']['report_id']}"

    def deliver() -> None:
        if eval_mode:
            sessions.audit_rejection("system", "kg_inbox_skipped", target, {"reason": "eval_mode"})
            return
        try:
            doc, n = inbox.kg_document(rec, approved_at)
            done = inbox.write(folder, doc)
        except Exception as e:  # noqa: BLE001 — 받은 편지 실패는 승인과 따로
            sessions.audit_rejection("system", "kg_inbox_failed", target, {"error": type(e).__name__})
            return
        if done != "written":
            sessions.audit_rejection("system", "kg_inbox_skipped", target, {"reason": done})
        else:
            runs.audit_names("kg_inbox", target, n)  # 거른 수만(001-63)

    c.info["after_commit"].append(deliver)


@router.post(
    "/reports/{report_id}/decision", response_model=responses.DecisionResult, response_model_exclude_unset=True
)
def report_decision(
    report_id: int, body: DecisionBody, user: User = Depends(lead_user), c: Connection = Depends(conn, scope="function")
) -> dict:
    """L03 결정 저장: 그대로 승인 · 수정 후 승인(R8 — 실패면 거부) · 반려(사유 필수 — AC-24)."""
    r = _report(c, report_id)
    target = f"report:{report_id}"
    if r.status != "awaiting_approval":
        _deny(user, "not_awaiting", target)
    decision = body.decision
    memo = (body.memo or "").strip()
    now = datetime.now(UTC)
    if decision == "reject":
        if not memo:
            _deny(user, "reason_required", target)  # AC-24-C1
        rejection = {"reason": memo, "by": user.user_id, "at": now.isoformat()}
        c.execute(
            m.Report.__table__.update()
            .where(m.Report.report_id == report_id)
            .values(status="rejected", rejection=rejection, updated_at=now, change_seq=deps.next_change_seq(c))
        )
        c.execute(
            m.DiagSession.__table__.update()
            .where(m.DiagSession.session_id == r.session_id)
            .values(status="in_progress")
        )
        c.execute(
            m.Alarm.__table__.update()
            .where(m.Alarm.alarm_id == r.alarm_id)
            .values(status="in_progress", change_seq=deps.next_change_seq(c))
        )
        events = sessions.events_of(c, r.session_id)
        sessions._store(c, r.session_id, events[-1]["seq"] + 1, "report_rejected", rejection, user.user_id)
        sessions._open_step(
            c, r.session_id, sessions._row(c, r.session_id), sessions.events_of(c, r.session_id), user.user_id
        )
        sessions.audit(
            c, user.user_id, "report_reject", target, {"reason": memo, "session_id": r.session_id}, r.equipment_id
        )
        return {"status": "rejected"}
    edits = dict(body.edits) if decision == "edit" else {}
    fields = [dict(f) for f in r.fields]
    changes = []
    if decision == "edit":
        if not edits:
            _deny(user, "no_edits", target)
        chk = check_edits(c, r, edits)
        if not chk["ok"]:  # L03 / edit_failed: 수정 문장 검사 실패면 승인되지 않음(FLOW-07)
            sessions.audit_rejection(
                user.user_id, "reject", target, {"reason": "edit_ungrounded", "fields": chk["fields"]}
            )
            raise HTTPException(status_code=409, detail={"reason": "edit_ungrounded", **chk})
        for f in fields:
            if f["key"] in edits and edits[f["key"]] != f["text"]:
                changes.append({"field": f["key"], "label": f["label"], "before": f["text"], "after": edits[f["key"]]})
                f["text"], f["edited_by_lead"] = edits[f["key"]], True
    approval = {"mode": decision, "by": user.user_id, "at": now.isoformat(), "edits": changes, "memo": memo or None}
    rec = history_record(c, r, fields, approval)
    # D-619: 승인 예외는 KG 반영 뒤에만 사전 점검에 듦 — 승인 대기 폴더에 쓰기 전 revision을 적어 둠
    revision = runs.revision_now(fresh=True) if rec.get("exception_record") and not r.eval_mode else None
    added = _add_history(c, rec, r.symptom_group, r.eval_mode)
    _to_inbox(c, rec, approval["at"], r.eval_mode)
    _to_kg_inbox(c, rec, approval["at"], r.eval_mode)
    approval["history_record_id"] = rec["record_id"] if added else None
    c.execute(
        m.Report.__table__.update()
        .where(m.Report.report_id == report_id)
        .values(status="approved", fields=fields, approval=approval, updated_at=now, change_seq=deps.next_change_seq(c))
    )
    checkrecord.queue_after_decision(c, report_id)  # EV-006: 사람 체크 기록 한 장(C1A15_CHECKS=deploy만 · commit 뒤)
    sessions.audit(
        c,
        user.user_id,
        "report_approve" if decision == "approve" else "report_edit_approve",
        target,
        {
            "edits": changes,
            "memo": memo or None,
            "history_record_id": approval["history_record_id"],
            **({"deploy_revision": revision} if revision else {}),
        },
        r.equipment_id,
    )
    return {"status": "approved", "history_record_id": approval["history_record_id"]}


# ---------------------------------------------------------------- 처리할 호출(L04)


def _call(c: Connection, call_id: int):
    r = c.execute(
        select(
            m.Call.__table__,
            m.DiagSession.alarm_id,
            m.DiagSession.status.label("session_status"),
            m.Alarm.equipment_id,
            m.Alarm.symptom_group,
        )
        .join(m.DiagSession, m.DiagSession.session_id == m.Call.session_id)
        .join(m.Alarm, m.Alarm.alarm_id == m.DiagSession.alarm_id)
        .where(m.Call.call_id == call_id)
    ).first()
    if r is None:
        raise HTTPException(status_code=404, detail="no_call")
    return r


def _need(c: Connection, r) -> str:
    """L04 표 '필요한 작업': 파트장 호출 = 점검(절, 등급), 숙련자 인계 = 인계 사유, 정정 호출 = 정정 확인(019 ⑧)."""
    events = sessions.events_of(c, r.session_id)
    k = flow.knowledge_of(events)  # 예외 절(EX-…) 호출도 '이름(절, 등급)'
    if r.correction:
        cor = r.correction
        return f"정정 확인 · {cor.get('section')} {cor.get('title')}({cor.get('before')} → {cor.get('after')})"
    if r.kind == "approval" and r.section in k.sections:
        return f"{k.sections[r.section]['title']}({r.section}, {r.grade})"
    reason = next(
        (
            e["decision"].get("reason")
            for e in reversed(events)
            if e["type"] == "step_opened" and e["decision"].get("kind") == "handover"
        ),
        None,
    )
    return {
        "no_alternative": "점검 소진 · 원인 미상",
        "out_of_scope_finding": "후보 원인과 맞지 않는 소견",
        "restart_outside": "재가동 뒤에도 경계 밖",
        "llm_failure": "안내 문장 생성 반복 실패",
    }.get(reason or "", "숙련자 확인 필요")


@router.get("/calls", response_model=responses.LeadCalls)
def calls(user: User = Depends(lead_user), c: Connection = Depends(conn, scope="function")) -> dict:
    names = _names(c)
    rows = c.execute(text("select call_id from ops.calls where answer is null order by sent_at desc")).all()
    out = []
    for (cid,) in rows:
        r = _call(c, cid)
        out.append(
            {
                "call_id": r.call_id,
                "kind": r.kind,
                "equipment_id": r.equipment_id,
                "group_name": deps.group_names()[r.symptom_group],
                "sent_at": _iso(r.sent_at),
                "need": _need(c, r),
                "sender_name": names.get(r.sent_by, r.sent_by),
            }
        )
    return {"now": datetime.now(UTC).isoformat(), "rows": out}


# ---------------------------------------------------------------- 진행 중 건(EV-002, D-233 (3)) — 읽기만

STAGE_LABEL = {"before_lock": "잠금 전", "check": "점검", "action": "조치", "report": "보고서"}
STAGE_NO = {"before_lock": None, "check": 3, "action": 4, "report": 5}
ACTION_KINDS = {"action", "action_after_checklist", "restart_check", "restart_value"}
CALL_KINDS = {"call", "call_direct"}
HOLD_LABEL = {
    "lock": "잠금 확인 전",
    "call_unsent": "호출 보내기 전",
    "call_sent": "파트장 답 대기",
    "call_waiting": "호출하지 않고 대기",
    "handover_sent": "숙련자 인계 답 대기",
    "confirm_pending": "정비자 확인 대기",
}


def lead_stage(locked: bool, kind: str, actions_done: bool) -> str:
    """정비자 앱 진행 표시(apps/tech Session.tsx stepNumber)와 같은 규칙. '알람 · 진단'은 기기 상태라 잠금 전 하나."""
    if not locked:
        return "before_lock"
    if kind in {"report", "closed"}:
        return "report"
    if kind in ACTION_KINDS or kind == "monitor_close" or (kind in CALL_KINDS and actions_done):
        return "action"
    return "check"


def _last_at(events: list[dict], etype: str) -> str | None:
    return next((e["_at"] for e in reversed(events) if e["type"] == etype), None)


def _hold(c: Connection, row, locked: bool, step: dict, events: list[dict]) -> dict | None:
    """정지점 대기와 그 시작 시각(Change Contract v3 'since 출처')."""
    kind, status = step.get("kind"), step.get("call_status")
    if not locked:
        return {"kind": "lock", "since": _iso(row.created_at), "call_id": None}
    if kind in {"call", "handover"} | CALL_KINDS:
        if status == "sent":
            call = c.execute(
                select(m.Call.call_id, m.Call.sent_at)
                .where(m.Call.session_id == row.session_id, m.Call.answer.is_(None))
                .order_by(m.Call.sent_at.desc())
            ).first()
            hold = "handover_sent" if kind == "handover" else "call_sent"
            return {
                "kind": hold,
                "since": _iso(call.sent_at) if call else None,
                "call_id": call.call_id if call else None,
            }
        if status == "waiting":
            return {"kind": "call_waiting", "since": _last_at(events, "call_wait"), "call_id": None}
        return {"kind": "call_unsent", "since": _last_at(events, "step_opened"), "call_id": None}
    if kind in {"confirm_question", "out_of_scope"}:
        return {"kind": "confirm_pending", "since": _last_at(events, "result"), "call_id": None}
    return None


def _minutes(now: datetime, at: str | None) -> int | None:
    return max(0, int((now - datetime.fromisoformat(at)).total_seconds() // 60)) if at else None


@router.get("/sessions", response_model=responses.LeadSessions)
def in_progress_sessions(user: User = Depends(lead_user), c: Connection = Depends(conn, scope="function")) -> dict:
    """진행 중 건: 세션마다 지금 단계 · 절 · 정지점 대기 · 시각(운영 기록만, D-227). 판정 · 기록은 읽기만."""
    now = datetime.now(UTC)
    names = _names(c)
    grades = deps.grades(c)[0]
    sessions_rows = c.execute(
        select(m.DiagSession.__table__, m.Alarm.equipment_id, m.Alarm.occurred_at)
        .join(m.Alarm, m.Alarm.alarm_id == m.DiagSession.alarm_id)
        .where(m.DiagSession.status == "in_progress")
    ).all()
    rows = []
    for r in sessions_rows:
        events = sessions.events_of(c, r.session_id)
        k = flow.knowledge_of(events)  # 약속 v8 예외 절 이름까지
        state = session_engine.fold(k, r.symptom_group, sessions.similar_of(r), sessions.fold_inputs(events))
        step = sessions.current_step(events) or {}
        stage = lead_stage(state.locked, step.get("kind") or "", bool(state.actions_done))
        sec = step.get("section")
        hold = _hold(c, r, state.locked, step, events)
        if hold:
            hold = {**hold, "label": HOLD_LABEL[hold["kind"]], "minutes": _minutes(now, hold["since"])}
        started = _iso(r.created_at)
        rows.append(
            {
                "session_id": r.session_id,
                "alarm_id": r.alarm_id,
                "equipment_id": r.equipment_id,
                "group_code": r.symptom_group,
                "group_name": deps.group_names()[r.symptom_group],
                "technician_name": names.get(r.user_id, r.user_id),
                "stage": stage,
                "stage_label": STAGE_LABEL[stage],
                "step_no": STAGE_NO[stage],
                "step_kind": step.get("kind"),
                "section": sec,
                "section_title": k.sections[sec]["title"] if sec in k.sections else None,
                "grade": step.get("grade") or (grades.get(sec) if sec else None),
                **screen.check_progress(events, k, r.symptom_group, grades, state.approvals),
                "hold": hold,
                "alarm_at": _iso(r.occurred_at),
                "started_at": started,
                "last_input_at": events[-1]["_at"] if events else None,
                "elapsed_min": _minutes(now, started) or 0,
                "report_rejected": state.report_rejected_seq is not None and not state.report_confirmed,
            }
        )
    far = datetime.max.replace(tzinfo=UTC)

    def at(v: str | None) -> datetime:
        return datetime.fromisoformat(v) if v else far

    rows.sort(key=lambda x: (x["hold"] is None, at((x["hold"] or {}).get("since")), at(x["started_at"])))
    return {
        "now": now.isoformat(),
        "counts": {"in_progress": len(rows), "on_hold": sum(1 for x in rows if x["hold"])},
        "rows": rows,
    }


@router.get("/calls/{call_id}", response_model=responses.LeadCall)
def call_detail(call_id: int, user: User = Depends(lead_user), c: Connection = Depends(conn, scope="function")) -> dict:
    r = _call(c, call_id)
    opts = [o for o in CALL_OPTIONS[r.kind] if not (o[0] == "approved" and r.grade == "D")]  # AC-23-C3
    if r.correction:  # 결정 019 ⑧: 정정 호출의 '승인 후 진행' = 정정 때 하던 단계로 이어서(결정 012 ②)
        opts = [
            (v, lb, "정비자가 정정 때 하던 단계로 이어서 합니다" if v == "approved" else hint) for v, lb, hint in opts
        ]
    return {
        "call_id": r.call_id,
        "kind": r.kind,
        "equipment_id": r.equipment_id,
        "group_name": deps.group_names()[r.symptom_group],
        "sent_at": _iso(r.sent_at),
        "section": r.section,
        "grade": r.grade,
        "summary": r.summary,
        "sender_name": _names(c).get(r.sent_by, r.sent_by),  # D-528 호출 답하기 머리(보낸 사람)
        "options": [{"value": v, "label": lb, "hint": hint} for v, lb, hint in opts],
        "answered": r.answer is not None,
        "answer": r.answer,
        "need": _need(c, r),
        "corrections": [  # L04 / call_corrected: '보낸 뒤 정비자 정정' 상자(AC-47-C2 · 결정 019 ②)
            {k2: x.get(k2) for k2 in ("at", "section", "title", "before", "after", "reason")} for x in r.corrections
        ],
    }


@router.post("/calls/{call_id}/answer", response_model=responses.AnswerResult)
def call_answer(
    call_id: int, body: AnswerBody, user: User = Depends(lead_user), c: Connection = Depends(conn, scope="function")
) -> dict:
    """답 저장(1.4 · 결정 018 ③ ④): ops.calls.answer + 업무 기록 call_answer + 정비자 세션 다음 단계 다시 열기."""
    r = _call(c, call_id)
    target = f"call:{call_id}"
    if r.answer is not None:
        _deny(user, "already_answered", target)
    if r.session_status != "in_progress":
        _deny(user, "session_not_open", target)
    answer = body.answer
    allowed = {o[0] for o in CALL_OPTIONS[r.kind] if not (o[0] == "approved" and r.grade == "D")}
    if answer not in allowed:
        _deny(user, "answer_not_allowed", target, answer=answer, grade=r.grade)  # AC-23-C3: D에 승인 후 진행 없음
    note = (body.note or "").strip() or None
    payload = {
        "answer": answer,
        "section": r.section,
        "by": user.user_id,
        "at": datetime.now(UTC).isoformat(),
        "note": note,
        "call_id": call_id,
        **({"corrected": True} if r.correction else {}),  # 정정 호출의 답(결정 012 ② · 019 ①)
    }
    c.execute(
        text(
            "update ops.calls set answer = cast(:a as jsonb), answered_at = now(), change_seq = :q where call_id = :c"
        ),
        {"a": json.dumps(payload, ensure_ascii=False), "c": call_id, "q": deps.next_change_seq(c)},
    )
    events = sessions.events_of(c, r.session_id)
    sessions._store(c, r.session_id, events[-1]["seq"] + 1, "call_answer", payload, user.user_id)
    sessions._open_step(
        c, r.session_id, sessions._row(c, r.session_id), sessions.events_of(c, r.session_id), user.user_id
    )
    sessions.audit(
        c,
        user.user_id,
        "call_answer",
        target,
        {"answer": answer, "note": note, "session_id": r.session_id},
        r.equipment_id,
    )
    return {"status": "answered", "answer": answer}


# ---------------------------------------------------------------- 권한 등급(L05, R13)


@router.get("/grades", response_model=responses.LeadGrades)
def grades(user: User = Depends(lead_user), c: Connection = Depends(conn, scope="function")) -> dict:
    """결정 018 ②: 등급표에 있는 절 전부(절 번호 순), 변경 기록(누가 · 언제 · 전 · 후)."""
    k = deps.manual()
    rows = c.execute(select(m.GradeEntry.section_id, m.GradeEntry.grade)).all()
    key = lambda s: [int(x) for x in s.split(".")]  # noqa: E731
    names = _names(c)
    changes = c.execute(select(m.GradeChange.__table__).order_by(m.GradeChange.changed_at.desc())).all()
    return {
        "now": datetime.now(UTC).isoformat(),
        "total": len(rows),
        "rows": [
            {
                "section": s,
                "title": k.sections[s]["title"] if s in k.sections else s,
                "grade": g,
                "label": GRADE_LABEL[g],
            }
            for s, g in sorted(rows, key=lambda x: key(x[0]))
        ],
        "changes": [
            {
                "section": ch.section_id,
                "title": k.sections[ch.section_id]["title"] if ch.section_id in k.sections else ch.section_id,
                "before": ch.before,
                "after": ch.after,
                "by_name": names.get(ch.changed_by, ch.changed_by),
                "at": _iso(ch.changed_at),
            }
            for ch in changes
        ],
    }


RANK = {"A": 0, "B": 1, "C": 2, "D": 3}


def grade_warning(k, section: str, before: str, after: str) -> dict:
    """L05 낮춤 · 올림 경고(EV-062: 모든 등급 변경에 경고, D 포함)."""
    title = k.sections[section]["title"] if section in k.sections else section
    up = RANK[after] > RANK[before]
    if up:
        body = (
            "야간 정비자가 이 작업을 혼자 할 수 없게 되어, 진행 중 세션의 다음 판정부터 "
            "이 점검 대신 다른 점검이나 파트장 호출로 바뀝니다. 이미 열린 화면은 그대로입니다."
            if after in {"C", "D"}
            else "진행 중 세션의 다음 판정부터 시작 전 체크리스트를 거쳐야 합니다. 이미 열린 화면은 그대로입니다."
        )
    else:  # L05 / lower_warning 문구
        body = (
            "야간 정비자가 혼자 하게 되는 작업이 늘어납니다. 바꾸기 전에 해당 매뉴얼 절의 안전 조건을 확인하십시오. "
            "저장하면 진행 중 세션의 다음 지시 판정부터 적용됩니다."
        )
    return {
        "direction": "raise" if up else "lower",
        "head": f"등급을 {'올리려' if up else '낮추려'} 합니다 · {section} {title} {before} → {after}",
        "body": body,
    }


@router.get("/grades/{section}/warning", response_model=responses.GradeWarning)
def grade_warning_view(
    section: str, grade: str, user: User = Depends(lead_user), c: Connection = Depends(conn, scope="function")
) -> dict:
    cur = c.execute(select(m.GradeEntry.grade).where(m.GradeEntry.section_id == section)).scalar()
    if cur is None:
        raise HTTPException(status_code=404, detail="no_section")
    if grade not in RANK or grade == cur:
        raise HTTPException(status_code=422, detail="invalid_grade")
    return grade_warning(deps.manual(), section, cur, grade)


@router.post("/grades/{section}", response_model=responses.GradeResult)
def grade_change(
    section: str, body: GradeBody, user: User = Depends(lead_user), c: Connection = Depends(conn, scope="function")
) -> dict:
    """R13: 경고를 확인해야 저장(AC-25-C3), 저장 즉시 다음 판정부터, 기록 · 감사.

    이미 열린 지시는 그대로다(step_opened 기록 — R13).
    """
    target = f"grade:{section}"
    cur = c.execute(select(m.GradeEntry.grade).where(m.GradeEntry.section_id == section)).scalar()
    if cur is None:
        raise HTTPException(status_code=404, detail="no_section")
    new = body.grade
    if new not in RANK or new == cur:
        raise HTTPException(status_code=422, detail="invalid_grade")
    if body.confirmed is not True:
        _deny(user, "warning_not_confirmed", target)
    c.execute(
        m.GradeEntry.__table__.update()
        .where(m.GradeEntry.section_id == section)
        .values(grade=new, updated_by=user.user_id, updated_at=datetime.now(UTC))
    )
    c.execute(
        m.GradeChange.__table__.insert().values(section_id=section, before=cur, after=new, changed_by=user.user_id)
    )
    sessions.audit(c, user.user_id, "grade_change", target, {"section": section, "before": cur, "after": new})
    return {"section": section, "before": cur, "after": new}


# ---------------------------------------------------------------- 감사 기록(L06)

APP_LABEL = {"tech": "정비자", "lead": "파트장"}
FIELD_LABEL = {k: lb for k, lb, _ in flow.REPORT_FIELDS}


NOTE_TARGET = {  # 메모 덧붙이기 대상 기록 이름(AC-48-C1)
    "lock": "잠금 완료",
    "b_checklist_done": "시작 전 체크리스트",
    "confirm_answer": "확인 질문 답",
    "out_of_scope_choice": "맞지 않는 소견 선택",
    "action_done": "조치 완료",
    "restart": "재가동",
    "restart_result": "재가동 확인",
    "monitor_close": "감시 마감",
    "call_sent": "호출",
    "call_wait": "호출하지 않고 대기",
    "handover_request": "숙련자 인계 요청",
}


# D-529: ML 길 알람 없음 사유(alarm_rejected) — 알람 발생기 줄과 같은 말
NO_ALARM_REASON = {
    "low_confidence": "유형 확신 낮음",
    "rnf": "유형 RNF",
    "model_failure": "예측 실패",
    "ml_type_alarm_unsupported": "ML 유형 · 경계 표시 없음",
}


def audit_label(a, k, names: dict[str, str] | None = None) -> str:
    d = a.detail or {}
    sec = d.get("section")
    if sec == "EX-NEW":  # 약속 v9 예외 진행 절 — 화면 글은 '예외'(코드 이름 0 · zzz51 답 4)
        sec = "예외"
    title = k.sections[sec]["title"] if sec in k.sections else ""
    act = a.action
    if act == "correction":  # L06 / correction: 정정 · 절 · 전 → 후 · 사유(AC-45-C3)
        before, after = d.get("before") or {}, d.get("after") or {}
        if d.get("target_type") == "precheck_done":
            what = f"측정값 {before.get('values') or ''} → {after.get('values') or ''}"
        elif before.get("value") != after.get("value"):
            what = f"결과 {before.get('value')} → {after.get('value')}"
        else:
            what = f"메모 '{before.get('memo') or ''}' → '{after.get('memo') or ''}'"
        return f"정정 · {sec or ''} {what} · 사유 '{d.get('reason')}'".replace("  ", " ")
    if act == "note_append":  # L06 / correction: 메모 덧붙이기 · 대상 기록
        return f"메모 덧붙이기 · {NOTE_TARGET.get(d.get('target_type'), d.get('target_type'))} {sec or ''}".strip()
    if act == "instruct":
        return f"지시 · {sec} {title}".strip()
    if act == "call_required":
        return f"호출 필요 · {sec} {title}".strip()
    if act == "lock":
        return "잠금 완료 · 1.2"
    if act == "precheck_done":
        return "사전 확인 완료"
    if act == "input":
        return f"결과 입력 · {sec or ''} {d.get('value') or '메모'}".strip()
    if act == "confirm_answer":
        return f"확인 질문 답 · {'네' if d.get('yes') else '아니요'}"
    if act == "out_of_scope_choice":
        return f"맞지 않는 소견 · {'숙련자 인계' if d.get('choice') == 'handover' else '계속'}"
    if act == "b_checklist_done":
        return f"시작 전 체크리스트 · {sec}"
    if act == "action_done":
        return f"조치 완료 · {sec}"
    if act == "restart":
        return "재가동 · 6.1"
    if act == "restart_result":
        return f"재가동 확인 · {'경계 이내' if d.get('outcome') == 'within' else '여전히 밖'}"
    if act == "monitor_close":
        return "이상 없음 · 감시 마감 · 6.2"
    if act == "call_wait":
        return "호출하지 않고 대기"
    if act == "call_send":
        return "숙련자 인계" if d.get("kind") == "handover" else f"파트장 호출 · {sec or ''}".strip(" ·")
    if act == "handover_request":
        return "숙련자 인계 요청"
    if act == "report_edit":
        return f"보고서 수정 · {FIELD_LABEL.get(d.get('field'), d.get('field'))}"
    if act == "report_confirm":
        return "보고서 확정"
    if act == "report_approve":
        return "보고서 승인"
    if act == "report_edit_approve":
        return "보고서 수정 후 승인"
    if act == "report_reject":
        return "보고서 반려"
    if act == "call_answer":
        labels = {v: lb for opts in CALL_OPTIONS.values() for v, lb, _ in opts}
        return f"호출 답 · {labels.get(d.get('answer'), d.get('answer'))}"
    if act == "grade_change":
        return f"등급 변경 · {sec} {title} {d.get('before')} → {d.get('after')}".replace("  ", " ")
    if act == "session_start":
        return "점검 시작"
    if act == "alarm_ack":
        return "알람 확인"  # EV-007
    if act == "alarm":
        who = (names or {}).get(d.get("assigned_to") or "", d.get("assigned_to") or "없음")
        return f"알람 등록 · 배정 {who}"
    if act == "reject":
        return f"요청 거부 · {d.get('reason')}"
    if act == "alarm_rejected":
        return f"알람 없음 · {NO_ALARM_REASON.get(d.get('reason'), '그 밖 사유')}"
    if act == "alarm_candidate":  # D-529 전 옛 행(사람 판단 지움 — 새로 생기지 않음)
        return f"후보 기록 · {NO_ALARM_REASON.get(d.get('reason'), '그 밖 사유')}"
    if act == "candidate_promote":
        return "후보 올림"
    if act == "candidate_dismiss":
        return "후보 버림"
    return act


HIDDEN = {"login", "logout"}  # L06 예시 행에 없는 접속 기록은 표에서 뺀다(AC-26-C1 목록 밖)


@router.get("/audit", response_model=responses.LeadAudit)
def audit(
    equipment: str | None = None,
    user_id: str | None = None,
    date: str | None = None,
    user: User = Depends(lead_user),
    c: Connection = Depends(conn, scope="function"),
) -> dict:
    """L06: 시간 역순 · 설비 · 사용자 · 기간(하루, 현장 시각) 필터(AC-26-C2)."""
    k = deps.manual()
    accts = {a.user_id: a for a in c.execute(select(m.Account.user_id, m.Account.display_name, m.Account.role)).all()}
    sess_eq = dict(
        c.execute(
            select(m.DiagSession.session_id, m.Alarm.equipment_id).join(
                m.Alarm, m.Alarm.alarm_id == m.DiagSession.alarm_id
            )
        ).all()
    )
    rep_of_session = {sid: rid for rid, sid in c.execute(select(m.Report.report_id, m.Report.session_id)).all()}
    rep_eq = dict(
        c.execute(
            select(m.Report.report_id, m.Alarm.equipment_id)
            .join(m.DiagSession, m.DiagSession.session_id == m.Report.session_id)
            .join(m.Alarm, m.Alarm.alarm_id == m.DiagSession.alarm_id)
        ).all()
    )
    call_eq = dict(
        c.execute(
            select(m.Call.call_id, m.Alarm.equipment_id)
            .join(m.DiagSession, m.DiagSession.session_id == m.Call.session_id)
            .join(m.Alarm, m.Alarm.alarm_id == m.DiagSession.alarm_id)
        ).all()
    )
    day = (
        datetime.fromisoformat(date).replace(tzinfo=screen.KST)
        if date
        else datetime.now(screen.KST).replace(hour=0, minute=0, second=0, microsecond=0)
    )
    q = (
        select(m.AuditEntry.__table__)
        .where(m.AuditEntry.at >= day, m.AuditEntry.at < day + timedelta(days=1))
        .order_by(m.AuditEntry.at.desc(), m.AuditEntry.audit_id.desc())
    )
    rows = []
    for a in c.execute(q).all():
        if a.action in HIDDEN:
            continue
        kind, _, ident = a.target.partition(":")
        eq = a.equipment_id
        if kind == "session" and ident.isdigit():
            eq = eq or sess_eq.get(int(ident))
            tlabel = f"{eq} 세션" if eq else a.target
        elif kind == "report" and ident.isdigit():
            eq = eq or rep_eq.get(int(ident))
            tlabel = f"{eq} 보고서" if eq else a.target
        elif kind == "call" and ident.isdigit():
            eq = eq or call_eq.get(int(ident))
            tlabel = f"{eq} 호출" if eq else a.target
        elif kind == "grade":
            tlabel = "권한 등급"
        elif kind == "alarm":
            tlabel = f"{eq} 알람" if eq else a.target
        elif kind in ("event", "candidate"):  # D-529: 알람 없음 · 옛 후보 행(event · 후보 번호는 보이지 않음)
            tlabel = f"{eq or ''} {'알람 판정' if kind == 'event' else '후보'}".strip()
        else:
            tlabel = a.target
        if a.action == "session_start" and a.detail and a.detail.get("alarm_id"):
            tlabel = f"{eq} 세션" if eq else tlabel
        report_id = None  # D-528 오늘 기록 「보고서 보기」: 보고서 확정(세션 기록) · 파트장 결정(보고서) 행
        if kind == "report" and ident.isdigit():
            report_id = int(ident)
        elif a.action == "report_confirm" and kind == "session" and ident.isdigit():
            report_id = rep_of_session.get(int(ident))
        system = a.action in {"instruct", "call_required"} or a.actor == "system"
        acct = accts.get(a.actor)
        row = {
            "audit_id": a.audit_id,
            "at": a.at.isoformat(),
            "user_id": None if system else a.actor,
            "user_name": "시스템" if system else (acct.display_name if acct else a.actor),
            "app": "system" if system else APP_LABEL.get(acct.role if acct else "", "system"),
            "action": audit_label(a, k, {u: x.display_name for u, x in accts.items()}),
            "target": tlabel,
            "equipment_id": eq,
            "report_id": report_id,
        }
        if equipment and row["equipment_id"] != equipment:
            continue
        if user_id and row["user_id"] != user_id:
            continue
        rows.append(row)
    return {
        "now": datetime.now(UTC).isoformat(),
        "date": day.date().isoformat(),
        "filters": {"equipment": equipment, "user_id": user_id},
        "options": {
            "equipment": sorted({x for x in sess_eq.values() if x}),
            "users": [{"user_id": u, "name": a.display_name} for u, a in sorted(accts.items())],
        },
        "rows": rows,
    }
