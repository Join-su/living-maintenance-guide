#!/usr/bin/env python3
"""시연 각본 10건(명세 R11)을 만든다. 일회용 생성 스크립트(제품 코드 아님).

- 원본: AI4I 2020 CSV(읽기만, 라벨 열은 build_data.load_rows가 읽자마자 버림). 행 배치 · 알람 판정은 build_data와 같은 규칙.
- 시연 행 = 분할 v2(generation/split, D-227)의 시연(demo) 몫에서 DEMO-01 ~ 10 행(ai4i-split.json demo_udi)으로 고정한다 — 행 · 설비 번호 그대로(D-236 (2)).
  경계는 train 행으로 정하고(build_data와 같음), 각 행이 온도 차 저하 5 · 출력 이탈 2 · 공구 마모 2 · 토크 과부하 1로 알람이 되는지 확인한다. 시간으로 행을 고르지 않는다.
- 평가 카드와 겹치지 않게 eval/cards/*/card_udi_map.json의 행 번호(UDI)만 읽어 확인한다(사용자 결정 2026-10-13). 카드 내용 · 정답은 읽지 않는다.
- 시연 release(data/demo/release.json, CH-11): 시연 구간 · 각본마다 구간 · 증상군 · 끝 모양(입력표, 결정 020 ②) · 시연 구간의 알람 행 목록.
- 각본에는 숨은 원인을 적지 않는다. 화면 추이는 같은 설비의 알람 행 이전 측정 12회(알람 센서 값).
- 각본 행 = 센서 값 · 추이만. 경계 판정 · 증상군은 서버(engine/alarm_rules.py)가 data/alarm_rules.json으로 정한다(FLOW-01).
실행: python3 scripts/build_demo_scenarios.py [--csv PATH]
"""

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("build_data", ROOT / "scripts" / "build_data.py")
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)

COUNTS = {"CL": 5, "PW": 2, "TW": 2, "LD": 1}
TREND_N = 12


def eval_udis() -> set[str]:
    out = set()
    for p in sorted((ROOT / "eval" / "cards").glob("*/card_udi_map.json")) + [ROOT / "eval" / "cards" / "card_udi_map.json"]:
        if p.is_file():
            out |= set(json.loads(p.read_text(encoding="utf-8")).values())
    return out


# 시연 입력표(packet demo_input_table, 사용자 결정 020 ②) · e2e/tests/demo.spec.ts의 끝 모양 — release에 적는 분류
ENDINGS = {"DEMO-01": "원인 확정", "DEMO-02": "파트장 승인 뒤 확정", "DEMO-03": "주간 처리", "DEMO-04": "원인 확정(확인 질문)",
           "DEMO-05": "숙련자 인계 마감(재가동 여전히 밖)", "DEMO-06": "이상 없음 · 감시 마감", "DEMO-07": "원인 확정(중간에 api 재시작)",
           "DEMO-08": "이상 없음 · 감시 마감(B 시작 전 체크)", "DEMO-09": "원인 확정", "DEMO-10": "숙련자 인계 마감(확인 불가 반복)"}
COVER = {"정상(이상 없음 · 감시)": ["DEMO-06", "DEMO-08"], "안전 진행(파트장 승인 · B 시작 전 체크)": ["DEMO-02", "DEMO-08"],
         "인계": ["DEMO-05", "DEMO-10"], "실패 재현(중간 재시작에서 이어 감)": ["DEMO-07"]}
SPLIT_JSON = ROOT / "generation" / "split" / "ai4i-split.json"


def num(v: float) -> float | int:
    return int(v) if float(v).is_integer() else round(float(v), 1)


