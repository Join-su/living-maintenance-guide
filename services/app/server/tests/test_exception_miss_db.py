"""D-637 F11(사용자 리허설): M-03이 M-01과 왜 비슷한지 · M-02는 왜 아닌지 — 진단 응답 한 줄
(표시만 · 진단 계산 · Pack 손 0). 비슷함 = exception_why(Pack matched + 근거 기록 id)
· 아님 = exception_miss(등록 예외의 최근 정비 절을 이 기기는 최근 7일 안 안 함)."""

from datetime import UTC, datetime, timedelta

import pytest

from test_api import (  # noqa: F401
    api,
    db,
    reset_ops,
    start,  # noqa: E402
)
from test_deploy_path import deploy  # noqa: F401
from test_exception_guide import _approved_exception

pytestmark = pytest.mark.db


def _line(rid, eq, days_ago):
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=days_ago)
    return {
        "record_id": rid,
        "equipment_id": eq,
        "occurred_at": at.isoformat(),
        "symptom_group": {"code": "CL", "name": "CL"},
        "sensor_snapshot": {},
        "steps": [{"kind": "action", "section": "4.3.2", "name": "흡기 필터 교체"}],
        "outcome": {"type": "원인 미상·숙련자 인계"},
        "narrative": f"{eq} 시험 줄",
        "provenance": "시연 이력 줄(D-570)",
        "demo": "D-570",
    }


@pytest.fixture
def work(db):  # noqa: F811
    from c1a15 import demo_history

    reset_ops(db)
    with db.begin() as c:
        for rid in ("H-0301", "H-0302"):
            demo_history.remove(c, rid)
        demo_history.add(c, _line("H-0301", "M-01", 3))
    yield
    with db.begin() as c:
        for rid in ("H-0301", "H-0302"):
            demo_history.remove(c, rid)


def test_m02_says_why_not(api, db, deploy, work):  # noqa: F811
    _approved_exception(api, db)  # M-01 보고서 승인 — 자동 정보에 최근 정비 H-0301(4.3.2)
    _, s = start(api, db, equipment="M-02")  # Pack 답에 예외 안내 없음(가짜 배포 기본)
    assert s.get("exception_why") is None
    assert s["exception_miss"] == "예외 조건 안 맞음 · 최근 7일 같은 정비(4.3.2) 없음 · M-01에서 배운 규칙"


def test_m03_no_miss_when_same_work(api, db, deploy, work):  # noqa: F811
    """실패 쪽: 같은 정비를 했는데 예외 안내가 없으면 까닭을 모름 → 글 0(지어내기 0)."""
    _approved_exception(api, db)
    with db.begin() as c:
        from c1a15 import demo_history

        demo_history.add(c, _line("H-0302", "M-03", 2))
    _, s = start(api, db, equipment="M-03")
    assert s["exception_miss"] is None


@pytest.mark.parametrize(("hours_ago", "says"), [(4 * 24 + 1, True), (4 * 24 - 1, False)])
def test_d642_four_day_window_boundary(api, db, deploy, work, monkeypatch, hours_ago, says):  # noqa: F811
    """D-642 B 길 ①: 시연 설정 C1A15_RECENT_DAYS=4(그래프 WHEN_RECENT.days 4와 같게) — 조회 창은 [알람 − 4일, 알람)
    (Pack _recent와 같은 끝 셈). 4일 + 1시간 전 교체 = 창 밖 → '최근 4일 … 없음' · 4일 − 1시간 전 = 창 안 → 글 0."""
    from c1a15 import demo_history
    from c1a15.api import flow

    monkeypatch.setattr(flow, "RECENT_DAYS", 4)
    _approved_exception(api, db)
    line = _line("H-0302", "M-03", 0)
    line["occurred_at"] = (datetime.now(UTC).replace(microsecond=0) - timedelta(hours=hours_ago)).isoformat()
    with db.begin() as c:
        demo_history.add(c, line)
    _, s = start(api, db, equipment="M-03")
    want = "예외 조건 안 맞음 · 최근 4일 같은 정비(4.3.2) 없음 · M-01에서 배운 규칙"
    assert s["exception_miss"] == (want if says else None)
