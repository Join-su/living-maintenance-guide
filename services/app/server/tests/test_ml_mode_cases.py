# ruff: noqa: F811, E501 — test_api · test_deploy_path의 fixture(api · db · deploy)를 이름으로 가져와 쓴다
"""D-546 M3 dev-2 ML 방식(ML만): 시험 자료 23행(task10-pack handoff d546 ml-alarm-cases · 모델 release v2)을
가짜 배포 서버 답(c1a15-pack alarm_route와 같은 규칙 = 앱 alarms.ml.route)으로 걸어 앱 결과를 기대와 대조.
- ML 유형만으로 알람(HDF → CL · OSF → LD) · 경계 + ML(증상군 = 경계) · 문턱 아래 = 알람 없음
- 온도 차 경계만 넘고 ML 정상 = 알람 없음(출력 · 온도 차 · 토크 경계 알람은 dev-2 ml에서 끔)
- 공구 마모 215분 경계 = ML 정상이어도 알람 TW(Pack alarm.py d05a64d가 냄 — 앱은 받은 대로 등록)
- 공구 교체 주의 200 ~ 246분(알람과 따로)
"""

import json
from pathlib import Path

import pytest

from test_api import api, db  # noqa: F401
from test_deploy_path import alarm_rows, count, deploy, reset_ops  # noqa: F401

pytestmark = pytest.mark.db

CASES = [
    json.loads(x)
    for x in (Path(__file__).parent / "fixtures" / "ml_alarm_cases_d546.jsonl").read_text(encoding="utf-8").splitlines()
    if x.strip()
]


@pytest.fixture
def ml(deploy, monkeypatch):
    from c1a15.deploy import runs

    monkeypatch.setenv("C1A15_ALARM", "ml")
    runs.settings.cache_clear()
    yield deploy
    runs.settings.cache_clear()


def pack_out(case: dict) -> dict:
    """Pack alarm Run 출력(약속 v6 꼴) — 경계 판정 · ML 규칙 = Pack과 같은 식(앱 engine · alarms.ml 사본),
    공구 마모 = Pack custom/alarm.py(d05a64d): ML 알람 없음 + 마모 > TW 경계면 알람 TW · sensor_boundary."""
    from c1a15.alarms import ml as rule
    from c1a15.api import deps
    from c1a15.engine import alarm_rules

    e = case["expect"]
    hit = alarm_rules.detect({**case["sensor_snapshot"], **case["sensor_derived"]}, deps.alarm_table())
    pred = {
        "type": e["predicted_type"],
        "probability": e["confidence"],
        "probabilities": case["v2_probabilities"],
        "model_release": "mr-ai4i-lgbm-large-v2",
    }
    r = rule.route(pred, hit, 0.5)
    if not r["alarm"] and case["sensor_snapshot"]["tool_wear_min"] > 215:
        r = {**r, "alarm": True, "group": "TW", "group_source": "sensor_boundary", "no_alarm_reason": None}
    return {
        "output": {
            "alarm": {
                "event_id": "x",
                "alarm": r["alarm"],
                "group": r["group"],
                "group_source": r["group_source"],
                "alarm_block": hit["alarm"] if hit else None,
                "sensor_derived": case["sensor_derived"],
                "ml": r["ml"],
                "no_alarm_reason": r["no_alarm_reason"],
            }
        }
    }


def alarm_runs(fake) -> list[dict]:
    return [q["body"] for q in fake.requests if q["method"] == "POST" and (q["body"] or {}).get("kind") == "alarm"]


def reading(api, case: dict, equipment: str = "M-01"):
    from fastapi.testclient import TestClient

    body = {
        "equipment_id": equipment,
        "sensor_snapshot": case["sensor_snapshot"],
        "product_type": case["product_type"],
        "data_lineage": {"source_release": "ai4i-2020@sha256:test", "row_id": case["udi"], "split": case["split"]},
    }
    return TestClient(api).post("/api/internal/alarms", json=body, headers={"X-Internal-Token": "test-internal-token"})


@pytest.mark.parametrize("case", CASES, ids=[c["case"] for c in CASES])
def test_m3_case_matches_expectation(api, db, ml, case):
    reset_ops(db)
    e = case["expect"]
    ml.plan = [pack_out(case)]
    r = reading(api, case)
    assert r.status_code == 200, r.text
    rows = alarm_rows(db)
    if e["alarm"]:
        assert r.json()["symptom_group"] == e["group"]
        (row,) = rows
        source = "ml_type" if row["alarm"].get("origin") == "ml_type" else "sensor_boundary"
        assert (row["symptom_group"], source) == (e["group"], e["group_source"])
        assert abs(row["ml"]["risk"] - e["risk"]) < 1e-6  # ML 값은 알람 행에 그대로(공구 마모 알람도)
        assert row["sensor_snapshot"]["power_w"] == case["sensor_derived"]["power_w"]  # 센서 7칸
    else:
        assert r.json()["alarm_id"] is None and rows == []
    caution = count(db, "select count(*) from ops.audit_log where action = 'tool_caution'")
    assert caution == int(e["tool_wear_caution"])
    assert len(alarm_runs(ml)) == 1  # ML Run 하나(공구 마모 알람도 그 Run이 냄)


def test_predict_failure_is_no_alarm_and_one_audit_even_with_tool_wear(api, db, ml):
    """예측 실패(D-275 · Run 실패)는 Pack이 공구 마모를 볼 차례 전 — 알람 없음 + 감사 한 줄(앱은 판정 0)."""
    reset_ops(db)
    case = next(c for c in CASES if c["case"] == "M3-18")
    ml.plan = [{"status": 500, "code": "run_failed", "where": "run.predict_failure", "node_code": "feature_not_number"}]
    r = reading(api, case)
    assert r.status_code == 200 and r.json()["alarm_id"] is None and alarm_rows(db) == []
    assert count(db, "select count(*) from ops.audit_log where action = 'alarm_rejected'") == 1
    assert len(alarm_runs(ml)) == 1


def test_low_confidence_without_tool_wear_is_no_alarm_and_one_audit(api, db, ml):
    reset_ops(db)
    case = next(c for c in CASES if c["case"] == "M3-22")
    out = pack_out(case)
    out["output"]["alarm"]["no_alarm_reason"] = "rnf"
    ml.plan = [out]
    r = reading(api, case)
    assert r.status_code == 200 and r.json()["alarm_id"] is None and alarm_rows(db) == []
    assert count(db, "select count(*) from ops.audit_log where action = 'alarm_rejected'") == 1
