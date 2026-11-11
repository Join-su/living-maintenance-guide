#!/usr/bin/env python3
"""평가 카드 행 고르기(EV-001 CH-10 · D-236 (3)): 분할 v2의 val · test 몫에서 알람 행을 증상군 층화로 30건씩 고른다.

- 원본 CSV는 scripts/build_data.load_rows로만 읽는다(고장 라벨 6열은 읽자마자 버림). 경계 = train 행(build_data와 같음).
- 행 시각 · 설비 번호 · 직전 12회 측정은 지금 배치 규칙 그대로(CH-02b까지).
- 만드는 것: eval/cards/<val|test>/card_inputs.json(작성자에게 줄 알람 입력, 라벨 없음) · card_udi_map.json(작성자에게 주지 않음) · selection.json(고른 규칙 · 몫 · 후보 수).
- 카드 내용(숨은 원인 · 메모)은 격리 작성자가 따로 쓴다 — 이 도구는 쓰지 않는다.
실행: python3 eval/select_card_rows.py --plan 가|나 [--dry-run]
  가: 분할 그대로(val에 CL 알람 없음) · 나: test 구간 56의 CL 알람 행을 val · test로 번갈아 나눔(질문 007-09 → D-246 = 나)
- 안 나의 예외(분할 파일은 그대로, D-246 (2)): selection.json의 exception에 구간 56 CL 알람 행 전부 · val로 간 행 · test로 간 행 · 나눈 규칙을 적고,
  검사 도구(eval/validate_cards.py · scripts/check_demo_separation.py)가 그 기록으로 val 카드의 test 몫 행을 허용한다.
- 한계(D-246 (3)): 같은 공구 교체 구간의 이웃 행이 val · test로 갈라져 test CL 점수가 조금 낙관적일 수 있다.
"""
import argparse
import importlib.util
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("build_data", ROOT / "scripts" / "build_data.py")
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)

SEED = 2026
GROUP_NAME = {"TW": "공구 마모 경고", "PW": "출력 이탈", "CL": "온도 차 저하", "LD": "토크 과부하"}
QUOTA = {"가": {"val": {"PW": 14, "TW": 9, "LD": 7}, "test": {"CL": 10, "PW": 8, "TW": 6, "LD": 6}},
         "나": {"val": {"CL": 7, "PW": 11, "TW": 7, "LD": 5}, "test": {"CL": 8, "PW": 10, "TW": 6, "LD": 6}}}
SPLIT_SEGMENT = 56   # 안 나: 이 구간의 CL 알람 행만 val · test로 나눈다
PREFIX = {"val": "EC-V", "test": "EC-T"}
TREND_KEYS = [("temp_diff", "temp_diff_k"), ("power", "power_w"), ("torque", "torque_nm"), ("wear", "tool_wear_min"), ("rpm", "rotational_speed_rpm")]


def num(v):
    return int(v) if float(v).is_integer() else round(float(v), 1)


