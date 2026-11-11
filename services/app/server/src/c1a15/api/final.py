"""보고서 최종본(D-528 · 구현 부록 5 · D-530 덧 4): 확정된 보고서 하나를 문서 한 장의 구역 1 ~ 5 값으로.

읽기만, 새 저장 0.

머리(설비 · 알람 · 확정 · 작성 · 잠금 확인 · 승인 상태) · 진행 넷 · 1 센서 표 · 2 원인 분석(진단 때 % · 근거 n · 결과)
· 3 조치 경과(감사 기록 + 절 · 등급) · 4 조치 결과(권한 사용 = 3의 등급 셈) · 5 미확인 · 요청 · 꼬리(작성 · 승인).
부록 '정해야 할 것'(요약 문단 · 권고 구역 · 이력 번호 · 알람 수신 행 · 실제 출력)은 넣지 않는다.
"""

from sqlalchemy import Connection, or_, select

from c1a15.api import deps, flow, screen, sessions
from c1a15.db import models as m
from c1a15.engine import session

# 3 조치 경과에 등급을 붙이는 행동(지시 · 메모 · 정정은 절만) — 4 권한 사용은 이 줄들의 등급을 센다
GRADED = {"lock", "precheck_done", "input", "b_checklist_done", "action_done", "restart"}
FIXED_SECTION = {"lock": "1.2", "restart": "6.1", "restart_result": "6.1"}
SYSTEM_ACTIONS = {"instruct", "call_required"}
HIDDEN = {"login", "logout"}


def _field(fields: list[dict], key: str) -> str:
    return next((f["text"] for f in fields if f["key"] == key), "")


def first_ranking(k, row: m.DiagSession, events: list[dict]) -> tuple[dict, dict]:
    """진단 때 원인 % · 근거 n = 세션을 만들 때 남긴 preview_shown.

    history.py 2 진단(지난 화면)과 같은 값 · 기록이 없는 옛 세션은 같은 다시 셈."""
    snap = next((e for e in events if e["type"] == "preview_shown" and "preview" in e), None)
    if snap is not None:
        return snap["probabilities"], snap["evidence"]
    st0 = session.fold(k, row.symptom_group, sessions.similar_of(row), [])
    return session.probabilities(st0), session.rules.evidence_counts(st0.candidates, st0.similar)


def _cause_rows(k, row: m.DiagSession, events: list[dict], state, grades: dict) -> list[dict]:
    probs, evidence = first_ranking(k, row, events)
    names = screen.cause_names(k, row.symptom_group)
    latest = {}  # 절 → 마지막 결과 값(정정이면 고친 값, 무효 기록 뺌)
    for e in events:
        if e["type"] == "result" and not e.get("void") and e.get("value"):
            latest[e["section"]] = e["value"]
    out = []
    order = list(screen.in_order(probs, list(names)))  # 같은 %면 매뉴얼 개요 순
    for code in sorted(order, key=lambda x: -probs.get(x, 0)):  # 진단 때 % 높은 순(가이드 31쪽)
        checks = [ch for ch in k.checks.get(row.symptom_group, []) if ch.cause == code]
        done = [ch for ch in checks if ch.section_id in latest]
        if state.confirmed == code and done:
            sec = next((ch.section_id for ch in done if latest[ch.section_id] == "이상"), done[-1].section_id)
            result = f"확정 · {sec} 점검 이상"
        elif done:
            result = f"{done[-1].section_id} 점검 {latest[done[-1].section_id]}"
        else:
            hard = next((ch for ch in checks if grades.get(ch.section_id) in ("C", "D")), None)
            result = f"미확인 · {hard.section_id} {grades[hard.section_id]}등급" if hard else "점검 안 함"
        out.append(
            {
                "name": names.get(code, code),
                "probability": probs.get(code, 0),
                "evidence": evidence.get(code, 0),
                "result": result,
                "confirmed": state.confirmed == code,
            }
        )
    return out


def _timeline(c: Connection, k, rep, row: m.DiagSession, grades: dict) -> list[dict]:
    from c1a15.api.lead import audit_label  # 감사 기록 화면과 같은 행동 문구(순환 import 피함)

    accts = {u: n for u, n in c.execute(select(m.Account.user_id, m.Account.display_name)).all()}
    call_ids = [x for (x,) in c.execute(select(m.Call.call_id).where(m.Call.session_id == row.session_id)).all()]
    targets = [f"session:{row.session_id}", f"report:{rep.report_id}"] + [f"call:{x}" for x in call_ids]
    q = (
        select(m.AuditEntry.__table__)
        .where(or_(*(m.AuditEntry.target == t for t in targets)))
        .order_by(m.AuditEntry.at, m.AuditEntry.audit_id)
    )
    out = []
    for a in c.execute(q).all():
        if a.action in HIDDEN or a.action in SYSTEM_ACTIONS or a.actor == "system":
            continue
        d = a.detail or {}
        sec = d.get("section") or FIXED_SECTION.get(a.action)
        if a.action == "precheck_done":  # 사전 확인 절(x.2)은 증상군마다 — 기록 칸에 없어 매뉴얼에서
            sec = sec or k.prechecks.get(row.symptom_group)
        out.append(
            {
                "at": a.at.isoformat(),
                "who": accts.get(a.actor, a.actor),
                "text": audit_label(a, k, accts),
                "section": sec,
                "grade": grades.get(sec) if a.action in GRADED and sec else None,
            }
        )
    return out


