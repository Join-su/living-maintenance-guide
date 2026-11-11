"""점검 바꾸기 · 예외 진행 덧층(D-556 · 약속 v9) — 순수 함수(망 · 파일 · DB 0). 사본 session · rules는 손 0(예외 갈래 custom/exception.py처럼 바깥에서).
꼴 정본 = orchestration agents/c1a15 handoff/d556/contract-v9.md 1 · 2절.
- switch_check{section = 고른 점검 | 그 세션 예외 후보 EX-…(D-580 · 예외 걸음은 custom/exception.py가 냄), from_section = 그때 추천}: 고른 점검에 결과가 아직 없으면 다음 단계 = 그 점검(등급 그대로 · 결정에 switched{from, to}).
  결과가 오면 사본 판정 그대로 이어 감(이상 → 그 원인 조치 · 정상 → 다음 점검). 건너뛴 점검 = '안 함'(결과 0 — 정상으로 세지 않음 · 차례가 오면 다시 추천).
- exception_start{mode = call_first | act_now}: 절 EX-NEW(등급 C) — 호출 후 조치(승인 전 조치 지시 0) / 바로 조치(파트장에게 '호출 없이 조치함') →
  조치 완료 → 재가동 → 재가동 값 → 보고서(원인 미상).
- 둘 다 잠금 · 사전 확인 · 확인 질문 · 범위 밖이 먼저(안전 단계 건너뜀 0)."""
from c1a15.engine import rules, session

NEW = "EX-NEW"
EX = "EX-"
SAFE = {"lock", "precheck", "confirm_question", "out_of_scope"}
TURN = {"instruct", "instruct_after_checklist", "call"}   # 점검 차례(이때만 바꾼 점검이 들어감)
MODES = ("call_first", "act_now")


class SwitchError(ValueError):
    pass


def _base(d):
    return {key: d[key] for key in ("probabilities", "evidence", "low_evidence") if key in d}


def _approved(events, sec):
    return any(e.get("section") == sec and (e["type"] == "approval" or (e["type"] == "call_answer" and e.get("answer") == "approved")) for e in events)


def _call_status(events, sec):
    calls = [e for e in events if e.get("section") == sec and e["type"] in ("call_sent", "call_wait")]
    return {"call_status": "sent" if calls[-1]["type"] == "call_sent" else "waiting"} if calls else {}


def _switched(d, k, group, events, grades, relation=None):
    sw = [e for e in events if e["type"] == "switch_check"]
    checks = {c.section_id: c for c in k.checks.get(group, [])}
    for e in sw:
        if e.get("section") not in checks and not (relation and str(e.get("section")).startswith(EX)):
            raise SwitchError(f"바꾼 점검이 그 증상군 점검 절이 아님: {e.get('section')}")
    if not sw or d["kind"] not in TURN or sw[-1]["section"].startswith(EX):
        return None
    last = sw[-1]
    s = last["section"]
    if any(e["type"] == "result" and e.get("section") == s and e["seq"] > last["seq"] for e in events):
        return None   # 고른 점검 결과가 옴 → 사본 판정 그대로 이어 감
    approvals = {s} if _approved(events, s) else set()
    out = {**_base(d), "kind": rules.grade_decision(rules.effective_grade(s, grades, approvals)), "section": s, "cause": checks[s].cause,
           "grade": grades.get(s), "switched": {"from": last.get("from_section"), "to": s}}
    return {**out, "reason": "switched_check", **_call_status(events, s)} if out["kind"] == "call" else out


def _progress(d, events, grades):
    st = [e for e in events if e["type"] == "exception_start"]
    if not st:
        return None
    mode = st[-1].get("mode")
    if mode not in MODES:
        raise SwitchError(f"모르는 예외 진행 방식: {mode}")
    head = {"section": NEW, "grade": "C", "reason": "exception_progress", "exception_progress": {"mode": mode}}
    mine = [e for e in events if e.get("section") == NEW]
    if not any(e["type"] == "action_done" for e in mine):
        approved = _approved(mine, NEW)
        if mode == "call_first" and not approved:
            return {**_base(d), "kind": "call", **head, **_call_status(mine, NEW)}
        out = {**_base(d), "kind": "action", **head, "stage": "exception_progress"}
        return {**out, "lead_notice": "acted_without_call"} if mode == "act_now" and not approved else out
    seen = {e["type"]: e for e in events}
    confirmed = False
    for e in events:   # D-560(정답지 20쪽): 보통 세션처럼 차례대로 — 확정 → 닫힘 · 파트장 반려(report_rejected) → 보고서 다시 열림
        if e["type"] in ("report_confirm", "report_rejected"):
            confirmed = e["type"] == "report_confirm"
    if confirmed:
        return {**_base(d), "kind": "closed"}
    if "restart" not in seen:
        return {**_base(d), "kind": "restart_check", "section": session.RESTART_SECTION, "grade": grades.get(session.RESTART_SECTION), "cause": None, "outcome": "unknown"}
    if "restart_result" not in seen:
        return {**_base(d), "kind": "restart_value", "section": session.RESTART_SECTION, "cause": None, "outcome": "unknown"}
    if seen["restart_result"].get("outcome") == "outside":
        return {**_base(d), "kind": "handover", "reason": "restart_outside"}
    return {**_base(d), "kind": "report", "outcome": "unknown", "cause": None}


def apply(d, k, request):
    """d = v8 판정(예외 갈래까지) → 점검 바꾸기 · 예외 진행을 덧댄 다음 단계 하나. 새 사건이 없으면 d 그대로."""
    events = [e for e in request.get("events", []) if not e.get("void")]
    grades = request.get("grades", {})
    if not any(e["type"] in ("switch_check", "exception_start") for e in events):
        return d
    for e in events:   # 꼴은 안전 단계 앞에서도 봄
        if e["type"] == "exception_start" and e.get("mode") not in MODES:
            raise SwitchError(f"모르는 예외 진행 방식: {e.get('mode')}")
    if d["kind"] in SAFE:
        _switched(d, k, request["group"], events, grades, request.get("exception_relation"))   # 꼴 검사만
        return d
    return _progress(d, events, grades) or _switched(d, k, request["group"], events, grades, request.get("exception_relation")) or d
