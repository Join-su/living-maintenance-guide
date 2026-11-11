# ruff: noqa: E501 — 시험 설명 문장은 길이 제한 밖
"""EV-001 ES-04(가): ML 알람 앞단 규칙(순수 함수) — VC-13 EventCard 모양 · VC-14 위험도 문턱 · VC-15 증상군 세 길 · 모델 실패 · 문턱 설정 한 곳.

앱이 C1-a15 Pack을 부르는 길(C1A15_ALARM=ml)은 연결 결정 뒤 — 여기서는 Pack 예측 모양(predict@1 출력)을 손으로 넣는다.
"""

import pytest

from c1a15.alarms import ml


def pred(**probs):
    t = max(probs, key=probs.get)
    return {"type": t, "probability": probs[t], "probabilities": probs, "model_release": "mr-c1a15-standin-v1"}


BOUNDARY = {"group": "CL", "alarm": {"direction": "경계 아래"}}


def test_vc14_risk_below_threshold_no_alarm_no_record():
    r = ml.route(pred(NONE=0.6, HDF=0.3, PWF=0.1), None, 0.5)
    assert r["alarm"] is False and r["no_alarm_reason"] is None and r["ml"]["risk"] == pytest.approx(0.4)


def test_vc15_boundary_first():
    r = ml.route(pred(NONE=0.2, PWF=0.45, HDF=0.35), BOUNDARY, 0.5)  # 경계가 넘으면 그 증상군(ML 1순위와 달라도)
    assert (r["alarm"], r["group"], r["group_source"]) == (True, "CL", "sensor_boundary")
    assert r["no_alarm_reason"] is None
    assert [x["type"] for x in r["ml"]["top_k"]] == ["PWF", "HDF"] and r["ml"]["top_k"][0]["group_suggestion"] == "PW"


def test_vc15_ml_type_when_confident():
    r = ml.route(pred(NONE=0.1, OSF=0.8, PWF=0.1), None, 0.5)
    assert (r["alarm"], r["group"], r["group_source"]) == (True, "LD", "ml_type") and r["ml"]["confidence"] == 0.8


def test_vc15_low_confidence_or_rnf_is_no_alarm_with_reason():
    """D-530 덧 3: 후보 칸 없음 — 낮은 확신 · RNF는 알람 없음 + 사유 한 칸(no_alarm_reason), 위험도 · 유형은 ml 칸에."""
    r = ml.route(pred(NONE=0.2, PWF=0.45, HDF=0.35), None, 0.5)
    assert r["alarm"] is False and r["no_alarm_reason"] == "low_confidence" and r["ml"]["risk"] == pytest.approx(0.8)
    r = ml.route(pred(NONE=0.1, RNF=0.7, TWF=0.2), None, 0.5)
    assert r["alarm"] is False and r["no_alarm_reason"] == "rnf" and r["group"] is None


def test_d530_route_output_has_no_candidate_key():
    """실패 쪽: 다섯 갈래(문턱 아래 · 경계 · ML 유형 · 낮은 확신 · 예측 실패) 어디에도 candidate 칸이 없고 칸 이름은 같다."""
    outs = [
        ml.route(pred(NONE=0.6, HDF=0.3, PWF=0.1), None, 0.5),
        ml.route(pred(NONE=0.2, PWF=0.45, HDF=0.35), BOUNDARY, 0.5),
        ml.route(pred(NONE=0.1, OSF=0.8, PWF=0.1), None, 0.5),
        ml.route(pred(NONE=0.2, PWF=0.45, HDF=0.35), None, 0.5),
        ml.route(None, None, 0.5),
    ]
    assert all(set(r) == {"alarm", "group", "group_source", "ml", "no_alarm_reason"} for r in outs)
    assert [r["no_alarm_reason"] for r in outs] == [None, None, None, "low_confidence", "model_failure"]


def test_twf_probability_folds_into_none():
    """D-526 덧 6: 모델은 5클래스 그대로, TWF 확률은 정상(NONE)에 더하고 TWF 칸은 지움 — 위험도에서 TWF 몫이 빠지고 유형 후보에 TWF 0."""
    assert "TWF" not in ml.TYPE_TO_GROUP
    r = ml.route(pred(NONE=0.3, TWF=0.6, HDF=0.1), None, 0.5)  # TWF로 위험하던 예측 → 알람 없음 · 기록 없음
    assert r["alarm"] is False and r["no_alarm_reason"] is None and r["ml"]["risk"] == pytest.approx(0.1)
    assert [x["type"] for x in r["ml"]["top_k"]] == ["HDF"]
    r = ml.route(pred(NONE=0.1, TWF=0.35, HDF=0.55), BOUNDARY, 0.5)  # 다른 유형이 남으면 그대로 알람
    assert (r["alarm"], r["group"], r["ml"]["risk"]) == (True, "CL", pytest.approx(0.55))
    # 1위가 TWF였어도 다음 유형으로(확신 낮으면 알람 없음 + 사유)
    r = ml.route(pred(NONE=0.05, TWF=0.4, OSF=0.3, PWF=0.25), None, 0.5)
    assert r["no_alarm_reason"] == "low_confidence"
    assert [x["type"] for x in r["ml"]["top_k"]] == ["OSF", "PWF"]
    assert r["ml"]["risk"] == pytest.approx(0.55) and r["ml"]["confidence"] == 0.3


def test_twf_without_probabilities_is_normal():
    """실패 쪽: probabilities 없이 type TWF만 오면 NONE과 같게(알람 · 후보 0) — TW 증상군으로 새지 않음."""
    twf = ml.route({"type": "TWF", "probability": 0.9, "model_release": "m"}, None, 0.5)
    none = ml.route({"type": "NONE", "probability": 0.9, "model_release": "m"}, None, 0.5)
    assert twf == none and twf["alarm"] is False and twf["no_alarm_reason"] is None and twf["ml"]["top_k"] == []


def test_vc29_model_failure_record_without_alarm():
    r = ml.route(None, BOUNDARY, 0.5)
    assert r == {"alarm": False, "group": None, "group_source": None, "ml": None, "no_alarm_reason": "model_failure"}


def test_threshold_is_one_user_setting(monkeypatch):
    monkeypatch.delenv("C1A15_ML_ALARM_THRESHOLD", raising=False)
    assert ml.threshold() == 0.5
    monkeypatch.setenv("C1A15_ML_ALARM_THRESHOLD", "0.7")
    assert (
        ml.threshold() == 0.7 and ml.route(pred(NONE=0.35, HDF=0.65), None)["alarm"] is False
    )  # 문턱 0.7 → 위험도 0.65 알람 없음
    monkeypatch.setenv("C1A15_ML_ALARM_THRESHOLD", "1.5")
    with pytest.raises(ValueError):
        ml.threshold()


def test_vc13_event_card_five_sensors_no_labels():
    snap = {
        "air_temperature_k": 302.7,
        "process_temperature_k": 310.5,
        "rotational_speed_rpm": 1511.0,
        "torque_nm": 37.7,
        "tool_wear_min": 60,
        "temp_diff_k": 7.8,
        "power_w": 5966,
        "quality_grade": "M",
    }
    ec = ml.event_card("C1A-1", "M-03", snap, {"row_id": 4494, "split": "demo"})
    assert set(ec) == {"event_id", "source_mode", "equipment_id", "sensor_snapshot", "alarm", "data_lineage"}
    assert (
        set(ec["sensor_snapshot"]) == set(ml.SENSOR_FIELDS) and ec["source_mode"] == "ml_predicted"
    )  # 온도 차 · 출력 · 등급 빠짐
