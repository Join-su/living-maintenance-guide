"""진단 세션 서비스: append-only 업무 기록(정본) → LangGraph 재개(다음 단계) · engine.session 접기(화면 상태).

화면 값은 서버 판정만(Authority boundary).
"""

import json
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import Connection, func, select, text

from c1a15.api import deps, flow, screen
from c1a15.db import models as m
from c1a15.deploy import runs
from c1a15.deploy.client import PackUnavailable
from c1a15.engine import exception, rules, session, switch
from c1a15.llm import adapter_for_mode, grounding
from c1a15.llm.base import AdapterError, Facts

SERVER_FIELDS = {
    "seq",
    "type",
    "signal",
    "llm_fallback",
    "at",
    "void",
    "_at",
    "_client",
    "_client_type",
}  # 서버만 쓰는 event 필드(재전송 비교에서 뺌)
CLIENT_TYPES = {
    "alarm_ack",  # EV-007(D-422): 알람 화면 '진단 보기' — 잠금 앞 차례(판정 Run 부르지 않음)
    "lock",
    "equipment_change",
    "precheck_done",
    "b_checklist_done",
    "result",
    "confirm_answer",
    "out_of_scope_choice",
    "switch_check",  # 약속 v9 1절 [점검 바꾸기] 후보 고름{section, from_section, choice} — judge 사건
    "exception_start",  # 약속 v9 1절 [예외]{mode call_first | act_now} — judge 사건(뒤 사건 절 EX-NEW)
} | flow.CLIENT_TYPES
INPUT_TYPES = (
    CLIENT_TYPES - flow.CLIENT_TYPES - {"alarm_ack"}  # 알람 확인은 판정 입력이 아님
    | {  # 판정 엔진이 접는 업무 기록(S-03 · 파트장 답 포함)
        "lock",
        "equipment_change",
        "precheck_done",
        "approval",
        "correction",  # S-05(P-14): 조치 뒤 정정 → 정정 호출(AC-47-C1)
    }
    | flow.ENGINE_TYPES
)
GROUP_NAME = {"TW": "공구 마모 경고", "PW": "출력 이탈", "CL": "온도 차 저하", "LD": "토크 과부하"}


def audit(c: Connection, actor: str, action: str, target: str, detail: dict, equipment_id: str | None = None) -> None:
    c.execute(
        m.AuditEntry.__table__.insert().values(
            actor=actor, action=action, target=target, detail=detail, equipment_id=equipment_id
        )
    )


def audit_rejection(actor: str, action: str, target: str, detail: dict, equipment_id: str | None = None) -> None:
    """거부 감사는 요청 트랜잭션과 따로 커밋한다 — 거부 응답(HTTPException)으로 요청이 rollback돼도 남는다(S02-G6-1)."""
    with deps.engine().begin() as c2:
        audit(c2, actor, action, target, detail, equipment_id)


def raw_events_of(c: Connection, sid: int) -> list[dict]:
    """append-only 업무 기록 그대로(정정 · 메모 덧붙임도 각자 한 행)."""
    rows = c.execute(
        select(m.SessionEvent.seq, m.SessionEvent.type, m.SessionEvent.payload, m.SessionEvent.created_at)
        .where(m.SessionEvent.session_id == sid)
        .order_by(m.SessionEvent.seq)
    ).all()
    return [{"seq": r.seq, "type": r.type, **r.payload, "_at": r.created_at.isoformat()} for r in rows]


NO_REJUDGE = {"call_with_correction", "attach_to_call", "attach_answered"}  # 다시 판정하지 않는 정정(AC-47 · 019 ② ③)


def logical(raw: list[dict]) -> list[dict]:
    """정정 · 메모 덧붙임을 적용한 업무 기록(S-05 P-14, FLOW-14 '다시 읽기').

    - correction: 대상 기록 값을 최신 값으로 바꾸고 corrections(시각 · 전 · 후 · 사유)와
      corrected_from(처음 값 · 마지막 시각 · 횟수)을 남긴다. 원래 값은 업무 기록 행과 감사에 그대로 있다.
      mode rejudge면 대상 뒤 · 정정 앞의 기록을 '정정으로 무효'(void)로 둔다.
    - note_append: 대상 기록에 notes(시각 · 메모)를 붙인다(판정 · 보고서 값은 그대로 — 결정 019 ⑥).
    판정 엔진 · 화면 · 보고서 · 감사 · 이력 변환은 모두 이 결과를 읽는다.
    """
    out = [dict(e) for e in raw]
    by_seq = {e["seq"]: e for e in out}
    for e in out:
        target = by_seq.get(e.get("target_seq"))
        if target is None:
            continue
        if e["type"] == "correction":
            history = target.setdefault("corrections", [])
            history.append(
                {
                    "seq": e["seq"],
                    "at": e["_at"],
                    "by": e.get("by"),
                    "reason": e.get("reason"),
                    "before": e.get("before"),
                    "after": e.get("after"),
                    "mode": e.get("mode"),
                }
            )
            target.update(e.get("after") or {})
            target.pop("signal", None)  # 정정한 결과는 R7 확인 질문을 다시 띄우지 않는다
            first = history[0]["before"] or {}
            target["corrected_from"] = {**first, "at": e["_at"], "count": len(history)}
            if (
                target["type"] == "result" and e.get("mode") in NO_REJUDGE
            ):  # AC-47: 화면 · 보고서는 최신 값, 판정은 그대로
                target.setdefault("judged_value", (e.get("before") or {}).get("value"))
            if e.get("mode") == "rejudge":
                target.pop("judged_value", None)
                for later in out:
                    if e["target_seq"] < later["seq"] < e["seq"] and not later.get("void"):
                        later["void"], later["voided_by"] = True, e["seq"]
        elif e["type"] == "note_append":
            target.setdefault("notes", []).append({"seq": e["seq"], "at": e["_at"], "memo": e.get("memo")})
    return out


def events_of(c: Connection, sid: int) -> list[dict]:
    return logical(raw_events_of(c, sid))


def fold_inputs(events: list[dict]) -> list[dict]:
    """앱 안 접기(화면 · 검사용 상태)에 넣는 업무 기록 — 예외 절 사건은 뺌(engine 사본은 예외 절을 모름, 약속 v8).
    Pack judge 요청 입력은 예외 절 사건까지 그대로."""
    ins = [e for e in events if e["type"] in INPUT_TYPES]
    eg = flow.exception_guide_of(events)
    if flow.walks(
        eg
    ):  # 약속 v10 걸음 길(Pack 3502e29): 안내 뒤 첫 재가동 · 재가동 값은 예외 몫 — 원래 순서 접기에서 뺌
        return exception.base_events(ins, flow.guide_ex(eg))
    return exception.without(ins)


def similar_of(row: m.DiagSession) -> list[rules.Similar]:
    return [rules.Similar(s["record_id"], s["cause"], s["score"]) for s in row.similar]


def _store(c: Connection, sid: int, seq: int, etype: str, payload: dict, actor: str) -> None:
    c.execute(
        m.SessionEvent.__table__.insert().values(
            session_id=sid, seq=seq, type=etype, payload=payload, actor=actor, change_seq=deps.next_change_seq(c)
        )
    )


def _deploy_decision(
    c: Connection,
    row: m.DiagSession,
    inputs: list[dict],
    manual_support: dict | None = None,
    eg: dict | None = None,
) -> tuple[dict, dict]:
    """다음 단계 = 배포 서버 judge Run(EV-001 ES-07, C1A15_JUDGE=deploy). 판정 하나 = Run 하나 — checkpoint 안 씀."""
    g, minutes = deps.grades(c)
    rel, g, minutes = flow.with_exception(eg, g, minutes)
    res = runs.judge(
        row.alarm_id,
        row.symptom_group,
        row.similar,
        inputs,
        g,
        minutes,
        manual_support,
        exception_relation=rel,
        context=runs.alarm_context(alarm_of(c, row.alarm_id)),
    )
    return res.output["decision"], {"via": "deploy", "run_id": res.run_id, "deployment_id": res.deployment_id}


