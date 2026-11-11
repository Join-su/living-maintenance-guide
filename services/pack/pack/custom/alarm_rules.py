"""알람 경계 판정(FLOW-01 진단 처리): 센서 값이 경계를 넘으면 증상군 · 알람 내용을 정한다.

경계는 data/alarm_rules.json(학습용 train 행으로 정한 값, 적재 자료)에서 읽는다.
여러 경계를 넘으면 priority 순서의 첫 증상군.
"""

import json
from pathlib import Path

SNAPSHOT_KEY = {"power": "power_w", "temp_diff": "temp_diff_k", "torque": "torque_nm", "wear": "tool_wear_min"}
TREND_N = 12  # R11: 같은 설비의 알람 행 이전 측정 12회


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def detect(snapshot: dict, table: dict) -> dict | None:
    rules = {r["group"]: r for r in table["rules"]}
    for group in table["priority"]:
        rule = rules[group]
        v = snapshot[SNAPSHOT_KEY[rule["key"]]]
        for side, boundary in rule["boundaries"].items():
            if (side == "low" and v < boundary) or (side == "high" and v > boundary):
                return {
                    "group": group,
                    "alarm": {
                        "sensor": rule["sensor"],
                        "value": v,
                        "unit": rule["unit"],
                        "boundary": boundary,
                        "direction": "경계 아래" if side == "low" else "경계 위",
                        "trend_title": f"{rule['sensor']} 추이 · 최근 {TREND_N}회 측정",
                    },
                }
    return None
