"""S-05(P-14): 세션 단계 기록 조회 — T15 지난 화면(AC-41 · FLOW-14 '다시 읽기').

진행 표시 5단계(알람 · 진단 · 점검 · 조치 · 보고서)마다 제출 기록을 시간순으로 낸다. 기록마다 그때 화면을 그릴 값,
정정(정정 n회 · 이전 값 · 사유) · 덧붙인 메모 · '정정으로 무효' · 정정 · 메모 덧붙이기 가능 여부를 서버가 정한다.
보고서 단계는 기존 수정 경로(연필 · 감사)라 기록을 내지 않는다(AC-41-C5).
"""

from datetime import datetime

from sqlalchemy import Connection, select

from c1a15.api import deps, flow, screen, sessions
from c1a15.db import models as m
from c1a15.engine import session

STEP_NAMES = ["알람", "진단", "점검", "조치", "보고서"]


def _hhmm(at: str | None) -> str:
    return screen.hhmm_kst(datetime.fromisoformat(at)) if at else ""


def _band(e: dict, verb: str, closed_at: str | None) -> str:
    """띠 문구: '02:23 결과 보냄 · 02:31 정정 · 보고서 02:45 확정'(T15 R-PASTBAR)."""
    text = f"{_hhmm(e.get('at') or e.get('_at'))} {verb}"
    cf = e.get("corrected_from")
    if cf:
        text += f" · {_hhmm(cf.get('at'))} 정정"
    if closed_at:
        text += f" · 보고서 {_hhmm(closed_at)} 확정"
    return text


def _common(e: dict, closed: bool) -> dict:
    return {
        "seq": e["seq"],
        "at": e.get("at") or e.get("_at"),
        "void": bool(e.get("void")),
        "correctable": e["type"] in session.CORRECTABLE_TYPES and not e.get("void") and not closed,
        "notable": e["type"] in session.NOTE_TYPES and not e.get("void") and not closed,
        "corrections": [{k: x.get(k) for k in ("at", "before", "after", "reason")} for x in e.get("corrections", [])],
        "notes": [{"at": n["at"], "memo": n["memo"]} for n in e.get("notes", [])],
    }


def _opened_before(events: list[dict], e: dict, section: str) -> dict:
    return next(
        (
            x["decision"]
            for x in reversed(events)
            if x["type"] == "step_opened" and x["seq"] < e["seq"] and x["decision"].get("section") == section
        ),
        {},
    )


def _shown_before(events: list[dict], e: dict, section: str) -> dict | None:
    return next(
        (
            x
            for x in reversed(events)
            if x["type"] == "instruction_shown" and x["seq"] < e["seq"] and x.get("section") == section
        ),
        None,
    )