def _open_step(
    c: Connection, sid: int, row: m.DiagSession, events: list[dict], actor: str, decided: tuple | None = None
) -> dict:
    """다음 단계를 지금 등급표로 판정해 step_opened로 남긴다(R13: 열린 단계는 이후 등급 변경에도 그대로).

    판정 길 = 배포 서버 judge Run(deploy만 — 앱 안 LangGraph 갈래는 ES-23에서 지움).
    decided = 세션 시작 start Run이 낸 첫 단계.
    """
    inputs = [e for e in events if e["type"] in INPUT_TYPES]
    ms = next((e.get("manual_support") for e in events if e["type"] == "step_opened"), None)  # start가 준 매뉴얼 몫
    eg = flow.exception_guide_of(events)  # 약속 v8: start가 준 예외 안내
    decision, ref = decided if decided is not None else _deploy_decision(c, row, inputs, ms, eg)
    seq = (events[-1]["seq"] if events else 0) + 1
    _store(c, sid, seq, "step_opened", {"decision": decision, **ref}, actor)
    if decision["kind"] in {
        "precheck",
        "instruct",
        "instruct_after_checklist",
        "call",
        "action",
        "action_after_checklist",
    } and not decision.get(
        "call_status"
    ):  # 결정 020 ④: 보냄 · 대기 뒤 다시 열린 같은 호출 단계는 '호출 필요'를 다시 남기지 않음
        audit(
            c,
            actor,
            "instruct" if decision["kind"].startswith(("instruct", "action", "precheck")) else "call_required",
            f"session:{sid}",
            {"section": decision.get("section"), "grade": decision.get("grade"), "kind": decision["kind"]},
        )
    if (
        decision["kind"] in INSTRUCTION_KINDS and decision.get("section") != switch.NEW and not decision.get("step")
    ):  # R8 '지시문': 열 때 한 번 쓰고 근거 검사 결과를 남긴다(주기 조회마다 다시 쓰지 않음) · v9 EX-NEW는 절차 글 없음
        # · 약속 v10 걸음(c1 · a1 …)은 기록에 적힌 글 그대로(지시문 생성 0)
        guide = screen.instruction_guide(flow.knowledge_of(events_of(c, sid)), decision["section"], row.similar)
        payload = {k2: guide[k2] for k2 in ("section", "steps", "label", "original")} | {
            "llm_fallback": guide["fallback"]
        }
        _store(c, sid, seq + 1, "instruction_shown", payload, actor)
    if decision["kind"] == "report":  # CH-20: 보고서 원인 · 조치 문장은 초안 열쇠마다 한 번(화면 조회는 쓰지 않음)
        evs = events_of(c, sid)
        state = session.fold(flow.knowledge_of(evs), row.symptom_group, similar_of(row), fold_inputs(evs))
        written = flow.report_written_payload(c, row, state, decision, evs, seq)
        if written is not None:
            _store(c, sid, seq + 1, "report_written", written, actor)
    return decision


INSTRUCTION_KINDS = {"precheck", "instruct", "instruct_after_checklist", "action", "action_after_checklist"}


def instructed_sections(events: list[dict]) -> set[str]:
    """이 세션에서 지시로 열린 절(지금 · 이전). 호출(C · D) 단계의 절은 들지 않는다."""
    return {
        e["decision"]["section"]
        for e in events
        if e["type"] == "step_opened" and e["decision"].get("kind") in INSTRUCTION_KINDS
    }


def current_step(events: list[dict]) -> dict | None:
    opened = [e for e in events if e["type"] == "step_opened"]
    return opened[-1]["decision"] if opened else None


def _check_known_records(c: Connection, alarm: m.Alarm, similar: list[dict], run_id: str) -> None:
    """PR-24: Pack이 고른 기록이 앱 이력에 모두 있어야 진단 카드 · 기록 전체 화면이 깨지지 않음.

    없으면 세션을 만들지 않음(503 pack_unavailable · code unknown_record).
    """
    ids = {s["record_id"] for s in similar}
    known = (
        set(c.execute(select(m.HistoryRecord.record_id).where(m.HistoryRecord.record_id.in_(ids))).scalars())
        if ids
        else set()
    )
    missing = sorted(ids - known)
    if missing:
        audit_rejection(
            "system",
            "pack_call_failed",
            f"alarm:{alarm.alarm_id}",
            {"kind": "start", "code": "unknown_record", "run_id": run_id, "missing": missing},
            alarm.equipment_id,
        )
        raise PackUnavailable("unknown_record", run_id)


def create(c: Connection, alarm: m.Alarm, user_id: str) -> int:
    # 같은 알람의 동시 시작은 알람 줄 잠금으로 줄 세움 — 뒤 요청은 앞 요청 commit 뒤 기존 세션을 받음(GAP-18)
    c.execute(select(m.Alarm.alarm_id).where(m.Alarm.alarm_id == alarm.alarm_id).with_for_update())
    existing = c.execute(select(m.DiagSession.session_id).where(m.DiagSession.alarm_id == alarm.alarm_id)).scalar()
    if existing:
        return existing
    # 첫 Run = start — 상위 10은 Pack 리트리버(약속 v4, EV-001 CH-29 · CH-31) · 실패면 세션을 만들지 않음
    g, minutes = deps.grades(c)
    recent = flow.recent_work_of(flow.recent_maintenance(c, alarm, days=flow.RECENT_WORK_DAYS))  # D-570 최근 정비 조건
    res = runs.start(alarm, g, minutes, recent)
    similar = res.output["similar"]
    _check_known_records(c, alarm, similar, res.run_id)
    deploy_cols = {"retrieval": res.output["retrieval"], "start_run_id": res.run_id, "deployment_id": res.deployment_id}
    ref = {"via": "deploy", "run_id": res.run_id, "deployment_id": res.deployment_id}
    if res.output.get("manual_support") is not None:  # 약속 v5(EV-011 CH-07) — judge 요청마다 다시 담음
        ref["manual_support"] = res.output["manual_support"]
    if res.output.get("exception_guide") is not None:  # 약속 v8(D-545 덧 2) — relation_id를 judge 요청마다 다시 담음
        ref["exception_guide"] = res.output["exception_guide"]
    decided = (res.output["decision"], ref)
    pack_preview = res.output.get("preview")  # 약속 v5 덧칸(EV-011 CH-03) — 없으면 앱 decide
    sid = c.execute(
        m.DiagSession.__table__.insert()
        .values(
            alarm_id=alarm.alarm_id, user_id=user_id, symptom_group=alarm.symptom_group, similar=similar, **deploy_cols
        )
        .returning(m.DiagSession.session_id)
    ).scalar_one()
    c.execute(
        m.Alarm.__table__.update()
        .where(m.Alarm.alarm_id == alarm.alarm_id)
        .values(status="in_progress", change_seq=deps.next_change_seq(c))
    )
    row = _row(c, sid)
    _open_step(c, sid, row, [], user_id, decided)
    pv = preview(row, c, pack_preview)  # 처음 보이는 진단 미리보기 문장의 대체 여부를 세션 실패 수에 남긴다(S02-G6-4)
    k = deps.manual()
    state = session.fold(k, row.symptom_group, similar_of(row), [])
    order = [x.code for x in rules.ordered_causes(k, row.symptom_group)]
    probs, evidence = screen.decided_numbers(decided[0], order) or (  # EV-011 CH-04: 그때 순위 = Pack start 값
        session.probabilities(state),
        rules.evidence_counts(state.candidates, state.similar),
    )
    snap = {  # T15 / diagnosis '그때의 원인 순위 · 첫 지시'(S-05 P-14)
        "kind": pv["kind"],
        "preview": pv,
        "probabilities": probs,
        "evidence": evidence,
        **(screen.decided_counts(decided[0], order) or {}),  # 리허설 2 R3 · R6: 그때 순위 줄 n건 중 k건
    }
    if pack_preview is not None:
        snap["decision"] = pack_preview
    if "reason_fallback" in pv:
        snap["llm_fallback"] = pv["reason_fallback"]
    _store(c, sid, events_of(c, sid)[-1]["seq"] + 1, "preview_shown", snap, user_id)
    audit(c, user_id, "session_start", f"session:{sid}", {"alarm_id": alarm.alarm_id}, alarm.equipment_id)
    return sid


