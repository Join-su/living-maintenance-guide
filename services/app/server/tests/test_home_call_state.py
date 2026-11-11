# ruff: noqa: F811, E501 — test_api의 fixture(api · db)를 이름으로 가져와 쓴다
"""G3 대조(답장 g3-audit-fix 2): 왼쪽 알람 칸 상태 줄 '진행 중 · 파트장 호출' — 홈 알람 행 칸 call_sent
= 지금 단계가 보낸 호출(파트장 호출 approval · 숙련자 인계 handover)이고 답 전이면 그 종류, 아니면 null."""

import pytest

from test_api import api, db, ok, reset_ops, run_until, start, to_instruct  # noqa: F401

pytestmark = pytest.mark.db


def _row(tech, sid):
    return next(a for a in tech.get("/api/tech/home").json()["alarms"] if a["session_id"] == sid)


def test_home_row_call_sent_approval_and_handover(api, db):
    reset_ops(db)
    tech, s = start(api, db)
    s = to_instruct(tech, s)
    assert _row(tech, s["session_id"])["call_sent"] is None
    s = run_until(tech, s, {"call"}, lambda sec: "정상")
    s = ok(tech, s, type="call_wait")
    assert _row(tech, s["session_id"])["call_sent"] is None  # 대기는 보낸 것 아님
    s = ok(tech, s, type="call_send")
    assert _row(tech, s["session_id"])["call_sent"] == "approval"
    tech2, s2 = start(api, db, equipment="M-03")
    s2 = to_instruct(tech2, s2)
    s2 = ok(tech2, s2, type="result", section="4.3", value="이상", memo="기름때")
    s2 = ok(tech2, s2, type="action_done", section="4.3.2")
    s2 = ok(tech2, s2, type="restart", checked=s2["restart"]["checklist"])
    s2 = ok(tech2, s2, type="restart_result", outcome="outside")
    assert _row(tech2, s2["session_id"])["call_sent"] is None  # 인계 단계 · 보내기 전
    s2 = ok(tech2, s2, type="call_send")
    assert _row(tech2, s2["session_id"])["call_sent"] == "handover"
