# ruff: noqa: F811, E501 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 가져와 쓴다
"""D-546 U5 ML 알람 카드: 센서 경계를 넘지 않아도 ML 유형으로 증상군을 정한 알람(Pack alarm_block 없음)을 등록 —
알람 꼴 = 그 증상군 센서 값 · 가까운 경계 · origin ml_type(경계 안 넘음 crossed false) · 화면 칸 ml = 예측 유형 · 위험도.
(지금까지 ml_type_alarm_unsupported로 거절하던 길 — CH-25를 D-546이 바꿈)
"""

import pytest
from sqlalchemy import text

from test_api import TECH_PW, api, db, login  # noqa: F401
from test_deploy_path import ML_OUT, alarm_rows, count, deploy, ml_alarm_output, register_sc, reset_ops, scenarios  # noqa: F401

pytestmark = pytest.mark.db


@pytest.fixture
def ml(deploy, monkeypatch):
    from c1a15.deploy import runs

    monkeypatch.setenv("C1A15_ALARM", "ml")
    runs.settings.cache_clear()
    yield deploy
    runs.settings.cache_clear()


def test_ml_type_alarm_registered_with_ml_view_on_home_and_alarm_screen(api, db, ml):
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    out = ml_alarm_output("x", "ml_type", None)
    out["output"]["alarm"]["sensor_derived"] = {"power_w": 6299, "temp_diff_k": 8.5}  # 온도 차 경계(8.2 아래) 안 넘음
    out["output"]["alarm"]["ml"] = {**ML_OUT, "risk": 0.82}
    ml.plan = [out]
    r = register_sc(api, scenarios()[0])
    assert r.status_code == 200 and r.json()["alarm_id"] and r.json()["symptom_group"] == "CL"
    (row,) = alarm_rows(db)
    assert row["symptom_group"] == "CL" and row["ml"]["risk"] == 0.82
    assert row["alarm"] == {
        "sensor": "온도 차",
        "value": 8.5,
        "unit": "K",
        "boundary": 8.2,
        "direction": "경계 아래",
        "trend_title": "온도 차 추이 · 최근 12회 측정",
        "origin": "ml_type",
        "crossed": False,
    }
    assert count(db, "select count(*) from ops.audit_log where action = 'alarm_rejected'") == 0  # 거절 감사 0
    (a,) = tech.get("/api/tech/home").json()["alarms"]
    assert (
        a["ml"] == {"type": "HDF", "type_name": "열 방출 고장(HDF)", "risk": 0.82} and a["alarm"]["origin"] == "ml_type"
    )
    s = tech.post(f"/api/tech/alarms/{a['alarm_id']}/session").json()
    assert s["alarm"]["ml"] == a["ml"] and s["alarm"]["out_of_range_count"] == 0  # 센서 이탈 0


def test_boundary_alarm_keeps_boundary_view_with_ml_values(api, db, ml):
    reset_ops(db)
    tech, _ = login(api, "tech", "tech.kim", TECH_PW)
    block = {
        "sensor": "온도 차",
        "value": 7.8,
        "unit": "K",
        "boundary": 8.2,
        "direction": "경계 아래",
        "trend_title": "온도 차 추이 · 최근 12회 측정",
    }
    ml.plan = [ml_alarm_output("x", "sensor_boundary", block)]
    register_sc(api, scenarios()[0])
    (a,) = tech.get("/api/tech/home").json()["alarms"]
    assert {k: a["alarm"][k] for k in block} == block and a["alarm"].get("origin") is None  # 경계 알람 꼴 그대로
    assert a["ml"] == {"type": "HDF", "type_name": "열 방출 고장(HDF)", "risk": 0.71}
    with db.connect() as c:
        assert c.execute(text("select count(*) from ops.alarms")).scalar() == 1
