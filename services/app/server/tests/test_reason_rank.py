# ruff: noqa: F811, E501 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 쓴다, 시험 설명 문장은 길이 제한 밖
"""D-450 작은 결함: 진단 화면 '이 점검을 고른 이유'(R5-6)가 고른 원인이 1위가 아닐 때 '가장 높고'라고 말하지 않는다.

장면 ③ 뒤 버전(p13)은 원인 순위를 그대로 두고 첫 지시만 2위 원인 쪽(4.3)으로 바꾼다 — 문장은 순위를 그대로 쓴다.
"""

import pytest

from c1a15.llm.base import Facts
from c1a15.llm.fixture import FixtureAdapter
from test_api import TECH_PW, api, db, login  # noqa: F401
from test_checkrecord import alarm
from test_deploy_path import deploy, reset_ops  # noqa: F401

FACTS = {
    "cause_name": "냉각 필터 막힘",
    "probability": 33,
    "minutes": 3,
    "grade": "A",
    "alternatives": [{"section": "4.5", "grade": "A"}],
}


def test_fixture_reason_says_rank_when_not_top():
    text = FixtureAdapter().write(Facts("reason", "4.3", {**FACTS, "rank": 2})).text
    assert text.startswith("2위 냉각 필터 막힘 33%: 4.3절 점검은 약 3분 · A등급입니다.") and "가장 높" not in text


def test_fixture_reason_top_unchanged():
    text = FixtureAdapter().write(Facts("reason", "4.3", {**FACTS, "rank": 1})).text
    assert (
        text
        == "냉각 필터 막힘 가능성이 33%로 가장 높고, 4.3절 점검은 약 3분 · A등급입니다. 매뉴얼의 다른 점검: 4.5(A)."
    )


@pytest.mark.db
def test_preview_reason_rank_matches_ranking(api, db, deploy):
    """시험 자료(p13) DEMO-01: 1위 냉각수 부족 · 첫 지시 4.3(냉각 필터 막힘) → '2위 냉각 필터 막힘 …', 근거 검사 통과."""
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    s = tech.post(f"/api/tech/alarms/{alarm(api, 'DEMO-01')}/session").json()
    p, probs = s["preview"], s["probabilities"]
    top = max(probs, key=probs.get)
    assert p["section"] == "4.3" and p["cause"] != top  # 고른 원인이 1위가 아님(결함이 드러나는 자료)
    assert p["reason"].startswith(f"2위 냉각 필터 막힘 {probs[p['cause']]}%:") and "가장 높" not in p["reason"]
    assert p["reason_fallback"] is False
    s6 = tech.post(f"/api/tech/alarms/{alarm(api, 'DEMO-06')}/session").json()  # 1위 원인 쪽 첫 지시는 그대로
    assert "가장 높고" in s6["preview"]["reason"]