def card_input(cid, r, hit, placed):
    prior = [p for p in placed if p["equipment_id"] == r["equipment_id"] and p["pos"] < r["pos"]][-12:]
    return {"card_id": cid, "equipment_id": r["equipment_id"], "occurred_at": bd.iso(r["at"]),
            "symptom_group": {"code": hit["group"], "name": GROUP_NAME[hit["group"]]},
            "alarm": {"sensor": hit["sensor"], "value": num(hit["value"]), "unit": hit["unit"], "boundary": num(hit["boundary"]),
                      "direction": "경계 아래" if hit["direction"] == "low" else "경계 위"},
            "sensor_snapshot": {"quality_grade": r["quality_grade"], "air_temperature_k": r["air"], "process_temperature_k": r["proc"],
                                "rotational_speed_rpm": r["rpm"], "torque_nm": r["torque"], "tool_wear_min": r["wear"],
                                "temp_diff_k": r["temp_diff"], "power_w": r["power"]},
            "recent_12": [{"at": bd.iso(p["at"]), **{snap: num(p[k]) for k, snap in TREND_KEYS}} for p in prior]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", choices=sorted(QUOTA), required=True)
    ap.add_argument("--csv", default=str(bd.DEFAULT_CSV))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    rows = bd.load_rows(a.csv)
    placed, _ = bd.place_rows(rows)
    split = bd.load_split()
    seg = {}
    import csv
    with open(bd.SPLIT_FILE, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            seg[r["UDI"]] = int(r["segment"])
    th = bd.thresholds([r for r in placed if split[r["udi"]] == "train"])
    pools = {"val": {}, "test": {}}
    cl56 = []
    for r in placed:   # 배치 순서
        sp = split[r["udi"]]
        if sp not in pools:
            continue
        hits = bd.detect(r, th)
        if not hits:
            continue
        hit = hits[0]
        if a.plan == "나" and sp == "test" and seg[r["udi"]] == SPLIT_SEGMENT and hit["group"] == "CL":
            cl56.append((r, hit))
            continue
        pools[sp].setdefault(hit["group"], []).append((r, hit))
    for i, item in enumerate(cl56):   # 번갈아(배치 순서, seed 없음 — 결정적): 짝수 → test, 홀수 → val (test 8 · val 7)
        pools["val" if i % 2 else "test"].setdefault("CL", []).append(item)
    exception = None
    if a.plan == "나":
        exception = {"segment": SPLIT_SEGMENT, "decision": "D-246", "split_file_unchanged": True,
                     "cl_alarm_rows": [r["udi"] for r, _ in cl56],
                     "to_val": [r["udi"] for i, (r, _) in enumerate(cl56) if i % 2], "to_test": [r["udi"] for i, (r, _) in enumerate(cl56) if not i % 2],
                     "rule": "train 경계로 CL 알람인 구간 56 행을 배치 순서로 번갈아(0번째 → test, 1번째 → val, …) — seed 없음",
                     "limit": "이웃 행이 val · test로 갈라져 test CL 점수가 조금 낙관적일 수 있음"}
    out = {}
    for sp in ("val", "test"):
        rng = random.Random(f"{SEED}-{sp}")
        picked = []
        for g, n in QUOTA[a.plan][sp].items():
            cand = pools[sp].get(g, [])
            if len(cand) < n:
                raise SystemExit(f"{sp} {g} 후보 {len(cand)} < {n}")
            picked += rng.sample(cand, n)
        picked.sort(key=lambda x: x[0]["pos"])
        inputs = [card_input(f"{PREFIX[sp]}{i:02d}", r, hit, placed) for i, (r, hit) in enumerate(picked, 1)]
        out[sp] = {"inputs": inputs, "udi": {c["card_id"]: r["udi"] for c, (r, _) in zip(inputs, picked)},
                   "selection": {"plan": a.plan, "seed": f"{SEED}-{sp}", "split_file_sha256": bd.sha256(bd.SPLIT_FILE),
                                 "quota": QUOTA[a.plan][sp], "candidates": {g: len(v) for g, v in sorted(pools[sp].items())},
                                 "picked": dict(Counter(i["symptom_group"]["code"] for i in inputs)),
                                 "segments": sorted({seg[r["udi"]] for r, _ in picked}),
                                 "rule": "train 경계로 알람인 행 · 증상군별 몫 · seed 표본 · 배치 순서로 번호" + (f" · 안 나: test 구간 {SPLIT_SEGMENT} CL 알람 행을 번갈아 val · test" if a.plan == "나" else ""),
                                 "exception": exception}}
        print(sp, json.dumps(out[sp]["selection"], ensure_ascii=False))
    assert not set(out["val"]["udi"].values()) & set(out["test"]["udi"].values())
    if a.dry_run:
        return 0
    for sp, o in out.items():
        d = ROOT / "eval" / "cards" / sp
        d.mkdir(parents=True, exist_ok=True)
        (d / "card_inputs.json").write_text(json.dumps(o["inputs"], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (d / "card_udi_map.json").write_text(json.dumps(o["udi"], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (d / "selection.json").write_text(json.dumps(o["selection"], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
