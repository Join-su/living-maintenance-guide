"""D-593(사용자) · D-637 F11(근거 기록 id): 예외 후보 글 — 규칙은 등록 기기에서,
최근 정비는 이 기기 것. 값은 Pack matched 그대로."""

import pytest

from c1a15.api import flow

BASE = {"group": "온도 차 저하", "sensors": [], "exception": "EX-R-5001", "records": ["R-5001"]}
RECENT = {"section": "4.3.2", "days": 6, "record_id": "H-0302", "at": "2026-11-04T04:01:32+00:00", "days_ago": 2.0}


@pytest.mark.parametrize(
    ("matched", "line"),
    [
        (
            {**BASE, "equipment_id": "M-03", "source_equipment": "M-01", "recent": RECENT},
            "M-01에서 배운 규칙 · 이 기기도 2일 전 같은 정비(4.3.2 · H-0302)",
        ),
        (
            {**BASE, "equipment_id": "M-01", "source_equipment": "M-01", "recent": {**RECENT, "days_ago": 3.3}},
            "이 기기에서 배운 규칙 · 3일 전 같은 정비(4.3.2 · H-0302)",
        ),
        (
            {**BASE, "equipment_id": "M-03", "source_equipment": "M-01", "recent": {**RECENT, "days_ago": 0.4}},
            "M-01에서 배운 규칙 · 이 기기도 오늘 같은 정비(4.3.2 · H-0302)",
        ),
        (
            {**BASE, "equipment_id": "M-03", "source_equipment": "M-01", "sensors": [["temp_diff_k", "<=", 7.8]]},
            "M-01에서 배운 규칙 · 온도 차 <= 7.8",
        ),
    ],
)
def test_why_line_says_rule_came_from_there_and_work_is_here(matched, line):
    assert flow.why_line({"matched": matched}) == line


def test_no_matched_no_line():
    assert flow.why_line({}) is None and flow.why_line(None) is None


MINE_OLD = [{"record_id": "H-0201", "did": ["4.3.2"], "checked": [], "days_before": 12}]  # 7일 밖 → 조건 밖


@pytest.mark.parametrize(
    ("source", "mine", "line"),
    [
        (  # F11 M-02: 등록 예외의 최근 정비 절을 이 기기는 최근 7일 안에 안 함
            {"record_id": "R-5001", "equipment_id": "M-01", "sections": ["4.3.2"]},
            [],
            "예외 조건 안 맞음 · 최근 7일 같은 정비(4.3.2) 없음 · M-01에서 배운 규칙",
        ),
        (
            {"record_id": "R-5001", "equipment_id": "M-01", "sections": ["4.3.2"]},
            [{"record_id": "H-0205", "did": ["4.4.2"], "checked": ["4.4"], "days_before": 1}],
            "예외 조건 안 맞음 · 최근 7일 같은 정비(4.3.2) 없음 · M-01에서 배운 규칙",
        ),
    ],
)
def test_miss_line_says_why_not(source, mine, line):
    assert flow.miss_line(source, mine, 7) == line


def test_miss_line_silent_when_not_sure():
    """실패 쪽: 같은 정비가 있는데 안 맞았으면 까닭을 모름 → 글 0 · 등록 예외 없음 → 글 0."""
    src = {"record_id": "R-5001", "equipment_id": "M-01", "sections": ["4.3.2"]}
    assert flow.miss_line(src, [{"record_id": "H-0302", "did": ["4.3.2"], "checked": [], "days_before": 2}], 7) is None
    assert flow.miss_line(None, [], 7) is None
    assert flow.miss_line({**src, "sections": []}, [], 7) is None
