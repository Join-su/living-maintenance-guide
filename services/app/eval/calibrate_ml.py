#!/usr/bin/env python3
"""ML 보정 점수표(EV-001 CH-04 · D-237 (1) 보정 확인) — 평가 전용, Pack 밖. 이 판에서 AI4I 고장 라벨을 읽는 유일한 도구.

val 몫(generation/split) 행마다 EventCard(센서 5칸, 라벨 없음 — c1a15.alarms.ml.event_card)를 만들어 C1-a15 Pack 예측(predict@1)에 넣고,
예측 위험도(1 − P(NONE))를 실제 고장(Machine failure · 유형 열)과 맞춰 본다: 구간별 실제 비율 · Brier · 문턱 0.5 혼동표 · 유형 대조.
라벨은 이 도구 안에서만 쓰고 Pack · 앱 · 생성기로 가지 않는다. 같은 입력 → 같은 표(VC-17).
실행(services/app에서 · packrt가 깔린 Pack 파이썬): <Pack 가상환경>/bin/python -B eval/calibrate_ml.py --pack ../pack/pack
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT.parent / "manifact_10" / "data" / "raw" / "ai4i2020.csv"
SPLIT = ROOT / "generation" / "split" / "ai4i-split-by-udi.csv"
TYPES = ["TWF", "HDF", "PWF", "OSF", "RNF"]


def load_ml():
    spec = importlib.util.spec_from_file_location("c1a15_ml", ROOT / "server" / "src" / "c1a15" / "alarms" / "ml.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--split", default="val")
    ap.add_argument("--out")
    a = ap.parse_args()
    from packrt.run.engine import Runtime

    ml = load_ml()
    with open(SPLIT, encoding="utf-8") as f:
        split = {r["UDI"]: r["split"] for r in csv.DictReader(f)}
    rt = Runtime(a.pack)
    rows, release = [], None
    with open(a.csv, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if split[r["UDI"]] != a.split:
                continue
            snap = {"air_temperature_k": float(r["Air temperature [K]"]), "process_temperature_k": float(r["Process temperature [K]"]),
                    "rotational_speed_rpm": float(r["Rotational speed [rpm]"]), "torque_nm": float(r["Torque [Nm]"]), "tool_wear_min": float(r["Tool wear [min]"])}
            out = rt.run({"kind": "predict", "event_card": ml.event_card(f"CAL-{r['UDI']}", "-", snap, {"row_id": int(r["UDI"]), "split": a.split})})
            p = out["output"]["prediction"]
            release = p.get("model_release")
            rows.append({"risk": 1 - p["probabilities"]["NONE"], "type": p["type"], "fail": int(r["Machine failure"]),
                         "labels": [t for t in TYPES if r[t] == "1"]})
    n, pos = len(rows), sum(x["fail"] for x in rows)
    bins = []
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        b = [x for x in rows if lo <= x["risk"] < hi or (i == 9 and x["risk"] == 1.0)]
        bins.append({"risk": f"{lo:.1f}–{hi:.1f}", "rows": len(b), "mean_risk": round(sum(x["risk"] for x in b) / len(b), 4) if b else None,
                     "actual_failure_rate": round(sum(x["fail"] for x in b) / len(b), 4) if b else None})
    th = ml.DEFAULT_THRESHOLD
    tp = sum(1 for x in rows if x["risk"] >= th and x["fail"])
    fp = sum(1 for x in rows if x["risk"] >= th and not x["fail"])
    fn = sum(1 for x in rows if x["risk"] < th and x["fail"])
    tn = n - tp - fp - fn
    types = {}
    for x in rows:
        if x["risk"] >= th:
            key = f"{x['type']} → {'+'.join(x['labels']) or ('고장(유형 없음)' if x['fail'] else '정상')}"
            types[key] = types.get(key, 0) + 1
    rep = {"tool": "eval/calibrate_ml.py", "split": a.split, "split_file_sha256": hashlib.sha256(SPLIT.read_bytes()).hexdigest(),
           "source_sha256": hashlib.sha256(Path(a.csv).read_bytes()).hexdigest(), "model_release": release, "pack_revision": rt.revision,
           "rows": n, "failures": pos, "brier": round(sum((x["risk"] - x["fail"]) ** 2 for x in rows) / n, 5), "bins": bins,
           "at_threshold": {"threshold": th, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                            "precision": round(tp / (tp + fp), 4) if tp + fp else None, "recall": round(tp / pos, 4) if pos else None},
           "predicted_type_vs_label(risk ≥ 문턱)": dict(sorted(types.items(), key=lambda kv: -kv[1])),
           "note": "자리 모델(손 가중치, 학습 0)은 경계 규칙을 확률 모양으로 옮긴 것 — 이 표는 보정 '확인'용, 성능 주장 아님. 사용자 ONNX가 오면 같은 도구로 다시."}
    text = json.dumps(rep, ensure_ascii=False, indent=1)
    out_path = Path(a.out) if a.out else ROOT / "eval" / "ml-calibration" / f"{a.split}-{release}.json"
    out_path.write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("rows", "failures", "brier", "at_threshold")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
