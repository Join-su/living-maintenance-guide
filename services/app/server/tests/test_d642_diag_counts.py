# ruff: noqa: E501 — 시험 설명 문장 · 실제 출력 줄은 길이 제한 밖
"""리허설 2 R3 · R6(A 진단 숫자 · 앱 몫, 표시만 · 셈 손 0): 순위 줄은 확률 셈에 쓴 기록 n건 중 k건을 보인다.

n · k = Pack 판정 출력 record_counts {scope, total, by_cause, prior} 그대로(앱이 다시 세지 않음).
칸이 없거나 꼴이 다르면 None — 예외 걸음 · 점검 바꾸기 뒤 결정엔 칸이 없어 세션 시작 때 남긴 진단 스냅샷 값을 그대로 쓴다.
'이 점검을 고른 이유'의 다른 점검 목록은 확률 순위가 아닌 매뉴얼 점검 목록이라 '매뉴얼의 다른 점검'으로 부른다. DB 없음.
"""

import json

from c1a15.api import screen
from c1a15.llm.base import Facts
from c1a15.llm.fixture import FixtureAdapter

ORDER = ["CL-1", "CL-2", "CL-3", "CL-4"]
# Pack pack/d642 c36ec02 실제 출력(저장소 그래프 · count_equipment · start 결과 decision 칸 그대로)
M03 = json.loads(
    '{"kind": "lock", "section": "1.2", "probabilities": {"CL-1": 67, "CL-2": 11, "CL-3": 11, "CL-4": 11}, "evidence": {"CL-1": 4, "CL-2": 0, "CL-3": 6, "CL-4": 0}, "record_counts": {"scope": "equipment", "total": 5, "by_cause": {"CL-1": 5, "CL-2": 0, "CL-3": 0, "CL-4": 0}, "prior": 1}}'
)
MZZ = json.loads(
    '{"kind": "lock", "section": "1.2", "probabilities": {"CL-1": 52, "CL-2": 23, "CL-3": 10, "CL-4": 15}, "evidence": {}, "record_counts": {"scope": "group", "total": 108, "by_cause": {"CL-1": 57, "CL-2": 25, "CL-3": 10, "CL-4": 16}, "prior": 1}}'
)


def test_real_pack_output_maps_to_the_screen_fields_in_manual_order():
    assert screen.PACK_COUNT_KEY == "record_counts"
    got = screen.decided_counts(M03, ORDER)
    assert got == {
        "equipment_counts": {"CL-1": 5, "CL-2": 0, "CL-3": 0, "CL-4": 0},
        "equipment_total": 5,
        "count_scope": "equipment",
        "count_prior": 1,
    }
    shuffled = {
        **M03,
        "record_counts": {**M03["record_counts"], "by_cause": {"CL-4": 0, "CL-3": 0, "CL-1": 5, "CL-2": 0}},
    }
    assert list(screen.decided_counts(shuffled, ORDER)["equipment_counts"]) == ORDER
    assert screen.decided_counts(MZZ, ORDER) == {
        "equipment_counts": {"CL-1": 57, "CL-2": 25, "CL-3": 10, "CL-4": 16},
        "equipment_total": 108,
        "count_scope": "group",
        "count_prior": 1,
    }


def test_no_counts_or_odd_shape_gives_none():
    rc = M03["record_counts"]
    assert screen.decided_counts(None, ORDER) is None
    assert (
        screen.decided_counts({"probabilities": {}, "evidence": {}}, ORDER) is None
    )  # 옛 Pack · 옛 세션 · 예외 · 바꾸기 뒤
    for bad in (
        {**rc, "total": "5"},
        {**rc, "by_cause": [5, 0]},
        {**rc, "by_cause": {"CL-1": "5"}},
        {**rc, "by_cause": {"CL-1": True}},
        {**rc, "total": True},
        {**rc, "prior": None},
        {**rc, "scope": "line"},
        {k: v for k, v in rc.items() if k != "scope"},
        "x",
        [rc],
    ):
        assert screen.decided_counts({**M03, "record_counts": bad}, ORDER) is None, bad


def test_a_decision_without_counts_shows_the_start_snapshot():
    events = [
        {"type": "step_opened", "decision": M03},
        {"type": "preview_shown", "preview": {}, **screen.decided_counts(M03, ORDER)},
        {
            "type": "step_opened",
            "decision": {"kind": "instruct", "probabilities": M03["probabilities"], "evidence": {}},
        },
    ]
    assert screen.shown_counts(events[-1]["decision"], events, ORDER) == screen.decided_counts(M03, ORDER)
    later = {**M03, "record_counts": {**M03["record_counts"], "total": 7}}
    assert screen.shown_counts(later, events, ORDER)["equipment_total"] == 7  # 칸이 있으면 그 결정 값
    assert (
        screen.shown_counts({"kind": "instruct"}, [{"type": "preview_shown", "preview": {}}], ORDER) is None
    )  # 옛 세션: 숨김


def test_reason_calls_the_alternatives_the_manuals_other_checks():
    facts = {
        "cause_name": "냉각 필터 막힘",
        "probability": 50,
        "minutes": 3,
        "grade": "A",
        "alternatives": [{"section": "4.4", "grade": "C"}, {"section": "4.5", "grade": "A"}],
    }
    for rank in (1, 2):
        text = FixtureAdapter().write(Facts("reason", "4.3", {**facts, "rank": rank})).text
        assert text.endswith("매뉴얼의 다른 점검: 4.4(C), 4.5(A).")
        assert text.count("다른 점검") == 1