def _row(c: Connection, sid: int) -> m.DiagSession:
    r = c.execute(select(m.DiagSession.__table__).where(m.DiagSession.session_id == sid)).first()
    if r is None:
        raise HTTPException(status_code=404, detail="no_session")
    return m.DiagSession(**r._mapping)


def checklist_for(c: Connection, section: str) -> list[str]:
    """B 시작 전 체크리스트: 그 절의 목록, 절에 없으면 공통 시작 전 확인 목록(AC-43-C2)."""
    k = deps.manual()
    own = []
    for cs in k.checks.values():
        for chk in cs:
            if chk.section_id == section:
                own = list(chk.checklist)
    return own or [i for (i,) in c.execute(select(m.CommonPrecheckItem.text).order_by(m.CommonPrecheckItem.seq)).all()]


def checklist_is_common(section: str) -> bool:
    """그 절에 시작 전 체크리스트가 없어 공통 시작 전 확인을 쓰는가(T06 / b_precheck '공통 시작 전 확인' 칩)."""
    return not any(chk.checklist for cs in deps.manual().checks.values() for chk in cs if chk.section_id == section)


def session_for(c: Connection, sid: int, user_id: str) -> m.DiagSession:
    row = _row(c, sid)
    if row.user_id != user_id:
        raise HTTPException(status_code=403, detail="not_your_session")
    return row


def _first_decision(row: m.DiagSession, c: Connection, k, g, minutes, decided: dict | None = None) -> dict:
    """잠금 · 사전 확인을 했다고 놓은 판정 = Pack start preview(저장값) — 없으면 앱 decide."""
    if decided is None:
        shown = (e for e in events_of(c, row.session_id) if e["type"] == "preview_shown" and "decision" in e)
        decided = next(shown, {}).get("decision")
    if decided is not None:
        return decided
    state = session.fold(k, row.symptom_group, similar_of(row), [{"type": "lock"}, {"type": "precheck_done"}])
    return session.decide(state, k, g, minutes)


def preview(row: m.DiagSession, c: Connection, decided: dict | None = None) -> dict:
    """진단 화면(T04)의 '다음 점검' 미리보기: 잠금 · 사전 확인을 했다고 놓고 판정한 다음 단계.

    판정 = Pack start 결과 preview(EV-011 CH-03 · 세션 시작 때 preview_shown에 저장), 없으면 앱 decide.
    절차 · 판정 기준은 넣지 않는다(R1).
    """
    k = flow.knowledge_of(events_of(c, row.session_id))
    g, minutes = deps.grades(c)
    d = _first_decision(row, c, k, g, minutes, decided)
    out = {"kind": d["kind"], "section": d.get("section"), "grade": d.get("grade"), "cause": d.get("cause")}
    # 분: 분 표 → 없으면 그 절의 분(예외 절 = exception_guide.minutes · knowledge_of) → 없으면 None(덧 33)
    mins = minutes.get(d.get("section"), (k.sections.get(d.get("section")) or {}).get("minutes"))
    if d["kind"] in {"instruct", "instruct_after_checklist"}:
        sec = k.sections[d["section"]]
        order = [x.code for x in rules.ordered_causes(k, row.symptom_group)]
        probs = screen.in_order(
            d["probabilities"], order
        )  # 순위 = 진단 화면 원인 순위와 같은 정렬(확률 내림차순 · 같으면 앞 순서)
        rank = sorted(probs, key=lambda c: -probs[c]).index(d["cause"]) + 1 if d["cause"] in probs else 1
        facts = Facts(
            "reason",
            d["section"],
            {
                "rank": rank,
                "cause_name": k.cause(d["cause"]).name,
                "probability": d["reason_facts"]["probability"],
                "minutes": d["reason_facts"]["minutes"],
                "grade": d["grade"],
                "alternatives": d["reason_facts"]["alternatives"],
                **({"picked_by": pb} if (pb := picked_by_of(d)) else {}),  # D-652 12-f
            },
        )
        out.update(title=sec["title"], minutes=mins, **_checked_reason(row, d, facts))
        if pb:
            out["picked_by"] = pb
    elif d["kind"] == "call":  # P-18: 첫 판정이 호출(T04 / first_call)이어도 카드 값은 서버가 낸다
        sec = k.sections[d["section"]]
        p = d["probabilities"].get(d.get("cause"), 0)
        eg = flow.exception_guide_of(events_of(c, row.session_id)) or {}
        here = eg.get("section") == d["section"]
        topic = (eg.get("topic") or eg.get("title")) if here else None
        name = (flow.first_what(eg) if here else None) or sec["title"]  # 정답지 11쪽: 칩 · 이유 = 첫 걸음 점검 이름
        cause_name = (  # 예외 관계에 원인이 없을 수 있음(Pack v8) · 고침A 11쪽 이름 '예외상황-{주제}'
            k.cause(d["cause"]).name if d.get("cause") else f"예외상황-{topic}" if topic else "예외 기록"
        )
        facts = Facts(
            "call_reason",
            d["section"],
            {
                "cause_name": cause_name,
                "probability": p,
                "check_title": name,
                "grade": d["grade"],
                "reason": d["reason"],
                **({"topic": topic} if topic else {}),
            },
        )
        ex_ids = (
            {*(eg.get("basis") or []), d["section"][len(exception.PREFIX) :]}
            if eg.get("section") == d["section"]
            else set()
        )
        g = grounding.Grounding(
            allowed_sections=frozenset([d["section"]]),
            source_numbers=frozenset([str(p), "1"]) | grounding.numbers_in(name),  # '1순위'의 1은 순위 사실
            top_record_ids=frozenset(s["record_id"] for s in row.similar)
            | ex_ids,  # 예외 절 = 근거 예외 기록(EX-<기록>)
        )
        who = f"{cause_name} {p}%" if d.get("cause") else cause_name
        where = "" if topic else f"{flow.sec_text(d['section'])} "  # 예외 이름이 있으면 기록 번호 0(고침A 11쪽)
        template = f"{who} · {where}{name} · {d['grade']}등급 · 파트장 호출 필요"
        r = grounding.checked(lambda: adapter_for_mode().write(facts), g, template)
        out.update(
            title=name,
            minutes=mins,
            call_reason=d["reason"],
            reason=r.text,
            reason_fallback=r.fallback,
            reason_label=r.label,
        )
    # D-585: 진단 카드 [점검 바꾸기] 창 후보(모든 알람 같게) · 고른 점검(잠금 · 사전 확인 뒤 Pack이 엶)
    ev = events_of(c, row.session_id)
    out["candidates"] = _candidate_rows(k, d, ev, row.symptom_group)
    sw = [e for e in ev if e["type"] == DIAG_PICK]
    hit = next((x for x in out["candidates"] if sw and x["section"] == sw[-1]["section"]), None)
    if hit:
        out["picked"] = {key: hit[key] for key in ("section", "title", "grade")}
    return out


PICKED_BY_KEYS = ("option", "section", "title", "instead_of", "instead_title")


