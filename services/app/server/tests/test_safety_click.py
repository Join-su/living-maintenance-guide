# ruff: noqa: F811 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 쓴다
"""EV-007(D-422 · D-423): 빠른 여러 번 누름으로 알람 · 잠금 · 사전 확인을 건너뛰지 못하게 — 서버 전이 거절.

알람 확인(alarm_ack) → 잠금 → 사전 확인(값 필수) 차례만 받는다. 되풀이 · 차례 밖 요청은 거절(409 · 감사),
같은 seq 재전송은 같은 응답 한 번. 알람 확인은 판정(배포 서버 Run)을 부르지 않는다.
"""

import pytest
from sqlalchemy import text

from test_api import TECH_PW, api, db, ev, login, ok  # noqa: F401
from test_checkrecord import alarm
from test_deploy_path import deploy, reset_ops  # noqa: F401

pytestmark = pytest.mark.db


def runs_of(fake):
    return [p for p in fake.posts() if p["path"] == "/runs"]


def start(api, db):
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    aid = alarm(api, "DEMO-01")
    return tech, aid, tech.post(f"/api/tech/alarms/{aid}/session").json()


def rejects(db, reason):
    with db.connect() as c:
        return c.execute(
            text("select count(*) from ops.audit_log where action = 'reject' and detail->>'reason' = :r"), {"r": reason}
        ).scalar()


def test_lock_needs_alarm_ack(api, db, deploy):
    """알람 화면을 거치지 않은 잠금은 거절 — 알람 확인 뒤에만 잠금."""
    tech, _, s = start(api, db)
    r = ev(tech, s, type="lock")
    assert r.status_code == 409 and r.json()["detail"] == "alarm_not_acked"
    assert rejects(db, "alarm_not_acked") == 1
    s = ok(tech, s, type="alarm_ack")
    assert s["alarm_ack_at"] and not s["locked"]
    s = ok(tech, s, type="lock")
    assert s["locked"]


def test_alarm_ack_once_and_no_run(api, db, deploy):
    """알람 확인은 한 번만 남고(되풀이는 기록 0) 배포 서버 Run을 부르지 않는다."""
    tech, _, s = start(api, db)
    before = len(runs_of(deploy))
    s1 = ok(tech, s, type="alarm_ack")
    s2 = ok(tech, s1, type="alarm_ack")  # 다음 seq로 또 — 같은 상태, 새 기록 없음
    assert s2["last_seq"] == s1["last_seq"] and s2["alarm_ack_at"] == s1["alarm_ack_at"]
    assert len(runs_of(deploy)) == before
    with db.connect() as c:
        n = c.execute(
            text("select count(*) from ops.session_events where session_id = :s and type = 'alarm_ack'"),
            {"s": s["session_id"]},
        ).scalar()
    assert n == 1


def test_lock_twice_rejected(api, db, deploy):
    tech, _, s = start(api, db)
    s = ok(tech, s, type="alarm_ack")
    s = ok(tech, s, type="lock")
    r = ev(tech, s, type="lock")
    assert r.status_code == 409 and r.json()["detail"] == "already_locked"


def test_precheck_needs_values_and_once(api, db, deploy):
    """사전 확인은 값이 있어야 하고, 끝난 뒤 되풀이는 거절."""
    tech, _, s = start(api, db)
    s = ok(tech, s, type="alarm_ack")
    s = ok(tech, s, type="lock")
    for body in ({}, {"values": "  "}):
        r = ev(tech, s, type="precheck_done", **body)
        assert r.status_code == 409 and r.json()["detail"] == "precheck_values_required"
    s = ok(tech, s, type="precheck_done", values="하우징 52 °C")
    assert s["step"]["kind"] != "precheck"
    r = ev(tech, s, type="precheck_done", values="하우징 52 °C")
    assert r.status_code == 409 and r.json()["detail"] == "not_precheck_step"


def test_same_seq_resend_is_one(api, db, deploy):
    """같은 seq 재전송(빠른 두 번 누름 — 화면이 아직 옛 seq)은 같은 응답 한 번 · 판정 Run도 한 번."""
    tech, _, s = start(api, db)
    s = ok(tech, s, type="alarm_ack")
    before = len(runs_of(deploy))
    a = ok(tech, s, type="lock")
    b = ok(tech, s, type="lock")
    assert a["last_seq"] == b["last_seq"] and len(runs_of(deploy)) == before + 1


def test_session_start_twice_one_session_one_run(api, db, deploy):
    """홈 '점검 시작' 두 번 = 세션 하나 · start Run 하나."""
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    aid = alarm(api, "DEMO-01")
    before = len(runs_of(deploy))
    a = tech.post(f"/api/tech/alarms/{aid}/session").json()
    b = tech.post(f"/api/tech/alarms/{aid}/session").json()
    assert a["session_id"] == b["session_id"]
    assert len(runs_of(deploy)) == before + 1


def test_precheck_correction_cannot_empty_values(api, db, deploy):
    """review: '이 기록 정정'으로 사전 확인 값을 비워 값 필수를 뒤에서 피하지 못함."""
    from test_correction import fix, seq_of

    tech, _, s = start(api, db)
    for t, body in (("alarm_ack", {}), ("lock", {}), ("precheck_done", {"values": "하우징 52 °C"})):
        s = ok(tech, s, type=t, **body)
    r = fix(tech, s, target_seq=seq_of(db, s["session_id"], "precheck_done"), values="  ", reason="지움")
    assert r.status_code == 409 and r.json()["detail"] == "precheck_values_required"
