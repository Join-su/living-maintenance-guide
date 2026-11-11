"""P-03 판정 규칙 시험: R5 계산 예시(AC-03) · 결과별 갱신(AC-04) · 합 100 property · 등급 × 상태 표 · 음성 시험."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from c1a15.engine import knowledge, rules, session
from c1a15.loader import core


@pytest.fixture(scope="module")
def k(data_dir_module):
    return knowledge.from_sections(core.read_manual(data_dir_module))


@pytest.fixture(scope="module")
def grades(data_dir_module):
    rows = core.grade_rows(core.read_manual(data_dir_module))
    return {r["section_id"]: r["grade"] for r in rows}, {r["section_id"]: r["minutes"] for r in rows}


R5_SIMILAR = [
    rules.Similar("H-a", "CL-1", 0.96),
    rules.Similar("H-b", "CL-1", 0.91),
    rules.Similar("H-c", "CL-2", 0.84),
]


def r5_session(k, events=()):
    return session.fold(k, "CL", R5_SIMILAR, [{"type": "lock"}, {"type": "precheck_done"}, *events])


def test_knowledge_parses_manual(k):
    assert [c.code for c in k.causes["CL"]] == ["CL-1", "CL-2", "CL-3", "CL-4"]
    assert k.check("3.5").kind == "screen" and k.check("5.5").kind == "screen" and k.check("4.3").kind == "confirm"
    assert k.cause("PW-2").action_section == "7.1" and k.cause("CL-1").action_section == "4.3.2"
    assert k.prechecks == {"TW": "2.2", "PW": "3.2", "CL": "4.2", "LD": "5.2"}
    assert k.check("2.5").checklist == (
        "주변 공구·이물질을 치웠다",
        "커버가 모두 닫혔다",
        "시험 가공 프로그램을 선택했다",
    )


def test_r5_worked_example(k, grades):
    g, m = grades
    s = r5_session(k)
    assert session.probabilities(s) == {"CL-1": 50, "CL-2": 28, "CL-3": 11, "CL-4": 11}  # AC-03-C1
    d = session.decide(s, k, g, m)
    assert d["kind"] == "instruct" and d["section"] == "4.3"  # AC-03-C2
    s2 = r5_session(k, [{"type": "result", "section": "4.3", "value": "정상"}])
    assert session.probabilities(s2) == {"CL-2": 57, "CL-3": 22, "CL-4": 21}  # AC-03-C3
    d2 = session.decide(s2, k, g, m)
    assert d2["kind"] == "call" and d2["section"] == "4.4" and d2["reason"] == "top_cause_cd_only"


def test_lock_precedes_instructions_and_equipment_change_resets(k, grades):
    g, m = grades
    s = session.fold(k, "CL", R5_SIMILAR, [])
    assert session.decide(s, k, g, m)["kind"] == "lock"  # AC-01-C1: 지시 내용 없음
    with pytest.raises(session.EventRejected, match="not_locked"):
        session.validate_result(
            s, {"kind": "instruct", "section": "4.3"}, {"type": "result", "section": "4.3", "value": "정상"}
        )
    s = session.fold(k, "CL", R5_SIMILAR, [{"type": "lock"}, {"type": "equipment_change"}])
    assert session.decide(s, k, g, m)["kind"] == "lock"  # AC-01-C3


def test_result_rules(k, grades):
    g, m = grades
    confirmed = r5_session(k, [{"type": "result", "section": "4.3", "value": "이상"}])
    assert confirmed.confirmed == "CL-1"
    d = session.decide(confirmed, k, g, m)
    assert d["kind"] == "action" and d["section"] == "4.3.2"  # A 조치는 바로
    unable = r5_session(k, [{"type": "result", "section": "4.3", "value": "확인 불가"}])
    assert session.probabilities(unable) == session.probabilities(
        r5_session(k)
    )  # 확인 불가 → 그대로(AC-04-C1 · AC-19-C1)
    d = session.decide(unable, k, g, m)
    assert d["kind"] == "instruct" and d["section"] != "4.3"  # 다른 점검 제안(AC-19-C1)
    pw = session.fold(
        k,
        "PW",
        [],
        [{"type": "lock"}, {"type": "precheck_done"}, {"type": "result", "section": "3.5", "value": "이상"}],
    )
    assert pw.candidates == ["PW-2"]  # 선별형 이상 → 그 원인만
    assert session.decide(pw, k, g, m)["kind"] == "call"  # 3.6은 C


def test_unable_twice_excludes_and_handover_without_alternative(k, grades):
    g, m = grades
    events = []
    for _ in range(2):
        events.append({"type": "result", "section": "2.3", "value": "확인 불가"})
    s = session.fold(k, "TW", [], [{"type": "lock"}, {"type": "precheck_done"}, *events])
    picks = rules.selectable_checks(k, "TW", session.probabilities(s), s.done, s.unable, g, m, s.approvals)
    assert "2.3" not in {p.check.section_id for p in picks}  # AC-19-C2
    only = {"2.3": "A", "2.4": "A", "2.5": "A", "2.6": "A"}
    s = session.fold(
        k,
        "TW",
        [],
        [{"type": "lock"}, {"type": "precheck_done"}]
        + [{"type": "result", "section": x, "value": "정상"} for x in ("2.4", "2.5", "2.6")]
        + events,
    )
    assert session.decide(s, k, {**g, **only}, m) == {
        **session.decide(s, k, {**g, **only}, m),
        "kind": "handover",
    }  # AC-19-C3


def test_grade_table_driven(k, grades):
    """R2 · AC-02: 승인 없는 C와 모든 D는 지시하지 않는다.

    승인은 그 C 하나만, D가 되면 무효, B가 되면 체크리스트 먼저."""
    g, m = grades
    assert [rules.grade_decision(x) for x in "ABCD"] == ["instruct", "instruct_after_checklist", "call", "call"]
    assert rules.effective_grade("4.4", g, {"4.4"}) == "A" and rules.effective_grade("4.4", g, set()) == "C"
    assert rules.effective_grade("4.4", {**g, "4.4": "D"}, {"4.4"}) == "D"
    assert rules.effective_grade("4.4", {**g, "4.4": "B"}, {"4.4"}) == "B"
    s = r5_session(k, [{"type": "result", "section": "4.3", "value": "정상"}, {"type": "approval", "section": "4.4"}])
    d = session.decide(s, k, g, m)
    assert d["kind"] == "instruct" and d["section"] == "4.4"  # AC-08 판정 부분
    d = session.decide(s, k, {**g, "4.4": "D"}, m)
    assert d["kind"] == "call"


def test_b_check_requires_checklist(k, grades):
    g, m = grades
    gb = {**g, "4.3": "B"}
    s = r5_session(k)
    d = session.decide(s, k, gb, m)
    assert d["kind"] == "instruct_after_checklist" and d["section"] == "4.3"  # AC-43-C1
    with pytest.raises(session.EventRejected, match="b_checklist_required"):
        session.validate_result(s, d, {"type": "result", "section": "4.3", "value": "정상"})
    s.b_checklist_done.add("4.3")
    session.validate_result(s, d, {"type": "result", "section": "4.3", "value": "정상"})


def test_not_instructed_result_rejected(k, grades):
    g, m = grades
    s = r5_session(k)
    d = session.decide(s, k, g, m)
    with pytest.raises(session.EventRejected, match="not_instructed"):  # AC-35-C1
        session.validate_result(s, d, {"type": "result", "section": "4.5", "value": "정상"})


def test_memo_signals_r7(k):
    q = r5_session(
        k,
        [
            {
                "type": "result",
                "section": "4.3",
                "value": "정상",
                "signal": {"interpretation": "이상", "conflicts_with_button": True},
            }
        ],
    )
    assert q.pending["kind"] == "confirm_question" and session.probabilities(q)["CL-1"] == 50
    no = r5_session(
        k,
        [
            {
                "type": "result",
                "section": "4.3",
                "value": "정상",
                "signal": {"interpretation": "이상", "conflicts_with_button": True},
            },
            {"type": "confirm_answer", "yes": False},
        ],
    )
    assert session.probabilities(no) == session.probabilities(r5_session(k)) and "4.3" not in no.done  # AC-05-C1
    yes = r5_session(
        k,
        [
            {
                "type": "result",
                "section": "4.3",
                "value": "정상",
                "signal": {"interpretation": "이상", "conflicts_with_button": True},
            },
            {"type": "confirm_answer", "yes": True},
        ],
    )
    assert yes.confirmed == "CL-1"  # AC-05-C2
    oos = r5_session(
        k,
        [
            {
                "type": "result",
                "section": "4.3",
                "value": "정상",
                "signal": {"out_of_scope_finding": True, "quote": "타는 냄새"},
            }
        ],
    )
    assert (
        oos.pending["kind"] == "out_of_scope" and not oos.handed_over and session.probabilities(oos)["CL-1"] == 50
    )  # AC-20


def test_low_evidence_and_assignment():
    assert rules.low_evidence(R5_SIMILAR[:2]) and not rules.low_evidence(R5_SIMILAR)  # AC-29 판정 부분
    assign = {"M-01": "tech.kim"}
    assert rules.assign("M-01", assign, {"tech.kim": 3, "tech.lee": 0}) == "tech.kim"  # AC-28-C1
    assert rules.assign("M-01", assign, {"tech.lee": 1, "tech.choi": 1}) == "tech.choi"  # AC-28-C2(같으면 ID 순)
    assert rules.assign("M-01", assign, {}) is None  # AC-28-C3


def test_correction_rejudge_and_after_action(k, grades):
    """정정 반영 방식(AC-46 · AC-47 · 결정 019 ② ③): 정정은 덧붙임 기록이고 적용은 sessions.logical이 한다."""
    from c1a15.api.sessions import logical

    events = [
        {"seq": 1, "type": "lock", "_at": "t1"},
        {"seq": 2, "type": "precheck_done", "_at": "t2"},
        {"seq": 3, "type": "result", "section": "4.3", "value": "정상", "memo": "깨끗", "_at": "t3"},
        {"seq": 4, "type": "result", "section": "4.5", "value": "정상", "_at": "t4"},
    ]
    assert session.correction_mode(events, 2, value_changed=True)["mode"] == "rejudge"
    fixed = logical(
        events
        + [
            {
                "seq": 5,
                "type": "correction",
                "target_seq": 3,
                "mode": "rejudge",
                "_at": "t5",
                "before": {"value": "정상", "memo": "깨끗"},
                "after": {"value": "이상"},
                "reason": "r",
            }
        ]
    )
    assert (
        fixed[3]["void"] and fixed[2]["value"] == "이상" and fixed[2]["corrected_from"]["count"] == 1
    )  # AC-46-C2 · AC-45-C4
    s = session.fold(k, "CL", R5_SIMILAR, fixed)
    assert s.confirmed == "CL-1"  # AC-46-C1 R4 · R5 다시 적용(뒤 기록 4.5는 접지 않음)
    assert session.correction_mode(events, 2, value_changed=False)["mode"] == "memo_only"  # AC-46-C5
    after_action = events + [{"seq": 5, "type": "action_done", "section": "4.5.2"}]
    assert session.correction_mode(after_action, 2, value_changed=True)["mode"] == "call_with_correction"  # AC-47-C1
    sent = events + [{"seq": 5, "type": "call_sent", "kind": "handover", "call_id": 7}]
    assert session.correction_mode(sent, 2, value_changed=True) == {"mode": "attach_to_call", "call_id": 7}  # 019 ②
    answered = sent + [{"seq": 6, "type": "call_answer", "call_id": 7, "answer": "assign"}]
    assert session.correction_mode(answered, 2, value_changed=True)["mode"] == "attach_answered"  # 019 ③
    with pytest.raises(session.EventRejected):
        session.correction_mode(events, 0, value_changed=True)  # 잠금 기록은 정정 불가(AC-48-C1)
    with pytest.raises(session.EventRejected):
        session.correction_mode(fixed, 3, value_changed=True)  # 정정으로 무효가 된 기록은 정정 불가


@given(
    st.lists(st.floats(min_value=0, max_value=1, allow_nan=False), min_size=0, max_size=10),
    st.integers(min_value=1, max_value=5),
)
def test_probabilities_sum_to_100(scores, n_causes):
    causes = [f"X-{i}" for i in range(n_causes)]
    similar = [rules.Similar(f"r{i}", causes[i % n_causes], s) for i, s in enumerate(scores)]
    probs = rules.round_to_100(rules.cause_scores(causes, similar))
    assert sum(probs.values()) == 100 and all(v >= 0 for v in probs.values())  # AC-04-C2


@given(st.integers(min_value=1, max_value=3))
def test_renormalize_after_removal(drop):
    causes = ["X-1", "X-2", "X-3", "X-4"]
    remaining = causes[drop:]
    assert sum(rules.round_to_100(rules.cause_scores(remaining, [])).values()) == 100


def test_negative_broken_grade_check_is_caught(k, grades, monkeypatch):
    """음성 시험(EP-TEST-003): 등급 검사를 지운 결함 함수에서는 R5 예시의 '4.4(C) → 호출' 판정이 깨져야 한다."""
    g, m = grades

    def r5_call_holds() -> bool:
        s = r5_session(k, [{"type": "result", "section": "4.3", "value": "정상"}])
        d = session.decide(s, k, g, m)
        return d["kind"] == "call" and d["section"] == "4.4"

    assert r5_call_holds()
    monkeypatch.setattr(rules, "INSTRUCTABLE", {"A", "B", "C", "D"})
    assert not r5_call_holds()


def test_all_candidates_excluded_goes_to_restart_check(k, grades):
    """AC-22(판정 부분): 후보가 모두 빠지면 재가동 확인으로. 감시 마감 · 보고서는 P-09 · P-10."""
    g, m = grades
    events = [{"type": "result", "section": x, "value": "정상"} for x in ("4.3", "4.5", "4.6")]
    events += [{"type": "approval", "section": "4.4"}, {"type": "result", "section": "4.4", "value": "정상"}]
    s = r5_session(k, events)
    assert s.candidates == [] and session.decide(s, k, g, m)["kind"] == "restart_check"


def test_grade_change_applies_from_next_judgment(k, grades):
    """AC-25-C1(판정 부분): 같은 세션 상태라도 판정 시점의 등급표로 다음 판정. 열린 지시 유지(C4)는 test_api."""
    g, m = grades
    s = r5_session(k)
    assert session.decide(s, k, g, m)["section"] == "4.3"
    d = session.decide(s, k, {**g, "4.3": "C"}, m)
    assert d["kind"] == "call" and d["section"] == "4.3" and d["reason"] == "top_cause_cd_only"


def test_approved_c_check_results_follow_rules(k, grades):
    """AC-42(판정 부분): 승인으로 열린 C 확정형 점검 4.4의 결과 뒤."""
    g, m = grades
    opened = [{"type": "result", "section": "4.3", "value": "정상"}, {"type": "approval", "section": "4.4"}]
    bad = r5_session(k, [*opened, {"type": "result", "section": "4.4", "value": "이상"}])
    assert bad.confirmed == "CL-2"  # C1: 그 원인 확정
    act = k.cause("CL-2").action_section
    d = session.decide(bad, k, {**g, act: "C"}, m)
    assert d["kind"] == "call" and d["stage"] == "action"  # C1: 조치가 C면 다시 호출
    assert session.decide(bad, k, {**g, act: "A"}, m)["kind"] == "action"
    ok = r5_session(k, [*opened, {"type": "result", "section": "4.4", "value": "정상"}])
    d = session.decide(ok, k, g, m)
    assert "CL-2" not in ok.candidates and d["kind"] in {"instruct", "instruct_after_checklist", "call"}  # C2
    assert d.get("section") in {"4.5", "4.6"}


def test_out_of_scope_choice_handover_or_continue(k, grades):
    """AC-20(판정 부분): 범위 밖 발견 뒤 정비자 선택 — 인계면 인계, 계속이면 그 결과를 반영해 이어 감."""
    g, m = grades
    found = [
        {
            "type": "result",
            "section": "4.3",
            "value": "정상",
            "signal": {"out_of_scope_finding": True, "quote": "타는 냄새"},
        }
    ]
    hand = r5_session(k, [*found, {"type": "out_of_scope_choice", "choice": "handover"}])
    assert hand.handed_over and session.decide(hand, k, g, m)["kind"] == "handover"
    assert session.probabilities(hand) == session.probabilities(r5_session(k))  # 인계는 순위를 바꾸지 않음
    go = r5_session(k, [*found, {"type": "out_of_scope_choice", "choice": "continue"}])
    assert not go.handed_over and go.pending is None and "CL-1" not in go.candidates  # 정상 결과 반영
    assert session.decide(go, k, g, m)["kind"] == "call"  # R5 예시와 같은 다음 단계(4.4 C 호출)
