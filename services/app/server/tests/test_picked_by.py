"""D-652 12-f: Pack 옵션 check_first_<증상군>이 첫 점검을 바꾼 판정 = reason_facts.picked_by
{option, section, title, instead_of, instead_title} → '이 점검을 고른 이유' = 정해 둔 글(확률 ÷ 분 말 0 · AI 글 0).
picked_by가 없으면 지금 길 그대로."""

from types import SimpleNamespace

from c1a15.api import sessions
from c1a15.llm.base import Facts

PB = {
    "option": "check_first_cl",
    "section": "4.3",
    "title": "흡기 필터 육안 점검",
    "instead_of": "4.6",
    "instead_title": "냉각 설정값 확인",
}
LINE = "먼저 볼 점검으로 정해 둔 흡기 필터 육안 점검(4.3절) — 원래 냉각 설정값 확인(4.6절)"


def _facts(**extra):
    return Facts(
        "reason",
        "4.3",
        {
            "rank": 2,
            "cause_name": "냉각 필터 막힘",
            "probability": 20,
            "minutes": 3,
            "grade": "A",
            "alternatives": [],
            **extra,
        },
    )


def test_picked_line_and_shape():
    assert sessions.picked_line(PB) == LINE
    assert sessions.picked_by_of({"reason_facts": {"picked_by": PB}}) == PB
    assert sessions.picked_by_of({"reason_facts": {}}) is None
    assert sessions.picked_by_of({"reason_facts": {"picked_by": {**PB, "instead_of": ""}}}) is None  # 꼴 어긋남 = 없음


def test_checked_reason_says_the_option_not_probability(monkeypatch):
    row = SimpleNamespace(similar=[])
    d = {"section": "4.3", "reason_facts": {"picked_by": PB}}
    called = []
    monkeypatch.setattr(sessions, "adapter_for_mode", lambda: called.append(1))
    got = sessions._checked_reason(row, d, _facts(picked_by=PB))
    assert (got["reason"], got["reason_fallback"]) == (LINE, False)
    assert called == []  # AI 글 0(정해 둔 글)
