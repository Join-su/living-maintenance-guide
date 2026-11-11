"""C1-a15 Pack 예측 칸 만들기(custom_code@3, EV-001 CH-04 · Pack EV-001 PC-06) — EventCard → 모델 입력. 망 · 부작용 · 앱 import 0 · 정답 0.

두 model release의 입력을 함께 낸다(predict@1은 release feature_schema.fields 칸만 골라 씀 — 실행 설정 한 줄로 모델을 바꾸고 되돌림):
- 자리 모델 mr-c1a15-standin-v1 4칸: 증상군마다 train 알람 경계를 넘은 정도 ÷ train 표준편차(부호 있음: 넘으면 +, 안쪽이면 −).
  경계 · 표준편차 = custom/alarm_rules.json(앱 data/alarm_rules.json 사본, 출처 COPIED_FROM.json). 온도 차 · 출력은 앱 생성기와 같은 식.
- 사용자 모델 mr-ai4i-lgbm-large-v1 6칸(D-264): [type_code, air, proc, rpm, torque, wear] = 제품 등급 숫자(manifest encodings) + 센서 원값.
  계산 칸 4개(공기/공정 비 · rpm×토크 · 토크×마모 · 마모 구간)는 ONNX 그래프 안 — 여기서 계산하지 않음.
"""
import json
import math
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def basis():
    d = json.loads(Path(__file__).with_name("alarm_rules.json").read_text(encoding="utf-8"))
    b = {r["group"]: r["boundaries"] for r in d["rules"]}
    return b, d["train_std"]


TYPE_CODE = {"L": 0, "M": 1, "H": 2}   # mr-ai4i-lgbm-large-v1 manifest feature_schema.encodings.type_code(시험이 manifest와 같은지 봄)


def prepare(request):
    card = request["event_card"]
    s = card["sensor_snapshot"]
    b, std = basis()
    temp_diff = round(s["process_temperature_k"] - s["air_temperature_k"], 1)
    power = round(s["torque_nm"] * s["rotational_speed_rpm"] * 2 * math.pi / 60)
    return {"model_input": {
        "tw_excess_std": (s["tool_wear_min"] - b["TW"]["high"]) / std["tool_wear_min"],
        "cl_excess_std": (b["CL"]["low"] - temp_diff) / std["temp_diff_k"],
        "pw_excess_std": max(b["PW"]["low"] - power, power - b["PW"]["high"]) / std["power_w"],
        "ld_excess_std": (s["torque_nm"] - b["LD"]["high"]) / std["torque_nm"],
        "type_code": TYPE_CODE[card["product_type"]],
        "air": s["air_temperature_k"],
        "proc": s["process_temperature_k"],
        "rpm": s["rotational_speed_rpm"],
        "torque": s["torque_nm"],
        "wear": s["tool_wear_min"],
    }}


def finish(prediction):
    return {"result": {"prediction": prediction}}
