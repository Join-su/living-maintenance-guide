# ruff: noqa: F811 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 가져와 쓴다
"""GAP-18(시험 DB): 같은 알람으로 세션 시작이 동시에 두 번 → 세션 하나 · 둘 다 200 · 같은 세션.

앞 요청이 세션을 만드는 도중(기본 길 = 리트리버 계산, deploy 길 = start Run)에 뒤 요청을 보낸다.
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from test_api import TECH_PW, api, db, login, register  # noqa: F401
from test_deploy_path import count, deploy, reset_ops  # noqa: F401

pytestmark = pytest.mark.db


def start_twice(api, tech, alarm_id, first_inside):
    """앞 요청이 first_inside()가 참이 될 때까지 들어간 뒤 뒤 요청을 보낸다 → (응답 둘)."""
    # 서버 예외도 응답(500)으로 받아 두 요청을 같은 모양으로 비교
    first, second = (
        TestClient(api, headers={"X-App": "tech"}, cookies=tech.cookies, raise_server_exceptions=False) for _ in "ab"
    )
    out = {}
    t1 = threading.Thread(target=lambda: out.update(a=first.post(f"/api/tech/alarms/{alarm_id}/session")))
    t1.start()
    deadline = time.monotonic() + 10
    while not first_inside():
        assert time.monotonic() < deadline, "앞 요청이 세션 만들기에 들어가지 않음"
        time.sleep(0.01)
    t2 = threading.Thread(target=lambda: out.update(b=second.post(f"/api/tech/alarms/{alarm_id}/session")))
    t2.start()
    t1.join(30)
    t2.join(30)
    assert not (t1.is_alive() or t2.is_alive()), "30초 안에 두 요청이 끝나지 않음(잠금 대기)"
    return out["a"], out["b"]


def assert_one_session(db, alarm_id, a, b):
    assert (a.status_code, b.status_code) == (200, 200)
    assert a.json()["session_id"] == b.json()["session_id"]
    assert count(db, "select count(*) from ops.diag_sessions where alarm_id = :a", a=alarm_id) == 1
    assert count(db, "select count(*) from ops.audit_log where action = 'session_start'") == 1


def test_concurrent_start_deploy_path_returns_existing_session_and_one_start_run(api, db, deploy):
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    alarm_id = register(api, "M-01").json()["alarm_id"]
    deploy.plan = [{"use_default": True, "delay": 1.0}]  # 앞 요청의 start Run이 1초 걸림

    def starts():
        return [p for p in deploy.posts() if p["body"]["kind"] == "start"]

    a, b = start_twice(api, tech, alarm_id, lambda: len(starts()) > 0)
    assert_one_session(db, alarm_id, a, b)
    assert len(starts()) == 1  # 뒤 요청은 Run을 보내지 않고 기존 세션을 받음(PR-10 start Run 세션당 1회)
    with db.connect() as c:
        assert c.execute(text("select status from ops.alarms where alarm_id = :a"), {"a": alarm_id}).scalar() == (
            "in_progress"
        )