def picked_by_of(d: dict) -> dict | None:
    """D-652 12-f Pack reason_facts.picked_by — 옵션 check_first_<증상군>이 첫 점검을 바꾼 판정만.
    다섯 칸이 다 있을 때만 · 아니면 None."""
    pb = (d.get("reason_facts") or {}).get("picked_by")
    if not isinstance(pb, dict) or not all(isinstance(pb.get(k), str) and pb[k].strip() for k in PICKED_BY_KEYS):
        return None
    return {k: pb[k].strip() for k in PICKED_BY_KEYS}


def picked_line(pb: dict) -> str:
    """정해 둔 첫 점검 이유 한 줄(확률 ÷ 분 말 0 · 절 이름 = 매뉴얼 값 그대로 · 지어낸 값 0)."""
    return (
        f"먼저 볼 점검으로 정해 둔 {pb['title']}({pb['section']}절) — 원래 {pb['instead_title']}({pb['instead_of']}절)"
    )


def _checked_reason(row: m.DiagSession, d: dict, facts: Facts) -> dict:
    """R5-6 '고른 이유'는 R8 근거 검사를 거친다.

    실패 · provider 오류 → 코드 템플릿 + '원문으로 대체됨'(AC-06 · AC-31-C1).
    D-652 12-f: 옵션이 첫 점검을 바꿨으면(picked_by) 정해 둔 글 그대로 — AI 글 · 근거 검사를 거치지 않으므로
    원래 절(instead_of)이 허용 절에서 빠져 원문 대체로 떨어지는 일이 없음(셈과 맞는 글).
    """
    v = facts.values
    pb = v.get("picked_by")
    if pb:
        return {"reason": picked_line(pb), "reason_fallback": False, "reason_label": None}
    alts = v["alternatives"]
    g = grounding.Grounding(
        allowed_sections=frozenset([d["section"], *(a["section"] for a in alts)]),
        source_numbers=frozenset(str(x) for x in (v["probability"], v["minutes"], v.get("rank", 1)))  # 'n위'의 n
        | frozenset(n for a in alts for n in grounding.numbers_in(a.get("title") or "")),  # D-585 예외 후보 이름의 숫자
        top_record_ids=frozenset(s["record_id"] for s in row.similar),
    )
    template = f"{v['cause_name']} {v['probability']}% · {d['section']}절 · 약 {v['minutes']}분 · {v['grade']}등급"
    r = grounding.checked(lambda: adapter_for_mode().write(facts), g, template)
    return {"reason": r.text, "reason_fallback": r.fallback, "reason_label": r.label}


def _memo_signal(k, section: str, memo: str, value: str | None) -> tuple[dict | None, bool]:
    """R7 메모 해석. provider 오류는 신호 없음으로 진행을 막지 않고 실패로 센다(AC-31-C1 · C2)."""
    try:
        criteria = screen.criterion(k, section) or ""  # R7 = 그 절의 판정 기준 줄(절 글 전체가 아님 — EV-004)
        sig = adapter_for_mode().memo_signal(section, criteria, memo, value)
    except AdapterError:
        return None, True
    return (sig.as_event_signal() if sig else None), False


def llm_failures(events: list[dict]) -> list[bool]:
    """같은 세션의 문장 생성 · 메모 해석 실패 순서(AC-31-C2).

    대상: 진단 미리보기 문장(처음 보인 때) · 지시문(열 때) · 메모가 있는 결과.
    """
    return [
        bool(e.get("llm_fallback"))
        for e in events
        if e["type"] in {"result", "preview_shown", "instruction_shown"} and "llm_fallback" in e and not e.get("void")
    ]


def alarm_of(c: Connection, alarm_id: int) -> m.Alarm:
    return m.Alarm(**c.execute(select(m.Alarm.__table__).where(m.Alarm.alarm_id == alarm_id)).first()._mapping)


def _guide_of(events: list[dict], k, section: str) -> dict:
    """지금 열린 지시의 지시문(instruction_shown). 기록이 없으면(이전 세션) 절 원문 절차."""
    last_open = max((e["seq"] for e in events if e["type"] == "step_opened"), default=0)
    shown = next(
        (
            e
            for e in reversed(events)
            if e["type"] == "instruction_shown" and e["seq"] > last_open and e["section"] == section
        ),
        None,
    )
    if shown is None:
        return {
            "steps": screen.procedure_steps(k, section),
            "fallback": False,
            "fallback_label": None,
            "original": None,
        }
    return {
        "steps": shown["steps"],
        "fallback": shown["llm_fallback"],
        "fallback_label": shown.get("label"),
        "original": shown.get("original"),
    }


def view(c: Connection, row: m.DiagSession) -> dict:
    events = events_of(c, row.session_id)
    k = flow.knowledge_of(events)
    g, minutes = deps.grades(c)
    _, g, minutes = flow.grades_of(events, g, minutes)
    state = session.fold(k, row.symptom_group, similar_of(row), fold_inputs(events))
    step = current_step(events) or {}
    order = [x.code for x in rules.ordered_causes(k, row.symptom_group)]
    probs, evidence = screen.decided_numbers(step, order) or (  # EV-011 CH-02: 화면 숫자 = Pack 판정 출력
        session.probabilities(state),
        rules.evidence_counts(state.candidates, state.similar),
    )
    counts = screen.shown_counts(step, events, order)
    al = alarm_of(c, row.alarm_id)
    out = {
        "session_id": row.session_id,
        "alarm_id": row.alarm_id,
        "status": row.status,
        "locked": state.locked,
        "alarm_ack_at": next((e["at"] for e in events if e["type"] == "alarm_ack"), None),
        "lock_at": next((e["at"] for e in reversed(events) if e["type"] == "lock"), None) if state.locked else None,
        "probabilities": probs,
        "evidence": evidence,
        **(counts or {}),  # 리허설 2 R3 · R6: 순위 줄 n건 중 k건(Pack 출력)
        "low_evidence": rules.low_evidence(state.similar),
        "top_records": [s["record_id"] for s in row.similar[:3]],
        "similar_basis": "상위 10건",
        "cause_names": screen.cause_names(k, row.symptom_group),  # P-19 ⑨(A-04): T04 순위 원인 이름
        "similar_cards": screen.similar_cards(c, row.similar),  # P-19 ④: T04 기록 카드
        # D-663 S1: 근거 칸 = 1위 후보로 확정된 지난 같은 알람 기록(셈 범위 그대로)
        "top_cards": screen.top_cards(c, al.equipment_id, al.symptom_group, al.occurred_at, probs, counts),
        "alarm": screen.alarm_block(al, len(row.similar)),  # P-19 ③: T03
        "results": [
            {k2: e.get(k2) for k2 in ("seq", "section", "value", "memo")} for e in events if e["type"] == "result"
        ],
        "step": {
            key: step.get(key)
            for key in (
                "kind",
                "section",
                "grade",
                "cause",
                "reason",
                "unable_before",
                "interpretation",
                "quote",
                "outcome",
                "call_status",
                "corrected",
                "step",
                "step_index",
                "steps_total",
            )
            if key not in {"corrected", "step", "step_index", "steps_total"} or step.get(key)
        },
        "last_seq": events[-1]["seq"] if events else 0,
        "recommend_handover": grounding.should_recommend_handover(
            llm_failures(events)
        ),  # AC-31-C2(미리보기 문장 · 지시문 · 메모 해석 실패)
    }
    if not state.locked:  # P-19 ⑤: 잠금 전에는 1.2절 절차만(R1)
        out["lock_instruction"] = screen.lock_instruction(k, g, minutes)
    if (
        state.locked
        and step.get("section")
        and step.get("kind") in {"instruct", "instruct_after_checklist", "precheck", "action", "action_after_checklist"}
    ):
        sec = k.sections[step["section"]]
        needs = step["kind"] in {"instruct_after_checklist", "action_after_checklist"}
        out["instruction"] = {  # 지시 내용은 잠금 뒤에만(R1)
            "section": step["section"],
            "title": sec["title"],
            "text": sec["text"],
            "checklist": (
                flow.checklist_of(k, c, step["section"])
                if k.sections[step["section"]]["kind"] == "action"
                else checklist_for(c, step["section"])
            )
            if needs
            else [],
            "checklist_done": step["section"] in state.b_checklist_done,
            "checklist_common": needs and checklist_is_common(step["section"]),
            # P-19 ⑥ · T06 R-INSTR: 등급 · 분 · 절차 단계 · 판정 기준 · 점검 n / 최대 m · 근거 기록 · 확인 불가 점검
            "grade": step.get("grade") or g.get(step["section"]),  # 사전 확인처럼 판정 값에 등급이 없으면 현재 등급표
            "minutes": minutes.get(step["section"]),
            **_guide_of(events, k, step["section"]),
            "criterion": screen.criterion(k, step["section"]),
            **screen.check_progress(events, k, row.symptom_group, g, state.approvals),
            "evidence_record_ids": screen.evidence_record_ids(k, step["section"], row.similar),
            "unable": [
                {"section": s, "title": k.sections[s]["title"], "count": n} for s, n in sorted(state.unable.items())
            ],
        }
        item = step.get("check") or step.get("action") if step.get("step") else None
        if item:  # 약속 v10 걸음: 그 점검 · 조치 칸 그대로(무엇 = 제목 · 방법 = 절차 · 기준 · 분)
            how = item.get("how") if step.get("check") else ", ".join(item.get("parts") or [])
            out["instruction"].update(
                title=item["what"],
                text="",
                steps=[how if step.get("check") else f"부품 {how}"] if how else [],
                fallback=False,
                fallback_label=None,
                original=None,
                criterion=item.get("criteria") or None if step.get("check") else None,
                minutes=item.get("minutes"),
            )
    if state.locked:  # D-556 [점검 바꾸기] 후보(점검 안내 중만 · 그 밖엔 빈 목록)
        out["switch_candidates"] = switch_candidates(k, step, events, row.symptom_group)
    out.update(flow.flow_view(c, row, state, step, events))  # S-03(P-09): 경로 · 조치 · 재가동 · 호출 · 보고서
    out["exception_draft"] = flow.exception_draft_of(events)  # D-543 「점검방법 등록」 초안
    eg = flow.exception_guide_of(events) or {}
    out["exception_topic"] = eg.get("topic") or eg.get("title")  # 고침A '예외상황-{주제}'(예외 절 칩 · 이름)
    out["exception_why"] = flow.why_line(eg)  # D-593 어떤 조건이 맞았나(설비 · 며칠 전 · 절 · 근거 예외)
    out["exception_miss"] = flow.exception_miss(
        c, alarm_of(c, row.alarm_id), eg
    )  # D-637 F11 예외가 아닌 까닭(최근 같은 정비 없음)
    out["exception_walk"] = flow.walk_of(events, step)  # 정답지 26쪽 '안내 차례 · 적은 순서 그대로'(약속 v10 걸음 길만)
    out["exception_notes"] = flow.exception_notes(c, alarm_of(c, row.alarm_id)) if state.locked else []  # 참고 알림
    return out