def history(c: Connection, row: m.DiagSession) -> dict:
    g, minutes = deps.grades(c)
    events = sessions.events_of(c, row.session_id)
    k = flow.knowledge_of(events)
    alarm = sessions.alarm_of(c, row.alarm_id)
    closed = row.status == "reported"
    rep = c.execute(select(m.Report.confirmed_at).where(m.Report.session_id == row.session_id)).scalar()
    closed_at = rep.isoformat() if closed and rep else None
    started = events[0]["_at"] if events else None
    steps: list[list[dict]] = [[] for _ in STEP_NAMES]
    # 1 알람(T15 / alarm)
    steps[0].append(
        {
            "seq": 0,
            "kind": "alarm",
            "at": alarm.occurred_at.isoformat(),
            "band": f"{screen.hhmm_kst(alarm.occurred_at)} 알람 · {_hhmm(started)} 점검 시작",
            "alarm": screen.alarm_block(alarm, len(row.similar)),
            "void": False,
            "correctable": False,
            "notable": False,
            "corrections": [],
            "notes": [],
        }
    )
    # 2 진단(T15 / diagnosis): 그때의 원인 순위 · 첫 지시(세션을 만들 때 남긴 preview_shown, 없으면 지금 다시 계산)
    snap = next((e for e in events if e["type"] == "preview_shown" and "preview" in e), None)
    if snap is None:
        st0 = session.fold(k, row.symptom_group, sessions.similar_of(row), [])
        snap = {
            "_at": started,
            "preview": sessions.preview(row, c),
            "probabilities": session.probabilities(st0),
            "evidence": session.rules.evidence_counts(st0.candidates, st0.similar),
        }
    steps[1].append(
        {
            "seq": snap.get("seq", 0),
            "kind": "diagnosis",
            "at": snap.get("_at"),
            "band": f"{_hhmm(snap.get('_at'))} 그때의 원인 순위 · 첫 지시",
            "probabilities": snap["probabilities"],
            "evidence": snap["evidence"],
            "equipment_counts": snap.get("equipment_counts"),  # 리허설 2 R3 · R6: 세션 시작 때 남긴 것만(없으면 숨김)
            "equipment_total": snap.get("equipment_total"),
            "count_scope": snap.get("count_scope"),
            "count_prior": snap.get("count_prior"),
            "cause_names": screen.cause_names(k, row.symptom_group),
            "similar_cards": screen.similar_cards(c, row.similar),
            # D-663 S1: 그때 1위 후보로 확정된 지난 같은 알람 기록(시작 때 남긴 셈 칸이 있을 때만)
            "top_cards": screen.top_cards(
                c,
                alarm.equipment_id,
                alarm.symptom_group,
                alarm.occurred_at,
                snap["probabilities"],
                snap if snap.get("equipment_counts") is not None else None,
            ),
            "low_evidence": session.rules.low_evidence(sessions.similar_of(row)),
            "next": snap["preview"],
            "void": False,
            "correctable": False,
            "notable": False,
            "corrections": [],
            "notes": [],
        }
    )
    lock_e = next((e for e in events if e["type"] == "lock"), None)
    b_done = {e.get("section"): e for e in events if e["type"] == "b_checklist_done" and not e.get("void")}
    answers = {e.get("call_id"): e for e in events if e["type"] == "call_answer" and not e.get("void")}
    calls = {r.call_id: r for r in c.execute(select(m.Call.__table__).where(m.Call.session_id == row.session_id)).all()}
    restart_e, result_e = flow._restart_events(events)
    precheck_sec = next(
        (
            x["decision"]["section"]
            for x in events
            if x["type"] == "step_opened" and x["decision"].get("kind") == "precheck"
        ),
        None,
    )
    for e in events:
        t = e["type"]
        if t == "lock":
            steps[2].append(
                {
                    **_common(e, closed),
                    "kind": "lock",
                    "band": _band(e, "잠금 완료", closed_at),
                    "section": "1.2",
                    "title": k.sections["1.2"]["title"],
                    "steps": screen.procedure_steps(k, "1.2"),
                    "record": f"잠금 완료 {_hhmm(e.get('at') or e['_at'])} · 1.2절",
                }
            )
        elif t == "precheck_done":
            sec = precheck_sec
            steps[2].append(
                {
                    **_common(e, closed),
                    "kind": "precheck",
                    "band": _band(e, "사전 확인 완료", closed_at),
                    "section": sec,
                    "title": k.sections[sec]["title"] if sec in k.sections else None,
                    "grade": g.get(sec),
                    "minutes": minutes.get(sec),
                    "steps": screen.procedure_steps(k, sec) if sec in k.sections else [],
                    "values": e.get("values"),
                    "lock_at": (lock_e or {}).get("at"),
                }
            )
        elif t == "result" and e.get("value"):
            sec = e["section"]
            d = _opened_before(events, e, sec)
            shown = _shown_before(events, e, sec)
            upto = [x for x in events if x["seq"] < e["seq"]]
            state = session.fold(k, row.symptom_group, sessions.similar_of(row), sessions.fold_inputs(upto))
            bd = b_done.get(sec)
            steps[2].append(
                {
                    **_common(e, closed),
                    "kind": "check",
                    "band": _band(e, "결과 보냄", closed_at),
                    "section": sec,
                    "title": k.sections[sec]["title"],
                    "grade": d.get("grade") or g.get(sec),
                    "minutes": minutes.get(sec),
                    "steps": (shown or {}).get("steps") or screen.procedure_steps(k, sec),
                    "criterion": screen.criterion(k, sec),
                    "evidence_record_ids": screen.evidence_record_ids(k, sec, row.similar),
                    **screen.check_progress(
                        upto + [{"type": "step_opened", "seq": e["seq"], "decision": d}],
                        k,
                        row.symptom_group,
                        g,
                        state.approvals,
                    ),
                    "value": e["value"],
                    "memo": e.get("memo"),
                    "b_checklist": (bd or {}).get("checked") if bd and bd["seq"] < e["seq"] else None,
                    "b_checklist_common": sessions.checklist_is_common(sec) if bd else None,
                    "lock_at": (lock_e or {}).get("at"),
                }
            )
        elif t == "call_sent":
            r = calls.get(e.get("call_id"))
            ans = answers.get(e.get("call_id"))
            sec = e.get("section")
            kind = "correction" if e.get("corrected") else e["kind"]
            status = (
                f"답 {flow.ANSWER_LABEL.get(ans['answer'], ans['answer'])} {_hhmm(ans.get('at'))}"
                if ans
                else "답 기다림"
            )
            steps[2].append(
                {
                    **_common(e, closed),
                    "kind": "call",
                    "call_kind": kind,
                    "band": _band(e, "숙련자 인계 보냄" if kind == "handover" else "호출 보냄", closed_at),
                    "section": sec,
                    "title": k.sections[sec]["title"] if sec in k.sections else None,
                    "grade": e.get("grade"),
                    "summary": e.get("summary"),
                    "record": f"{'인계' if kind == 'handover' else '호출'} 보냄 {_hhmm(e['_at'])} · {status}",
                    "box": [
                        {k2: x.get(k2) for k2 in ("at", "section", "title", "before", "after", "reason")}
                        for x in (r.corrections if r else [])
                    ],
                }
            )
        elif t == "action_done":  # 4 조치(T15 / action): 조치 + 그 뒤 재가동 기록
            sec = e["section"]
            cause = next((cz for cz in k.causes.get(row.symptom_group, []) if cz.action_section == sec), None)
            parts = [f"조치 완료 {_hhmm(e['_at'])}"]
            if restart_e:
                parts.append(f"재가동 {_hhmm(restart_e['_at'])}")
            if result_e:
                parts.append(
                    f"{'경계 이내' if result_e['outcome'] == 'within' else '여전히 밖'} {_hhmm(result_e['_at'])}"
                )
            steps[3].append(
                {
                    **_common(e, closed),
                    "kind": "action",
                    "band": _band(e, "조치 완료", None) + (f" · {_hhmm(restart_e['_at'])} 재가동" if restart_e else ""),
                    "section": sec,
                    "title": k.sections[sec]["title"],
                    "grade": g.get(sec),
                    "cause_name": cause.name if cause else None,
                    "evidence_record_ids": screen.evidence_record_ids(k, cause.confirm_check, row.similar)
                    if cause
                    else [],
                    "restart": {
                        "section": session.RESTART_SECTION,
                        "title": k.sections[session.RESTART_SECTION]["title"],
                        "grade": g.get(session.RESTART_SECTION),
                        "at": (restart_e or {}).get("_at"),
                        "checklist": (restart_e or {}).get("checked") or [],
                    }
                    if restart_e
                    else None,
                    "record": " · ".join(parts),
                }
            )
        elif t in {"restart", "monitor_close"} and not any(x["type"] == "action_done" for x in events):
            label = "재가동" if t == "restart" else "이상 없음 · 감시 마감"
            steps[3].append(
                {
                    **_common(e, closed),
                    "kind": t,
                    "band": _band(e, label, None),
                    "section": session.RESTART_SECTION if t == "restart" else session.MONITOR_SECTION,
                    "title": k.sections[session.RESTART_SECTION if t == "restart" else session.MONITOR_SECTION][
                        "title"
                    ],
                    "record": f"{label} {_hhmm(e['_at'])}",
                    "checklist": e.get("checked") or [],
                }
            )
    total = {i: len(s) for i, s in enumerate(steps)}
    for i, recs in enumerate(steps):
        for n, rec in enumerate(recs, start=1):
            rec["position"] = f"{n} / {total[i]}"
    return {
        "session_id": row.session_id,
        "closed": closed,
        "closed_at": closed_at,
        "closed_note": "보고서를 확정한 세션이라 정정할 수 없습니다. 파트장이 반려하면 다시 정정할 수 있습니다."
        if closed
        else None,
        "steps": [{"no": i + 1, "name": n, "records": steps[i]} for i, n in enumerate(STEP_NAMES)],
    }
