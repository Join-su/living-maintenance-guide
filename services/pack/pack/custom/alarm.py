"""C1-a15 Pack 알람 node(custom_code@3, 약속 v2 kind alarm · Pack EV-001 PC-02) — 망 · 부작용 · 앱 import 0 · 정답 0.

prepare_alarm: EventCard 센서 5칸 → 계산 칸(출력 · 온도 차, 앱 생성기와 같은 식) → 경계 판정(custom/alarm_rules.detect = 앱 engine/alarm_rules 사본).
decide_alarm: 경계 길(sensor_boundary)은 경계 판정만, ML 길(ml_predicted)은 예측 + 경계로 위험도 · 증상군 · 알람 없음 사유(custom/alarm_route.route = 앱 alarms/ml 사본,
약속 v6 no_alarm_reason — 후보 칸 지움 D-529 · D-530 덧 2).
ML 문턱은 요청 settings.ml_alarm_threshold 한 곳(앱 사용자 설정, D-260 (3)) — 사본에 있는 환경 변수 문턱 함수는 부르지 않는다.
공구 마모(D-546 · 4클래스 모델 v2에 TWF 칸 없음): ML 길이 알람을 내지 않았는데 공구 마모가 TW 경계(alarm_rules.json high 215분)를 넘으면
알람 · TW · sensor_boundary(ml 칸은 그대로 남김). 온도 차 · 출력 · 토크는 ML 길에서 경계만으로 알람 0(위험도 < 문턱이면 알람 없음 — 사용자 결정 'ML만').
예측 node가 실패하면 Run 실패(error.where = run.predict_failure) — 앱이 그것을 사유 model_failure로 읽는다(D-275, LLMOps가 on_error 길을 등록하지 못해 on_error를 쓰지 않음).
"""
import json
import math
from functools import lru_cache
from pathlib import Path

from custom import alarm_route, alarm_rules


@lru_cache(maxsize=1)
def table():
    return json.loads(Path(__file__).with_name("alarm_rules.json").read_text(encoding="utf-8"))


def derived(s):
    """출력 · 온도 차 — 경계 판정용(반올림, 앱 data/demo 각본 · 생성기와 같은 값)."""
    return {"power_w": round(s["torque_nm"] * s["rotational_speed_rpm"] * 2 * math.pi / 60),
            "temp_diff_k": round(s["process_temperature_k"] - s["air_temperature_k"], 1)}


def tw_boundary():
    return next(r for r in table()["rules"] if r["group"] == "TW")["boundaries"]["high"]


def prepare_alarm(request):
    s = request["event_card"]["sensor_snapshot"]
    d = derived(s)
    return {"alarm_basis": {"sensor_derived": d, "hit": alarm_rules.detect({**s, **d}, table())}}


def decide_alarm(request, alarm_basis, prediction=None):
    card, hit = request["event_card"], alarm_basis["hit"]
    out = {"event_id": card["event_id"], "alarm_block": hit["alarm"] if hit else None, "sensor_derived": alarm_basis["sensor_derived"]}
    if card["source_mode"] == "sensor_boundary":
        out.update(alarm=hit is not None, group=hit["group"] if hit else None, group_source="sensor_boundary" if hit else None, ml=None, no_alarm_reason=None)
    else:
        r = alarm_route.route(prediction, hit, request["settings"]["ml_alarm_threshold"])
        if not r["alarm"] and card["sensor_snapshot"]["tool_wear_min"] > tw_boundary():   # 공구 마모는 센서 경계(모델 밖)
            r = {**r, "alarm": True, "group": "TW", "group_source": "sensor_boundary", "no_alarm_reason": None}
        out.update(alarm=r["alarm"], group=r["group"], group_source=r["group_source"], ml=r["ml"], no_alarm_reason=r["no_alarm_reason"])
    return {"result": {"alarm": {k: out[k] for k in ("event_id", "alarm", "group", "group_source", "alarm_block", "sensor_derived", "ml", "no_alarm_reason")}}}
