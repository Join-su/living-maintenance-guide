"""세션 상태 = 업무 기록(event) 접기(fold). 판정 · 정정 다시 판정은 같은 함수를 다시 적용한다(R1 · R4 · R5 · R7 · R13).

event는 API가 append-only 업무 기록으로 저장한 사실이다. 이 모듈은 저장하지 않는다.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from c1a15.engine import rules
from c1a15.engine.knowledge import ManualKnowledge

RESULT_VALUES = {"정상", "이상", "확인 불가"}


class EventRejected(ValueError):
    """서버가 거부해야 하는 입력(잠금 전 지시 밖 결과 · B 체크리스트 전 결과 · 정정 불가 등)."""


@dataclass
class SessionState:
    group: str
    similar: list[rules.Similar]
    candidates: list[str]
    locked: bool = False
    precheck_done: bool = False
    confirmed: str | None = None
    done: set[str] = field(default_factory=set)
    unable: dict[str, int] = field(default_factory=dict)
    approvals: set[str] = field(default_factory=set)
    b_checklist_done: set[str] = field(default_factory=set)
    pending: dict | None = None
    handed_over: bool = False
    handover_reason: str = "out_of_scope_finding"
    results: list[dict] = field(default_factory=list)
    # S-03(P-09): 조치 · 재가동 · 마감 · 호출 · 보고서
    actions_done: set[str] = field(default_factory=set)
    restarted: bool = False
    restart_outcome: str | None = None  # within | outside
    monitor_closed: bool = False
    call: dict | None = None  # 지금 단계의 호출: {status: waiting | sent, kind, section}
    call_answer: dict | None = None  # 파트장 답(S-04 P-11이 쓴다): {answer: approved | daytime | direct, section}
    handover_sent: bool = False
    report_confirmed: bool = False
    report_rejected_seq: int | None = None  # 마지막 반려 기록 seq(FLOW-06: 반려 → 재개 · 수정 → 다시 확정)
    # S-05(P-14): 조치 뒤 결과 정정 → 다시 판정 대신 정정 호출(AC-47-C1, T10 / summary_corrected)
    correction_call: dict | None = None


def start(k: ManualKnowledge, group: str, similar: Iterable[rules.Similar]) -> SessionState:
    return SessionState(group=group, similar=list(similar), candidates=[c.code for c in rules.ordered_causes(k, group)])


@dataclass(frozen=True)
class Scoring:
    """R4 셈 매개변수(D-476 Pack 옵션) — 기본 = 지금 셈. 안전 규칙 · 근거 적음은 여기 없음."""

    alpha: float = rules.ALPHA
    rule: str = "sum"
    near_min: float = 0.8
    manual: Mapping[str, float] | None = None
    manual_weight: float = 0.0


def probabilities(state: SessionState, scoring: Scoring | None = None) -> dict[str, int]:
    s = scoring or Scoring()
    return rules.round_to_100(
        rules.cause_scores(state.candidates, state.similar, s.alpha, s.rule, s.near_min, s.manual, s.manual_weight)
    )


def _apply_result(state: SessionState, k: ManualKnowledge, section_id: str, value: str) -> None:
    """R4 갱신: 확정형 '이상' → 원인 확정, 선별형 '이상' → 그 원인만, '정상' → 그 원인 제외, '확인 불가' → 변경 없음."""
    check = k.check(section_id)
    if value == "확인 불가":
        state.unable[section_id] = state.unable.get(section_id, 0) + 1
        return
    state.done.add(section_id)
    if value == "정상":
        state.candidates = [c for c in state.candidates if c != check.cause]
    elif check.kind == "confirm":
        state.confirmed = check.cause
    else:
        state.candidates = [check.cause]


def apply(state: SessionState, k: ManualKnowledge, event: dict) -> SessionState:
    t = event["type"]
    if t == "lock":
        state.locked = True
    elif t == "equipment_change":  # R1: 다른 설비로 옮기면 잠금을 다시 확인
        state.locked = False
    elif t == "precheck_done":
        state.precheck_done = True
    elif t == "approval":  # 파트장 '승인 후 진행'(그 C 작업 하나)
        state.approvals.add(event["section"])
    elif t == "b_checklist_done":
        state.b_checklist_done.add(event["section"])
    elif t == "result":
        if "judged_value" in event:  # 다시 판정하지 않는 정정(AC-47): 판정은 정정 전 값 그대로
            event = {**event, "value": event["judged_value"]}
        state.results.append(event)
        signal = event.get("signal") or {}
        if signal.get("out_of_scope_finding"):
            state.pending = {
                "kind": "out_of_scope",
                "section": event["section"],
                "value": event.get("value"),
                "quote": signal.get("quote"),
            }
        elif signal.get("conflicts_with_button") or event.get("value") is None:
            state.pending = {
                "kind": "confirm_question",
                "section": event["section"],
                "interpretation": signal.get("interpretation"),
            }
        else:
            _apply_result(state, k, event["section"], event["value"])
    elif t == "confirm_answer":  # R7: '네' → 해석대로, '아니요' → 반영 안 함
        pending, state.pending = state.pending, None
        if event["yes"] and pending and pending.get("interpretation") in {"정상", "이상"}:
            _apply_result(state, k, pending["section"], pending["interpretation"])
    elif t == "action_done":  # R2: A 조치는 바로, B 조치는 시작 전 체크리스트 뒤(API가 거부 규칙을 먼저 본다)
        state.actions_done.add(event["section"])
    elif t == "restart":  # 6.1 재가동(B — 체크리스트 뒤)
        state.restarted = True
    elif t == "restart_result":  # 재가동 후 값: 정비자가 고름
        state.restart_outcome = event["outcome"]
    elif t == "monitor_close":  # 6.2 이상 없음 · 감시 마감
        state.monitor_closed = True
    elif t == "call_wait":  # '호출하지 않고 대기'
        kind = "correction" if event.get("corrected") else "approval"
        state.call = {"status": "waiting", "kind": kind, "section": event.get("section")}
    elif t == "call_sent":
        kind = "correction" if event.get("corrected") else event["kind"]
        state.call = {"status": "sent", "kind": kind, "section": event.get("section")}
        if event["kind"] == "handover":
            state.handover_sent = True
    elif t == "call_answer" and event.get("corrected"):  # 정정 호출의 답(결정 012 ② · 019 ①)
        state.correction_call, state.call = None, None
        if event["answer"] != "approved":  # 주간 처리 · 직접 확인 → 보고서(확정 원인 '원인 미상', 조치는 그대로)
            state.call_answer = {"answer": event["answer"], "section": event.get("section"), "corrected": True}
        # 승인 후 진행 → 새 작업 없이 정정 때 하던 단계로 이어서 한다(승인 목록에 더하지 않음)
    elif t == "call_answer":  # 1.4: 승인 후 진행 · 주간 처리 · 직접 확인
        state.call_answer = {"answer": event["answer"], "section": event.get("section")}
        if event["answer"] == "approved" and event.get("section"):
            state.approvals.add(event["section"])
            state.call = None
    elif t == "correction" and event.get("mode") == "call_with_correction":
        state.correction_call = {
            "section": event.get("section"),
            "before": event.get("before"),
            "after": event.get("after"),
            "seq": event.get("seq"),
        }
    elif t == "report_confirm":
        state.report_confirmed = True
    elif t == "report_rejected":  # FLOW-06 · AC-11: 파트장 반려 → 세션이 다시 열려 보고서를 고치거나 이어서 함
        state.report_confirmed, state.report_rejected_seq = False, event.get("seq")
    elif t == "handover_request":  # AC-31-C2: 문장 생성 반복 실패 → 정비자가 숙련자 인계를 고름
        state.handed_over, state.handover_reason = True, event.get("reason", "llm_failure")
    elif t == "out_of_scope_choice":  # R7(c): 정비자가 인계 또는 계속을 고름 — 순위 변경 · 인계는 확인 뒤에만
        pending, state.pending = state.pending, None
        if event["choice"] == "handover":
            state.handed_over = True
        elif pending and pending.get("value") in RESULT_VALUES:
            _apply_result(state, k, pending["section"], pending["value"])
    return state


def fold(k: ManualKnowledge, group: str, similar: Iterable[rules.Similar], events: Iterable[dict]) -> SessionState:
    state = start(k, group, similar)
    for event in events:
        if not event.get("void"):
            apply(state, k, event)
    return state


RESTART_SECTION, MONITOR_SECTION = "6.1", "6.2"


def decide(
    state: SessionState,
    k: ManualKnowledge,
    grades: Mapping[str, str],
    minutes: Mapping[str, int],
    threshold: int = rules.CALL_THRESHOLD,
    scoring: Scoring | None = None,
) -> dict:
    """다음 단계. R5 판정 뒤에 S-03 단계(조치 완료 → 재가동 → 재가동 후 값 → 마감 · 인계 · 보고서)를 잇는다."""
    d = _decide_r5(state, k, grades, minutes, threshold, scoring)
    base = {key: d[key] for key in ("probabilities", "evidence", "low_evidence")}
    if d["kind"] in {"lock", "confirm_question", "out_of_scope"}:
        return d
    if state.report_confirmed:
        return {**base, "kind": "closed"}
    if state.correction_call:  # AC-47-C1: 조치 뒤 정정 → 다시 판정하지 않고 정정 내용이 든 파트장 호출 요약
        cc = state.correction_call
        out = {**base, "kind": "call", "corrected": True, "section": cc["section"], "reason": "corrected_after_action"}
        if state.call and state.call.get("kind") == "correction":
            out["call_status"] = state.call["status"]
        return out
    if state.handover_sent:  # 숙련자 인계를 보냄 → 파트장 답(주간 숙련자 배정 · 직접 확인)이 오면 보고서(결정 018 ④)
        if state.call_answer:
            return {**base, "kind": "report", "outcome": "unknown"}
        reason = (
            "restart_outside"
            if state.restart_outcome == "outside"
            else d.get("reason")
            if d["kind"] == "handover"
            else state.handover_reason
        )
        return {**base, "kind": "handover", "reason": reason, "call_status": "sent"}
    if state.restart_outcome == "outside":  # AC-21-C3 · 6.2-3: 재가동 뒤에도 밖 → 숙련자 인계
        return {**base, "kind": "handover", "reason": "restart_outside"}
    answer = state.call_answer or {}
    if answer.get("answer") == "daytime":  # 주간 처리(저부하 감시 운전) → 보고서 '미확인 · 요청'
        return {**base, "kind": "report", "outcome": "daytime", "section": answer.get("section")}
    if answer.get("answer") == "direct":  # 결정 018 ③: 파트장이 확인을 끝내고 처리 메모와 함께 답 → 보고서
        return {**base, "kind": "report", "outcome": "direct", "section": answer.get("section")}
    restart_now = (d["kind"] in {"action", "action_after_checklist"} and d["section"] in state.actions_done) or d[
        "kind"
    ] == "restart_check"
    if restart_now:
        outcome = "confirmed" if state.confirmed else "monitor"
        if not state.restarted:
            return {
                **base,
                "kind": "restart_check",
                "section": RESTART_SECTION,
                "grade": grades[RESTART_SECTION],
                "cause": state.confirmed,
                "outcome": outcome,
            }
        if state.restart_outcome is None:
            return {
                **base,
                "kind": "restart_value",
                "section": RESTART_SECTION,
                "cause": state.confirmed,
                "outcome": outcome,
            }
        if outcome == "monitor" and not state.monitor_closed:  # 후보가 모두 빠지고 경계 이내 → 6.2(AC-22)
            return {**base, "kind": "monitor_close", "section": MONITOR_SECTION, "grade": grades[MONITOR_SECTION]}
        return {**base, "kind": "report", "outcome": outcome, "cause": state.confirmed}
    if d["kind"] in {"call", "handover"} and state.call and state.call.get("section") == d.get("section"):
        d = {**d, "call_status": state.call["status"]}
    return d


def _decide_r5(
    state: SessionState,
    k: ManualKnowledge,
    grades: Mapping[str, str],
    minutes: Mapping[str, int],
    threshold: int = rules.CALL_THRESHOLD,
    scoring: Scoring | None = None,
) -> dict:
    """R5 다음 단계. 등급은 판정 시점의 등급표(R13). 지시 내용은 잠금 뒤에만(R1)."""
    probs = probabilities(state, scoring)
    base = {
        "probabilities": probs,
        "evidence": rules.evidence_counts(state.candidates, state.similar),
        "low_evidence": rules.low_evidence(state.similar),
    }
    if not state.locked:
        return {**base, "kind": "lock", "section": "1.2"}
    if state.pending:
        return {**base, "kind": state.pending["kind"], **state.pending}
    if state.handed_over:
        return {**base, "kind": "handover", "reason": state.handover_reason}
    if state.confirmed:
        cause = k.cause(state.confirmed)
        grade = rules.effective_grade(cause.action_section, grades, state.approvals)
        decision = rules.grade_decision(grade)
        kind = {"instruct": "action", "instruct_after_checklist": "action_after_checklist"}.get(decision, "call")
        return {
            **base,
            "kind": kind,
            "section": cause.action_section,
            "cause": cause.code,
            "grade": grades[cause.action_section],
            "stage": "action",
        }
    if not state.precheck_done:
        return {**base, "kind": "precheck", "section": k.prechecks[state.group]}
    if not state.candidates:
        return {**base, "kind": "restart_check"}
    order = [c.code for c in rules.ordered_causes(k, state.group)]
    top = rules.top_cause(probs, order)
    top_open = rules.cause_checks_open(k, state.group, top, state.done, state.unable)
    top_instructable = [
        c for c in top_open if rules.effective_grade(c.section_id, grades, state.approvals) in rules.INSTRUCTABLE
    ]
    if probs[top] >= threshold and top_open and not top_instructable:
        c = top_open[0]
        return {
            **base,
            "kind": "call",
            "section": c.section_id,
            "cause": top,
            "grade": grades[c.section_id],
            "reason": "top_cause_cd_only",
        }
    picks = rules.selectable_checks(k, state.group, probs, state.done, state.unable, grades, minutes, state.approvals)
    answer = state.call_answer or {}
    approved = answer.get("section") if answer.get("answer") == "approved" else None
    # 명세 S4-4 · T10 ans_approved: '승인 후 진행' 답이 온 그 C 점검이 바로 지시로 열린다(한 번 하면 다시 R5-3)
    pick = next((p for p in picks if p.check.section_id == approved), None) or rules.best_pick(picks, state.unable)
    if pick:
        alternatives = [
            {"section": c.section_id, "grade": grades[c.section_id]}
            for c in k.checks[state.group]
            if c.section_id != pick.check.section_id and c.section_id not in state.done and c.cause in probs
        ]
        kind = "instruct" if pick.grade == "A" else "instruct_after_checklist"
        return {
            **base,
            "kind": kind,
            "section": pick.check.section_id,
            "cause": pick.check.cause,
            "grade": grades[pick.check.section_id],  # 화면 등급 배지 = 등급표(승인된 C도 C), 지시 여부는 kind
            "reason_facts": {
                "probability": pick.probability,
                "minutes": pick.minutes,
                "grade": pick.grade,
                "alternatives": alternatives,
            },
            "unable_before": state.unable.get(pick.check.section_id, 0),
        }
    for cause in sorted(probs, key=lambda c: (-probs[c], order.index(c))):
        open_checks = rules.cause_checks_open(k, state.group, cause, state.done, state.unable)
        if open_checks:
            c = open_checks[0]
            return {
                **base,
                "kind": "call",
                "section": c.section_id,
                "cause": cause,
                "grade": grades[c.section_id],
                "reason": "no_ab_left",
            }
    return {**base, "kind": "handover", "reason": "no_alternative"}


def validate_result(state: SessionState, decision: dict, event: dict) -> None:
    """서버 거부 규칙: 잠금 전(R1) · 지시되지 않은 점검(AC-35) · B 체크리스트 전(AC-43) 결과는 받지 않는다."""
    if not state.locked:
        raise EventRejected("not_locked")
    if decision.get("kind") not in {"instruct", "instruct_after_checklist"} or event.get("section") != decision.get(
        "section"
    ):
        raise EventRejected("not_instructed")
    if decision["kind"] == "instruct_after_checklist" and event["section"] not in state.b_checklist_done:
        raise EventRejected("b_checklist_required")
    if event.get("value") is not None and event["value"] not in RESULT_VALUES:
        raise EventRejected("invalid_value")


CORRECTABLE_TYPES = {"result", "precheck_done"}  # AC-45-C1: 정비자가 입력해 제출한 결과 · 메모 · 사전 확인 측정값
NOTE_TYPES = {  # AC-48-C1 · CO-36: 정정하지 않는 기록 — '메모 덧붙이기'만
    "lock",
    "b_checklist_done",
    "confirm_answer",
    "out_of_scope_choice",
    "action_done",
    "restart",
    "restart_result",
    "monitor_close",
    "call_sent",
    "call_wait",
    "handover_request",
}


def correction_mode(events: list[dict], index: int, *, value_changed: bool) -> dict:
    """정정을 어떻게 반영할지(AC-46 · AC-47 · 결정 019 ② ③). events는 정정을 적용한 업무 기록(sessions.logical).

    - 결과 값을 바꾸지 않음(메모 · 측정값만) → memo_only: 다음 단계 그대로(AC-46-C5)
    - 대상 뒤에 답이 아직 없는 보낸 호출(파트장 호출 · 숙련자 인계 · 정정 호출) → attach_to_call:
      다시 판정 · 새 호출 없음(AC-47-C2 · 019 ②)
    - 대상 뒤에 조치 완료 → call_with_correction: 다시 판정 대신 정정 호출 요약(AC-47-C1)
    - 대상 뒤에 답이 온 호출 → attach_answered: 다시 판정 · 다시 답 받기 없음(019 ③)
    - 그 밖 → rejudge: 그 점검부터 다시 판정, 뒤 기록은 '정정으로 무효'(AC-46-C1 · C2)
    메모 · 측정값만 바꿨어도 뒤에 보낸 호출이 있으면 그 호출에 정정 내용을 붙인다.
    """
    target = events[index]
    if target.get("type") not in CORRECTABLE_TYPES or target.get("void"):
        raise EventRejected("not_correctable")
    later = [e for e in events[index + 1 :] if not e.get("void")]
    sent = [e for e in later if e["type"] == "call_sent"]
    answered = {e.get("call_id") for e in later if e["type"] == "call_answer"}
    open_call = next((e for e in reversed(sent) if e.get("call_id") not in answered), None)
    if open_call:
        return {"mode": "attach_to_call", "call_id": open_call.get("call_id")}
    if not value_changed:
        return {"mode": "memo_only"}
    if any(e["type"] == "action_done" for e in later):
        return {"mode": "call_with_correction"}
    if sent:
        return {"mode": "attach_answered", "call_id": sent[-1].get("call_id")}
    return {"mode": "rejudge"}