def progress(c: Connection, row: m.DiagSession) -> dict:
    """홈 · 종 목록의 '점검 n / 최대 m'(P-19 ① · ②, 결정 014 ②)."""
    events = events_of(c, row.session_id)
    k = flow.knowledge_of(events)
    state = session.fold(k, row.symptom_group, similar_of(row), fold_inputs(events))
    return screen.check_progress(events, k, row.symptom_group, deps.grades(c)[0], state.approvals)


def line_of(events: list[dict], k, confirmed: str | None) -> str:
    """D-642 R5 · R2 보고서 한 줄(정비자 홈 '최근 내 보고서' · 파트장 '확정된 보고서'): 보고서 원인 문장을 자르지 않고
    업무 기록 값으로 — 예외 절에서 조치까지 했거나(또는 원인 확정 없이 예외 점검으로 끝남) '예외상황 · 점검 → 조치',
    아니면 원인 이름(확정 원인 · 원인 미상 · 이상 없음 · 감시). 판정 · 보고서 칸 값은 그대로."""
    eg = flow.exception_guide_of(events)
    ex = {switch.NEW, *([eg["section"]] if eg and eg.get("section") else [])}
    live = [
        e for e in events if e["type"] in {"result", "action_done"} and not e.get("void") and e.get("section") in ex
    ]
    acts = [e for e in live if e["type"] == "action_done"]
    if acts or (live and not confirmed):
        results = [e for e in live if e["type"] == "result"]
        found = [e for e in results if e.get("value") == "이상"] or results[-1:]  # 문제를 찾은 점검(없으면 마지막)
        check = flow.step_title(found[-1], k, eg) if found else None
        act = ", ".join(dict.fromkeys(flow.step_title(e, k, eg) for e in acts)) or None
        parts = [x for x in dict.fromkeys((check, act)) if x]  # 옛 v8 안내는 점검 · 조치 이름이 같은 절 이름 — 한 번만
        return " · ".join(["예외상황", " → ".join(parts)]) if parts else "예외상황"
    step = current_step(events) or {}
    outcome = step.get("outcome") or ("confirmed" if confirmed else "unknown")
    if confirmed and outcome == "confirmed":
        return k.cause(confirmed).name
    return flow.OUTCOME_CAUSE.get(outcome, "원인 미상")


def report_line(c: Connection, session_id: int) -> str:
    """line_of를 세션 업무 기록에서(홈 · 파트장 목록 줄마다 한 번)."""
    row = m.DiagSession(
        **c.execute(select(m.DiagSession.__table__).where(m.DiagSession.session_id == session_id)).one()._mapping
    )
    events = events_of(c, session_id)
    k = flow.knowledge_of(events)
    state = session.fold(k, row.symptom_group, similar_of(row), fold_inputs(events))
    return line_of(events, k, state.confirmed)


def in_exception(c: Connection, row: m.DiagSession) -> bool:
    """왼쪽 알람 칸 '진행 중 · 예외 조치'(정답지 17쪽): [예외] 진행을 고른 세션."""
    return flow.progress_of(events_of(c, row.session_id)) is not None


def call_sent(c: Connection, row: m.DiagSession) -> str | None:
    """G3 왼쪽 칸 '진행 중 · 파트장 호출': 지금 단계가 보낸 호출(답 전)이면 approval · handover, 아니면 None."""
    step = current_step(events_of(c, row.session_id)) or {}
    if step.get("kind") in {"call", "handover"} and step.get("call_status") == "sent":
        return "handover" if step["kind"] == "handover" else "approval"
    return None