def snapshot(r: dict) -> dict:
    return {"air_temperature_k": r["air"], "process_temperature_k": r["proc"], "rotational_speed_rpm": r["rpm"], "torque_nm": r["torque"],
            "tool_wear_min": r["wear"], "temp_diff_k": r["temp_diff"], "power_w": r["power"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(bd.DEFAULT_CSV))
    args = ap.parse_args()
    rows = bd.load_rows(args.csv)
    placed, _ = bd.place_rows(rows)
    split = bd.load_split()
    seg = {}
    with open(bd.SPLIT_FILE, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            seg[r["UDI"]] = int(r["segment"])
    th = bd.thresholds([r for r in placed if split[r["udi"]] == "train"])
    excluded = eval_udis()
    history_udis = {json.loads(line)["source"]["udi"] for line in (ROOT / "data/history/maintenance_history.jsonl").read_text().splitlines() if line.strip()}
    pinned = [str(u) for u in json.loads(SPLIT_JSON.read_text(encoding="utf-8"))["demo_udi"]]
    by_udi = {r["udi"]: r for r in placed}
    chosen, by_group, release_rows = [], dict.fromkeys(COUNTS, 0), []
    for n, u in enumerate(pinned, 1):
        r = by_udi[u]
        if split[u] != "demo" or u in excluded or u in history_udis:
            raise SystemExit(f"DEMO-{n:02d} 행 {u}: 시연 몫이 아니거나 카드 · 이력과 겹침")
        hits = bd.detect(r, th)
        if not hits:
            raise SystemExit(f"DEMO-{n:02d} 행 {u}: train 경계로 알람이 아님")
        hit = hits[0]
        by_group[hit["group"]] += 1
        prior = [p for p in placed if p["equipment_id"] == r["equipment_id"] and p["pos"] < r["pos"]][-TREND_N:]
        sid = f"DEMO-{n:02d}"
        chosen.append({
            "scenario_id": sid, "udi": r["udi"], "equipment_id": r["equipment_id"],
            "product_type": r["quality_grade"],  # AI4I 원본 Type(L · M · H) — Pack 알람 입력 product_type(D-264, EV-001 ES-08)
            "sensor_snapshot": snapshot(r),
            "trend": [{"at": bd.iso(p["at"]), "value": num(p[hit["key"]])} for p in prior],
        })
        release_rows.append({"scenario_id": sid, "udi": r["udi"], "segment": seg[u], "equipment_id": r["equipment_id"], "group": hit["group"], "ending": ENDINGS[sid]})
    if by_group != COUNTS:
        raise SystemExit(f"시연 증상군 구성이 다름: {by_group}")
    split_sha = hashlib.sha256(bd.SPLIT_FILE.read_bytes()).hexdigest()
    segments = sorted({seg[u] for u, sp in split.items() if sp == "demo"})
    out = {"source": {"name": "UCI AI4I 2020", "sha256": hashlib.sha256(Path(args.csv).read_bytes()).hexdigest(), "split": "v2 demo",
                      "split_file_sha256": split_sha, "segments": segments},
           "rule": "R11: 분할 v2 시연 몫의 DEMO-01 ~ 10 행 고정(행 · 설비 번호 그대로), 경계 = train 행, 숨은 원인 없음", "scenarios": chosen}
    (ROOT / "data" / "demo" / "scenarios.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    alarm_rows = {g: [] for g in COUNTS}
    for r in placed:
        if split[r["udi"]] == "demo":
            hits = bd.detect(r, th)
            if hits:
                alarm_rows[hits[0]["group"]].append(r["udi"])
    release = {"release": "demo-v2", "split_file_sha256": split_sha, "segments": segments, "scenarios": release_rows,
               "cover": {"네 증상군": dict(by_group), **COVER},
               "alarm_rows_by_group": {g: {"count": len(v), "udi": v} for g, v in alarm_rows.items()},
               "rule": "시연 구간의 행만, 경계 = train 행. 각본 밖 알람 행은 ML 알람 길(ES-04) · 시연 연습용 — 평가 카드 · 이력과 겹치지 않음"}
    (ROOT / "data" / "demo" / "release.json").write_text(json.dumps(release, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"scenarios={len(chosen)} by_group={by_group} segments={segments}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
