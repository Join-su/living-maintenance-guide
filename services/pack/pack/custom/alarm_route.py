"""ML 알람 앞단 규칙(EV-001 CH-04 · Q-1 (나) · D-237 · D-238) — 순수 함수, DB · 망 0.

입력 = C1-a15 Pack 예측(predict@1: type · probability · probabilities · model_release)과 센서 경계 판정.
- 위험도 = 1 − P(NONE). 위험도 ≥ 문턱이면 알람 후보, 미만이면 알람 없음(기록도 없음).
- TWF는 ML 알람에서 뺌(D-526 덧 6): 모델은 5클래스 그대로, P(TWF)를 P(NONE)에 더하고 TWF 칸은 지움
  (probabilities 없이 type TWF면 NONE과 같음) — 위험도에서 TWF 몫이 빠지고 유형 후보에 TWF가 나오지 않음.
- 증상군: 경계 규칙이 넘으면 그 증상군(sensor_boundary). 아니면 ML 유형을 증상군 표
  (HDF → CL · PWF → PW · OSF → LD)로, 확신도(그 유형 확률) ≥ 문턱일 때만(ml_type).
  그 밖(확신도 < 문턱 · RNF)은 알람 없음 + 사유(no_alarm_reason low_confidence · rnf)
  — 앱은 감사 한 줄만(D-529 · D-530 덧 3).
- 예측 실패(모델 파일 없음 · 형식 오류 · 예외) → 알람 없음 + 사유 model_failure.
- 문턱 = 앱 사용자 설정 한 곳 C1A15_ML_ALARM_THRESHOLD(기본 0.5). 모델 release threshold_config는 기록만.
- R4(원인 순위)는 ML 값을 입력으로 쓰지 않는다(D-238) — ml 칸은 알람 행에 저장만.
앱이 Pack을 부르는 길(C1A15_ALARM=ml)은 앱 → Pack 연결 결정 뒤(답장 007-06 · 007-11-01) — 이 판은 규칙만.
"""

import os

TYPE_TO_GROUP = {"HDF": "CL", "PWF": "PW", "OSF": "LD"}
DEFAULT_THRESHOLD = 0.5
SENSOR_FIELDS = ("air_temperature_k", "process_temperature_k", "rotational_speed_rpm", "torque_nm", "tool_wear_min")


def threshold() -> float:
    """사용자 설정 한 곳(C1A15_ML_ALARM_THRESHOLD, 0 < 값 < 1)."""
    raw = os.environ.get("C1A15_ML_ALARM_THRESHOLD")
    v = DEFAULT_THRESHOLD if raw in (None, "") else float(raw)
    if not 0 < v < 1:
        raise ValueError("C1A15_ML_ALARM_THRESHOLD는 0과 1 사이")
    return v


def event_card(event_id: str, equipment_id: str, sensor_snapshot: dict, lineage: dict | None = None) -> dict:
    """EventCard(⚑ 초안 1절): 센서 5칸만(온도 차 · 출력은 Pack 안에서 계산), 고장 라벨 칸 없음."""
    return {
        "event_id": event_id,
        "source_mode": "ml_predicted",
        "equipment_id": equipment_id,
        "sensor_snapshot": {k: float(sensor_snapshot[k]) for k in SENSOR_FIELDS},
        "alarm": {"source": "none"},
        "data_lineage": dict(lineage or {}),
    }


def route(prediction: dict | None, boundary_hit: dict | None, th: float | None = None) -> dict:
    """prediction = Pack 예측(실패면 None), boundary_hit = engine.alarm_rules.detect 결과(없으면 None).

    돌려줌: {alarm: bool, group, group_source, ml: {...} | None,
             no_alarm_reason: None | low_confidence · rnf · model_failure}.
    """
    th = threshold() if th is None else th
    crossed = boundary_hit is not None
    if prediction is None:
        return {"alarm": False, "group": None, "group_source": None, "ml": None, "no_alarm_reason": "model_failure"}
    probs = dict(prediction.get("probabilities") or {prediction["type"]: prediction["probability"]})
    if "TWF" in probs:  # D-526 덧 6: TWF 확률 → NONE
        probs["NONE"] = round(probs.get("NONE", 0.0) + probs.pop("TWF"), 6)
    risk = round(1 - probs.get("NONE", 0.0), 6)
    top = sorted(((t, p) for t, p in probs.items() if t != "NONE"), key=lambda x: (-x[1], x[0]))[:3]
    top_k = [{"type": t, "probability": p, "group_suggestion": TYPE_TO_GROUP.get(t)} for t, p in top]
    ml = {
        "model_release": prediction.get("model_release"),
        "risk": risk,
        "alarm": risk >= th,
        "top_k": top_k,
        "confidence": top[0][1] if top else 0.0,
        "threshold": th,
        "confirmed": False,
    }
    if risk < th:
        return {"alarm": False, "group": None, "group_source": None, "ml": ml, "no_alarm_reason": None}
    if crossed:
        return {
            "alarm": True,
            "group": boundary_hit["group"],
            "group_source": "sensor_boundary",
            "ml": {**ml, "group_source": "sensor_boundary"},
            "no_alarm_reason": None,
        }
    t, p = top[0] if top else ("NONE", 0.0)
    if t in TYPE_TO_GROUP and p >= th:
        return {
            "alarm": True,
            "group": TYPE_TO_GROUP[t],
            "group_source": "ml_type",
            "ml": {**ml, "group_source": "ml_type"},
            "no_alarm_reason": None,
        }
    reason = "rnf" if t == "RNF" else "low_confidence"
    return {"alarm": False, "group": None, "group_source": None, "ml": ml, "no_alarm_reason": reason}