def post_event(c: Connection, row: m.DiagSession, actor: str, body: dict) -> dict:
    """입력 하나를 업무 기록에 덧붙인다. seq = 클라이언트가 본 마지막 seq + 1(같은 seq · 같은 내용 재전송은 한 번만)."""
    etype = body.get("type")
    if etype not in CLIENT_TYPES:
        raise HTTPException(status_code=422, detail="unknown_event")

    def deny(reason: str, **detail):  # 업무 규칙 거부는 감사 행을 남긴다(S02-G6-1, G6-S03 비차단 의견)
        audit_rejection(actor, "reject", f"session:{row.session_id}", {"reason": reason, "type": etype, **detail})
        raise HTTPException(status_code=409, detail=reason)

    if row.status == "reported":  # AC-33: 확정한 보고서 · 세션은 정비자가 고칠 수 없다
        deny("session_closed")
    events = events_of(c, row.session_id)
    last = events[-1]["seq"] if events else 0
    payload = {k2: v for k2, v in body.items() if k2 not in {"type", "seq"}}
    # 피드백 F9 사전 확인 [상급자 호출]: 사전 확인 사건에는 값만 — 호출은 아래에서 예외 '호출 후 조치' 길로
    call_up = etype == "precheck_done" and payload.pop("mode", None) == "call_first"
    seq = body.get("seq")
    if isinstance(seq, int) and seq <= last:
        stored = next((e for e in events if e["seq"] == seq), None)
        client_stored = {k2: v for k2, v in stored.items() if k2 not in SERVER_FIELDS} if stored else None
        same_flow = stored and stored.get("_client_type") == etype and stored.get("_client") == payload
        if same_flow or (
            stored and stored["type"] == etype and client_stored == payload
        ):  # 저장된 클라이언트 필드 전체가 같을 때만(S02-G6-3)
            return view(c, row)  # 같은 업무 key 재전송
        raise HTTPException(status_code=409, detail="stale_seq")
    if seq is not None and seq != last + 1:
        raise HTTPException(status_code=409, detail="stale_seq")
    if etype == "alarm_ack":  # EV-007(D-422): 알람 확인 시각만 남김 — 되풀이는 기록 0 · 판정(배포 서버 Run) 안 부름
        if any(e["type"] == "alarm_ack" for e in events):
            return view(c, row)
        from datetime import UTC, datetime

        _store(c, row.session_id, last + 1, etype, {"at": datetime.now(UTC).isoformat()}, actor)
        audit(c, actor, etype, f"session:{row.session_id}", {"seq": last + 1})
        return view(c, row)
    k = flow.knowledge_of(events)
    state = session.fold(k, row.symptom_group, similar_of(row), fold_inputs(events))
    step = current_step(events) or {}
    if etype == "result":
        if payload.get("memo") and step.get("section"):
            sig, failed = _memo_signal(k, step["section"], payload["memo"], payload.get("value"))
            payload["llm_fallback"] = failed  # 서버만 쓰는 필드(요청 스키마는 받지 않음)
            if sig:
                payload["signal"] = sig
        if step.get("step") and payload.get("section") == step.get("section"):
            payload["step"] = step["step"]  # 약속 v10 5절: 예외 안내 점검 걸음(c1 …) — 판정 결정이 정한 것을 그대로
        try:
            session.validate_result(state, step, {"type": "result", **payload})
        except session.EventRejected as exc:
            audit_rejection(
                actor, "reject", f"session:{row.session_id}", {"reason": str(exc), "section": payload.get("section")}
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    elif etype == "b_checklist_done":
        if step.get("kind") not in {"instruct_after_checklist", "action_after_checklist"} or payload.get(
            "section"
        ) != step.get("section"):
            deny("no_b_checklist_step")
        expected = (
            flow.checklist_of(k, c, step["section"])
            if k.sections[step["section"]]["kind"] == "action"
            else checklist_for(c, step["section"])
        )
        if sorted(payload.get("checked", [])) != sorted(expected):
            deny("checklist_incomplete")
    elif etype == "lock" and not any(e["type"] == "alarm_ack" for e in events):
        deny("alarm_not_acked")  # EV-007: 알람 화면을 거친 뒤에만 잠금(빠른 여러 번 누름으로 건너뛰지 못함)
    elif etype == "lock" and state.locked:
        deny("already_locked")
    elif etype in {"precheck_done"} and not state.locked:
        deny("not_locked")
    elif etype == "precheck_done" and state.precheck_done:
        deny("not_precheck_step")
    elif etype == "precheck_done" and not str(payload.get("values") or "").strip():
        deny("precheck_values_required")  # D-422: 사전 확인 값 없이 다음으로 가지 않음
    elif etype in {"confirm_answer", "out_of_scope_choice"} and not state.pending:
        deny("nothing_pending")
    elif etype == "switch_check":  # 약속 v9 [점검 바꾸기]: 지금 추천 후보 중 하나 → 판정 Run(Pack이 그 점검을 엶)
        # D-585: 진단 단계(잠금 전)는 앱이 고른 값만 들고 있음(예외 절 사건이 먼저 가면 Pack이 잠금을 건너뜀) ·
        # 사전 확인 완료 때 switch_check로 함께 넘김
        early = step.get("kind") == "lock" and not state.locked
        at = _first_decision(row, c, k, *deps.grades(c)) if early else step
        rows = (_candidate_rows if early else switch_candidates)(k, at, events, row.symptom_group)
        cands = {x["section"] for x in rows}
        if not (early or switchable(step)) or payload.get("section") not in cands:
            deny("not_switchable")
        payload = {"section": payload["section"], "from_section": at.get("section"), "choice": "candidate"}
        if early:
            _store(c, row.session_id, last + 1, DIAG_PICK, payload, actor)
            audit(c, actor, DIAG_PICK, f"session:{row.session_id}", {"seq": last + 1, **payload})
            return view(c, row)
    elif etype == "exception_start":  # 약속 v9 [예외]: 호출 후 조치 / 바로 조치 → 판정 Run(절 EX-NEW)
        if step.get("kind") not in SWITCHABLE or flow.progress_of(events):
            deny("not_switchable")
    elif etype in flow.CLIENT_TYPES:  # S-03: 조치 · 재가동 · 마감 · 호출 · 보고서(P-09)
        try:
            stype, spayload = flow.accept(c, row, actor, step, state, events, etype, payload)
        except HTTPException as exc:
            audit_rejection(actor, "reject", f"session:{row.session_id}", {"reason": str(exc.detail), "type": etype})
            raise
        _store(c, row.session_id, last + 1, stype, {**spayload, "_client_type": etype, "_client": payload}, actor)
        audit(c, actor, etype, f"session:{row.session_id}", {"seq": last + 1, **spayload})
        if etype not in flow.NOT_JUDGED:  # D-543 「노하우 등록」 · D-556 「AI 나눔」은 판정 입력이 아님 — 판정 Run 0
            _open_step(c, row.session_id, row, events_of(c, row.session_id), actor)
        return view(c, _row(c, row.session_id))
    if etype == "lock":
        from datetime import UTC, datetime

        payload["at"] = datetime.now(UTC).isoformat()
    picks = [e for e in events if e["type"] == DIAG_PICK]
    if etype == "precheck_done" and picks:  # 진단에서 고른 점검 — 안전 단계를 마친 이 판정부터 Pack이 그 점검을 엶
        pick = {key: picks[-1][key] for key in ("section", "from_section", "choice")}
        last += 1
        _store(c, row.session_id, last, "switch_check", pick, actor)
    _store(c, row.session_id, last + 1, etype, payload, actor)
    audit(
        c,
        actor,
        {"result": "input", "confirm_answer": "confirm_answer"}.get(etype, etype),
        f"session:{row.session_id}",
        {"seq": last + 1, **payload},
    )
    decision = _open_step(c, row.session_id, row, events_of(c, row.session_id), actor)
    if call_up and decision.get("kind") in SWITCHABLE:  # 첫 점검 안내가 열렸으면 [예외] 호출 후 조치와 같은 사건
        etype, payload, last = "exception_start", {"mode": "call_first"}, events_of(c, row.session_id)[-1]["seq"]
        _store(c, row.session_id, last + 1, etype, payload, actor)
        audit(c, actor, etype, f"session:{row.session_id}", {"seq": last + 1, **payload})
        decision = _open_step(c, row.session_id, row, events_of(c, row.session_id), actor)
    if etype == "exception_start" and payload["mode"] == "call_first" and decision["kind"] == "call":
        _auto_call(c, row, actor)  # 정답지 묶음 ① 16쪽: 호출 후 조치는 호출이 자동으로 나감
    return view(c, row)


def _auto_call(c: Connection, row: m.DiagSession, actor: str) -> None:
    """지금 열린 호출 단계에 정비자가 「파트장 호출」을 누른 것과 같은 기록(call_send → 판정 Run)."""
    events = events_of(c, row.session_id)
    k = flow.knowledge_of(events)
    state = session.fold(k, row.symptom_group, similar_of(row), fold_inputs(events))
    stype, spayload = flow.accept(c, row, actor, current_step(events) or {}, state, events, "call_send", {})
    seq = events[-1]["seq"] + 1
    _store(c, row.session_id, seq, stype, {**spayload, "_client_type": "call_send", "_client": {}}, actor)
    audit(c, actor, "call_send", f"session:{row.session_id}", {"seq": seq, **spayload})
    _open_step(c, row.session_id, row, events_of(c, row.session_id), actor)


DIAG_PICK = "diagnosis_pick"  # D-585 진단 카드 [점검 바꾸기] 고른 값(앱 사건 · 판정 입력 아님)
SWITCHABLE = {"instruct", "instruct_after_checklist"}  # 점검 안내 중(사전 확인 · 조치 · 호출 · 보고서 단계는 0)


def switchable(step: dict) -> bool:
    """점검 안내 중 · 또는 승인 전 예외 안내 호출(D-580: 예외도 후보 하나 — 다른 점검으로 바꿀 수 있음)."""
    return step.get("kind") in SWITCHABLE or (step.get("kind") == "call" and step.get("reason") == "exception_guide")


def switch_candidates(k, step: dict, events: list[dict] = (), group: str | None = None) -> list[dict]:
    """[점검 바꾸기] 후보 = 판정이 낸 지금 점검 밖 후보(decision.reason_facts.alternatives) — 제목 · 등급표 등급.
    바꾼 점검(결정 switched · 후보 칸 없음)이면 앞 판정 후보를 이어 씀(지금 점검 빼고 · 정답지 14쪽 '다시 바꾸기').
    D-580: 판정이 후보를 안 냈는데 예외가 얽히면 그 증상군 매뉴얼 점검 · 조건 맞은 예외도 같은 줄(끝난 예외는 뺌)."""
    return _candidate_rows(k, step, events, group) if switchable(step) else []


def _candidate_rows(k, step: dict, events: list[dict], group: str | None) -> list[dict]:
    eg = flow.exception_guide_of(list(events))
    alts = (step.get("reason_facts") or {}).get("alternatives") or []
    if not alts and step.get(
        "switched"
    ):  # 앞 판정들 후보를 이어 씀(늦은 것 먼저 · 예외에서 바꿔 나와도 예외 줄이 남음)
        seen: dict[str, dict] = {}
        for e in reversed(events):
            if e["type"] == "step_opened":
                for a in (e["decision"].get("reason_facts") or {}).get("alternatives") or []:
                    seen.setdefault(a.get("section"), a)
        alts = list(seen.values())
    if not alts and eg and group:  # 판정이 후보를 안 낸 옛 Pack(ef9ec74 앞) — 그 증상군 매뉴얼 점검 + 예외
        alts = [
            {"section": c.section_id, "grade": k.sections[c.section_id].get("grade")} for c in k.checks.get(group, [])
        ]
        alts = [*alts, {"section": eg.get("section"), "grade": eg.get("grade")}]
    ex = eg.get("section") if eg else None
    results = {e.get("section") for e in events if e["type"] == "result" and not e.get("void")}
    return [
        {
            "section": a["section"],
            "title": (flow.first_what(eg) or eg.get("title"))
            if a["section"] == ex
            else k.sections[a["section"]]["title"],
            "grade": a.get("grade"),
            # 고침A 13쪽 제목 아래 설명 = Pack 후보 desc(c1a15-pack 986eaff · 매뉴얼 판정 기준 첫 문장 · LLM 0)
            "summary": a.get("desc") or (flow.why_line(eg) if a["section"] == ex else None),
            "exception": a["section"] == ex,
        }
        for a in alts
        if (a.get("section") in k.sections or a.get("section") == ex)
        and a["section"] != step.get("section")
        and a["section"] not in results
    ]


def check_records(sid: int, events: list[dict]) -> list[dict]:
    """약속 v9 3절 기록 줄: 헛걸음(switch_check) · 놓침 / 새 예외(마지막 확정 예외 하나 · v10) + 사람이 고른 점검.
    사람이 고른 점검 = 예외 진행이면 {EX-NEW, 그때 점검} · 아니면 마지막 바꾼 점검 · 없으면 None."""
    live = [e for e in events if not e.get("void")]
    out, human, chain = [], None, None
    for e in live:
        if e["type"] == "switch_check":  # 고침A 14쪽: 여러 번 바꾸면 마지막에 결과를 낸 점검만 헛걸음(중간 고름 기록 0)
            start = chain["from_section"] if chain else e.get("from_section")
            chain = {"section": e["section"], "from_section": start, "seq": e["seq"]}
        elif e["type"] == "result" and chain and e.get("section") == chain["section"]:
            human = {"section": chain["section"], "from_section": chain["from_section"]}
            out.append({"kind": "wasted_step", "session_id": sid, "human_check": human, "at_seq": chain["seq"]})
            chain = None
        elif e["type"] == "exception_start":
            before = current_step([x for x in live if x["seq"] < e["seq"]]) or {}
            human = {"section": switch.NEW, "from_section": before.get("section")}
    last = next((e for e in reversed(live) if e["type"] == "report_confirm"), None)
    # 놓침 / 새 예외 = 마지막 확정 예외 하나(약속 v10 · 예외 전체에 한 번) — v9 기록은 짝마다 한 줄 그대로 읽음
    for it in flow.exception_entries((last or {}).get("exception_record") or {}):
        if not it.get("kind"):
            continue
        out.append(
            {
                "kind": it["kind"],
                "session_id": sid,
                "human_check": human,
                "relation_id": (it.get("match") or {}).get("no"),
                "at_seq": last["seq"],
            }
        )
    offer = flow.exception_offer(events) if last else None  # D-633 예외가 보였는데 끝까지 안 고름(확정 때 정해짐)
    if offer and not offer["adopted"]:
        out.append(
            {
                "kind": "exception_not_picked",
                "session_id": sid,
                "human_check": {"section": offer["picked_section"], "from_section": None},
                "relation_id": offer["relation_id"],
                "at_seq": last["seq"],
                "how": offer["how"],
                "run_id": offer["run_id"],
            }
        )
    return out


def check_record_list(c: Connection) -> dict:
    """약속 v9 기록 줄 전체(세션 · 순번 순) — LLMOps가 읽음(내부 토큰)."""
    sids = c.execute(
        text(
            "select distinct session_id from ops.session_events "
            "where type in ('switch_check', 'exception_start', 'report_confirm') order by session_id"
        )
    ).scalars()
    return {"items": [r for sid in sids for r in check_records(sid, events_of(c, sid))]}


def open_alarm_counts(c: Connection) -> dict[str, int]:
    """R9 입력: 로그인한 정비자(유효 세션)별 처리 중 알람 수."""
    from datetime import UTC, datetime

    users = [
        u
        for (u,) in c.execute(
            select(m.LoginSession.user_id)
            .join(m.Account, m.Account.user_id == m.LoginSession.user_id)
            .where(
                m.LoginSession.app == "tech",
                m.Account.role == "tech",
                m.LoginSession.revoked_at.is_(None),
                m.LoginSession.expires_at > datetime.now(UTC),
            )
            .distinct()
        ).all()
    ]
    counts = dict(
        c.execute(
            select(m.Alarm.assigned_to, func.count())
            .where(m.Alarm.status.in_(["new", "in_progress"]), m.Alarm.assigned_to.is_not(None))
            .group_by(m.Alarm.assigned_to)
        ).all()
    )
    return {u: counts.get(u, 0) for u in users}


# ---------------------------------------------------------------- S-05(P-14): 정정 · 메모 덧붙이기(FLOW-14)


def _closed_deny(row: m.DiagSession, actor: str, etype: str) -> None:
    """확정한 세션(승인 대기 · 승인)은 정정 · 메모 덧붙이기 모두 거부(AC-48-C2 · 생성자 해석 ③).

    반려로 다시 열리면 다시 된다(AC-48-C3).
    """
    if row.status == "reported":
        audit_rejection(actor, "reject", f"session:{row.session_id}", {"reason": "session_closed", "type": etype})
        raise HTTPException(status_code=409, detail="session_closed")


def _step_label(k, d: dict) -> dict:
    sec = d.get("section")
    return {"kind": d.get("kind"), "section": sec, "title": k.sections[sec]["title"] if sec in k.sections else None}


def _trial_decision(c: Connection, row: m.DiagSession, events: list[dict]) -> dict:
    """정정 확인 창 '다음'(EV-011 CH-06): 정정을 넣은 기록으로 부른 Pack judge 시험 판정(X-Run-Purpose: trial).

    배포 실패면 PackUnavailable(503) — 기록은 저장 전이라 그대로. 취소해도 이 Run은 trace에 남고 LLMOps가 뺌.
    """
    g, minutes = deps.grades(c)
    inputs = [e for e in events if e["type"] in INPUT_TYPES]
    ms = next((e.get("manual_support") for e in events if e["type"] == "step_opened"), None)
    rel, g, minutes = flow.with_exception(flow.exception_guide_of(events), g, minutes)
    res = runs.judge(
        row.alarm_id,
        row.symptom_group,
        row.similar,
        inputs,
        g,
        minutes,
        ms,
        purpose="trial",
        exception_relation=rel,
        context=runs.alarm_context(alarm_of(c, row.alarm_id)),
    )
    return res.output["decision"]


def post_correction(c: Connection, row: m.DiagSession, actor: str, body: dict) -> dict:
    """'이 기록 정정'(AC-45 ~ AC-48): 정비자가 입력해 제출한 결과 · 메모 · 사전 확인 측정값만, 사유 필수, 덧붙임 기록.

    결과를 바꾸면 반영 방식(session.correction_mode)에 따라 다시 판정 · 정정 호출 · 보낸 호출에 붙임.
    다시 판정으로 지금 열린 지시가 바뀌면 confirmed 없이 409 confirm_required
    (AC-46-C3 · C4: 취소하면 아무것도 바뀌지 않음).
    """
    etype = "correction"

    def deny(reason: str, **detail):
        audit_rejection(actor, "reject", f"session:{row.session_id}", {"reason": reason, "type": etype, **detail})
        raise HTTPException(status_code=409, detail=reason)

    _closed_deny(row, actor, etype)
    reason = (body.get("reason") or "").strip()
    if not reason:
        deny("reason_required")  # AC-45-C2
    events = events_of(c, row.session_id)
    idx = next((i for i, e in enumerate(events) if e["seq"] == body.get("target_seq")), None)
    if idx is None:
        deny("no_record")
    target = events[idx]
    if target["type"] == "result":
        before = {"value": target.get("value"), "memo": target.get("memo")}
        after = {"value": body.get("value", before["value"]), "memo": body.get("memo", before["memo"])}
        if after["value"] not in session.RESULT_VALUES:
            deny("invalid_value")
    elif target["type"] == "precheck_done":
        before = {"values": target.get("values")}
        after = {"values": body.get("values", before["values"])}
    else:
        deny("not_correctable", target_type=target["type"])  # AC-48-C1
    after = {k2: (v.strip() if isinstance(v, str) else v) for k2, v in after.items()}
    if target["type"] == "precheck_done" and not after["values"]:
        deny("precheck_values_required")  # EV-007 review: 정정으로 사전 확인 값을 비우지 못함
    if after == before:
        deny("no_change")
    changed = target["type"] == "result" and after["value"] != before["value"]
    try:
        plan = session.correction_mode(events, idx, value_changed=changed)
    except session.EventRejected as exc:
        deny(str(exc))
    k = flow.knowledge_of(events)
    seq = events[-1]["seq"] + 1
    at = datetime.now(UTC).isoformat()
    payload = {
        "target_seq": target["seq"],
        "target_type": target["type"],
        "section": target.get("section"),
        "before": before,
        "after": after,
        "reason": reason,
        "by": actor,
        "mode": plan["mode"],
    }
    if plan.get("call_id"):
        payload["call_id"] = plan["call_id"]
    cur = current_step(events) or {}
    if plan["mode"] == "rejudge" and cur.get("kind") in INSTRUCTION_KINDS and not body.get("confirmed"):
        # EV-011 CH-06: '다음' = Pack judge 시험 판정(열린 지시가 있고 확인 전일 때만 — 확인 뒤 저장은 실제 Run)
        trial = logical([*raw_events_of(c, row.session_id), {"seq": seq, "type": etype, "_at": at, **payload}])
        nxt = _trial_decision(c, row, trial)
        if (nxt.get("kind"), nxt.get("section")) != (
            cur.get("kind"),
            cur.get("section"),
        ):  # AC-46-C3: 확인해야 저장(거부가 아니라 확인 요청 — 감사하지 않음)
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "confirm_required",
                    "current": _step_label(k, cur),
                    "next": _step_label(k, nxt),
                    "target": {"section": target.get("section"), "before": before, "after": after},
                },
            )
    _store(c, row.session_id, seq, etype, payload, actor)
    audit(c, actor, "correction", f"session:{row.session_id}", {"seq": seq, **payload})
    if plan["mode"] in {"attach_to_call", "attach_answered"} and plan.get("call_id"):  # AC-47-C2 · 019 ② ③
        sec = target.get("section") or next(
            (
                x["decision"]["section"]
                for x in events
                if x["type"] == "step_opened" and x["decision"].get("kind") == "precheck"
            ),
            None,
        )
        item = {
            "at": at,
            "section": sec,
            "title": k.sections[sec]["title"] if sec in k.sections else None,
            "target_type": target["type"],
            "before": before,
            "after": after,
            "reason": reason,
            "answered": plan["mode"] == "attach_answered",
        }
        c.execute(
            text(
                "update ops.calls set corrections = corrections || cast(:item as jsonb), change_seq = :q "
                "where call_id = :c"
            ),
            {"item": json.dumps([item], ensure_ascii=False), "q": deps.next_change_seq(c), "c": plan["call_id"]},
        )
    if plan["mode"] in {
        "rejudge",
        "call_with_correction",
    }:  # 메모만 · 보낸 호출에 붙임은 다음 단계 그대로(R13 — 다시 열지 않음)
        _open_step(c, row.session_id, row, events_of(c, row.session_id), actor)
    return view(c, _row(c, row.session_id))