def _cause_basis(k, row: m.DiagSession, events: list[dict], state) -> dict | None:
    """확정 근거(가이드 20쪽): 확정 원인을 정한 점검 절의 마지막 결과 · 메모(정정이면 고친 값, 무효 기록 뺌)."""
    if not state.confirmed:
        return None
    last = {}
    for e in events:
        if e["type"] == "result" and not e.get("void") and e.get("value"):
            last[e["section"]] = e
    done = [ch for ch in k.checks.get(row.symptom_group, []) if ch.cause == state.confirmed and ch.section_id in last]
    if not done:
        return None
    ch = next((x for x in done if last[x.section_id]["value"] == "이상"), done[-1])
    e = last[ch.section_id]
    return {
        "section": ch.section_id,
        "title": k.sections[ch.section_id]["title"],
        "value": e["value"],
        "memo": e.get("memo"),
    }


def final_report(c: Connection, report_id: int) -> dict | None:
    rep = c.execute(select(m.Report.__table__).where(m.Report.report_id == report_id)).first()
    if rep is None:
        return None
    grades, _ = deps.grades(c)
    row = sessions._row(c, rep.session_id)
    alarm = flow.sessions_alarm(c, row)
    events = sessions.events_of(c, row.session_id)
    k = flow.knowledge_of(events)
    _, grades, _ = flow.grades_of(events, grades, {})  # 예외 절 등급(C) — 3 · 4 권한 사용(덧 34 · v9 EX-NEW)
    state = session.fold(k, row.symptom_group, sessions.similar_of(row), sessions.fold_inputs(events))
    names = {u: n for u, n in c.execute(select(m.Account.user_id, m.Account.display_name)).all()}
    lock = next((e for e in events if e["type"] == "lock" and not e.get("void")), None)
    timeline = _timeline(c, k, rep, row, grades)
    use = {g: sum(1 for t in timeline if t["grade"] == g) for g in ("A", "B", "C")}
    _, restart = flow._restart_events(events)
    restart_text = None
    if restart:
        rv = flow.restart_block(alarm, bool(state.confirmed))
        side = "이내" if restart["outcome"] == "within" else "밖"  # 가이드 21쪽: '온도 차 9.0 K · 경계 8.2 K 이내'
        restart_text = f"{rv['sensor']} {rv['value_text']} · 경계 {rv['boundary_text']} {side}"
    decision = None
    if rep.status == "approved" and rep.approval:
        ap = rep.approval
        by = names.get(ap["by"], ap["by"])
        decision = {"kind": "approved", "by_name": by, "at": ap["at"], "memo": ap.get("memo")}
    elif rep.status == "rejected" and rep.rejection:
        rj = rep.rejection
        decision = {"kind": "rejected", "by_name": names.get(rj["by"], rj["by"]), "at": rj["at"], "memo": rj["reason"]}
    f = rep.fields
    table = deps.alarm_table()
    return {
        "report_id": rep.report_id,
        "session_id": rep.session_id,
        "status": rep.status,
        "equipment_id": alarm.equipment_id,
        "equipment_type": deps.equipment_types().get(alarm.equipment_id, deps.default_equipment_type()),
        "group_name": deps.group_names()[alarm.symptom_group],
        "occurred_at": alarm.occurred_at.isoformat(),
        "confirmed_at": rep.confirmed_at.isoformat() if rep.confirmed_at else None,
        "author_name": names.get(rep.author, rep.author),
        "author_role": f"{screen.shift_summary(0)['label']} 정비",  # 머리 '작성 김 정비(야간 정비)'
        "lock_at": lock["_at"] if lock else None,
        "rejected_before": rep.rejection is not None,  # 진행 '정비자 다시 확정'(반려 뒤 다시 확정)
        "decision": decision,
        "sensors": [
            {
                "name": s["name"],
                "value_text": s["value_text"],
                "range_text": s["range_text"],
                "status": s["status"],
                "out_of_range": s["out_of_range"],
            }
            for s in screen.sensor_rows(alarm.alarm, alarm.symptom_group, alarm.sensor_snapshot, table)
        ],
        "sensor_evidence": _field(f, "sensor_evidence"),
        "causes": _cause_rows(k, row, events, state, grades),
        "cause": _field(f, "cause"),
        "cause_basis": _cause_basis(k, row, events, state),
        "check_path": _field(f, "check_path"),
        "evidence_records": _field(f, "evidence_records"),
        "timeline": timeline,
        "action": _field(f, "action"),
        "restart": restart_text,
        "downtime": _field(f, "downtime"),
        "grade_use": use,
        "approved_calls": flow.approved_calls(k, events, grades),
        "unresolved": _field(f, "unresolved"),
        "exception_record": flow.record_out(flow.exception_of(events)),
    }
