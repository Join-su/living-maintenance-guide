# ruff: noqa: F811, E501 — test_api의 fixture(api · db)를 이름으로 가져와 쓴다
"""D-546 U6 공구 교체 주의: 센서 값의 공구 마모가 200 ~ 246분이면 그 설비에 '공구 교체 주의' — 알람 아님
(점검 · 세션 · 보고서 흐름 0) · 정비자 홈 · 「확인」을 누르면 사라짐. 표 · migration 0(감사 줄 두 종류로 남김).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from test_api import SENSOR, TECH_PW, api, db, login, register, reset_ops  # noqa: F401

pytestmark = pytest.mark.db

QUIET = {**SENSOR, "process_temperature_k": 311.6}  # 온도 차 9.1 K — 어느 경계도 넘지 않음(알람 없음)


def _cautions(tech):
    return tech.get("/api/tech/home").json()["cautions"]


def _alarms(db):
    with db.connect() as c:
        return c.execute(text("select count(*) from ops.alarms")).scalar()


def test_tool_caution_range_latest_ack_twice_and_again(api, db):
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    assert _cautions(tech) == []
    assert (
        register(api, "M-02", {**QUIET, "tool_wear_min": 205}).status_code == 422
    )  # 경계 안 넘음 = 알람 없음 응답 그대로
    assert _alarms(db) == 0  # 알람 아님
    (c1,) = _cautions(tech)
    assert (c1["equipment_id"], c1["tool_wear_min"]) == ("M-02", 205) and c1["at"] and c1["caution_id"]
    register(api, "M-02", {**QUIET, "tool_wear_min": 210})
    (c2,) = _cautions(tech)  # 같은 설비는 최근 값 하나
    assert c2["tool_wear_min"] == 210 and c2["caution_id"] != c1["caution_id"]
    for wear in (199, 247):  # 범위 밖은 주의 0(M-03)
        register(api, "M-03", {**QUIET, "tool_wear_min": wear})
    assert [c["equipment_id"] for c in _cautions(tech)] == ["M-02"]
    assert _alarms(db) == 1  # 247은 공구 마모 경계(215) 넘음 = 알람(주의와 따로)
    other, _ = login(api, "tech", "tech.lee", TECH_PW)
    assert [c["caution_id"] for c in _cautions(other)] == [c2["caution_id"]]  # 정비자 모두에게(설비 담당과 상관없음)
    r = tech.post(f"/api/tech/cautions/{c2['caution_id']}/ack")
    assert r.status_code == 200 and r.json() == {"caution_id": c2["caution_id"], "equipment_id": "M-02"}
    assert _cautions(tech) == [] and _cautions(other) == []  # 확인하면 사라짐(누구 화면에서든)
    assert tech.post(f"/api/tech/cautions/{c2['caution_id']}/ack").status_code == 200  # 같은 확인 두 번째 누름 — 그대로
    assert tech.post(f"/api/tech/cautions/{c1['caution_id']}/ack").status_code == 200  # 앞 주의도 이미 확인된 것
    with db.connect() as c:
        acks = c.execute(text("select actor, target from ops.audit_log where action = 'tool_caution_ack'")).all()
    assert [tuple(a) for a in acks] == [("tech.kim", "equipment:M-02")]  # 확인 줄은 한 번만
    register(api, "M-02", {**QUIET, "tool_wear_min": 220})  # 확인 뒤 같은 공구(200 아래로 안 내려감) → 다시 뜨지 않음
    assert _cautions(tech) == [] and _alarms(db) == 2  # 215 넘음 = 공구 마모 알람은 그대로
    register(api, "M-02", {**QUIET, "tool_wear_min": 150})  # 공구 교체(200 아래)
    assert _cautions(tech) == []
    register(api, "M-02", {**QUIET, "tool_wear_min": 205})  # 교체 뒤 다시 200 ~ 246 → 다시 뜸(답장 zzz19 · 꼴 02)
    assert _cautions(tech) == []  # 220 공구 마모 알람이 아직 떠 있음 = 알람이 이김(덧 25)
    with db.begin() as c:
        c.execute(text("update ops.alarms set status = 'done' where equipment_id = 'M-02'"))
    (c3,) = _cautions(tech)
    assert c3["tool_wear_min"] == 205 and c3["caution_id"] > c2["caution_id"]


def test_tool_caution_replaced_before_ack_disappears(api, db):
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    register(api, "M-02", {**QUIET, "tool_wear_min": 210})  # 215 아래 = 주의만(넘으면 알람이 이김 — 덧 25)
    assert [c["tool_wear_min"] for c in _cautions(tech)] == [210]
    register(api, "M-02", {**QUIET, "tool_wear_min": 10})  # 확인 전에 공구 교체 → 사라짐
    register(api, "M-02", {**QUIET, "tool_wear_min": 12})
    assert _cautions(tech) == []
    with db.connect() as c:
        n = c.execute(text("select count(*) from ops.audit_log where action = 'tool_caution_reset'")).scalar()
    assert n == 1  # 교체 줄은 주의가 있던 뒤 한 번만(200 아래 값마다 쌓지 않음)


def test_tool_caution_ack_unknown_and_needs_login(api, db):
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    assert tech.post("/api/tech/cautions/999999/ack").status_code == 404
    register(api, "M-02", {**QUIET, "tool_wear_min": 212})
    (c1,) = _cautions(tech)
    assert TestClient(api).post(f"/api/tech/cautions/{c1['caution_id']}/ack").status_code in {
        401,
        403,
    }  # 로그인 없으면 거부
    assert [c["caution_id"] for c in _cautions(tech)] == [c1["caution_id"]]


def test_tool_caution_hidden_while_tool_wear_alarm_open(api, db):
    """D-546 덧 25: 같은 설비에 공구 마모 경계 알람(> 215분)이 떠 있으면 주의 줄 0(알람이 이김) — 알람이 끝나면 지금 규칙대로."""
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    register(api, "M-03", {**QUIET, "tool_wear_min": 205})  # 다른 설비 주의(알람 없음)
    assert register(api, "M-02", {**QUIET, "tool_wear_min": 220}).status_code == 200  # 주의 범위 · 215 넘음 = 알람
    assert _alarms(db) == 1
    assert [(c["equipment_id"], c["tool_wear_min"]) for c in _cautions(tech)] == [("M-03", 205)]  # M-02 주의 0
    with db.begin() as c:  # 보고서 확정 = 알람 끝(status done)
        c.execute(text("update ops.alarms set status = 'done' where equipment_id = 'M-02'"))
    assert [c["equipment_id"] for c in _cautions(tech)] == [
        "M-02",
        "M-03",
    ]  # 알람 끝 → 지금 규칙(확인 전 주의 다시 보임)