def post_note(c: Connection, row: m.DiagSession, actor: str, body: dict) -> dict:
    """'메모 덧붙이기'(AC-48-C1): 정정하지 않는 기록에 시각과 함께 메모를 덧붙인다.

    원래 기록 · 판정 · 보고서는 그대로(019 ⑥).
    """
    etype = "note_append"

    def deny(reason: str, **detail):
        audit_rejection(actor, "reject", f"session:{row.session_id}", {"reason": reason, "type": etype, **detail})
        raise HTTPException(status_code=409, detail=reason)

    _closed_deny(row, actor, etype)
    memo = (body.get("memo") or "").strip()
    if not memo:
        deny("memo_required")
    events = events_of(c, row.session_id)
    target = next((e for e in events if e["seq"] == body.get("target_seq")), None)
    if target is None:
        deny("no_record")
    if target["type"] not in session.NOTE_TYPES or target.get("void"):
        deny("not_note_target", target_type=target["type"])
    seq = events[-1]["seq"] + 1
    sec = target.get("section") or {
        "lock": "1.2",
        "restart": "6.1",
        "restart_result": "6.1",
        "monitor_close": "6.2",
    }.get(target["type"])
    payload = {"target_seq": target["seq"], "target_type": target["type"], "section": sec, "memo": memo}
    _store(c, row.session_id, seq, etype, payload, actor)
    audit(c, actor, "note_append", f"session:{row.session_id}", {"seq": seq, **payload})
    return view(c, _row(c, row.session_id))
