"""D-536 ③ 보강 1 — 잡음 섞인 이력으로 같은 그래프 · 같은 대조. 원본 이력 손 0(임시 폴더 사본) · 씨앗 고정 · 비율 몇 단계.
잡음 셋(이력 한 건마다 비율 p로 따로 뽑음): 점검 결과 칸 빔(이상 → 빈 칸) · 원인 잘못 적힘(같은 증상군의 다른 원인) · 조치 빠짐(조치 단계 지움).
그래프는 비율마다 따로 꼬리표(noise-<p>)로 만들고 끝나면 지움. 대조는 check_v3.compare 그대로 + '근거 n ≥ 최소'로 거른 대조.
출력은 개수만(results/잡음.json · 잡음.md).

kg-test/.venv-kg/bin/python -B kg-test/graph-v3/noise.py --history <이력> --gold <앱 저장소>/data/manuals/MG-MM.md"""
import argparse
import json
import random
import tempfile
from collections import defaultdict
from pathlib import Path

import build_v3 as B
import check_v3 as C
from conn import driver

RATES = (0.0, 0.05, 0.1, 0.2, 0.3)
SEED = 20261103
MIN_N = (1, 3, "몫 30%")   # 몫 30% = 같은 원인의 연결 가운데 가장 큰 근거 건수의 30% 이상만


def noisy(recs, p, seed=SEED):
    rng = random.Random(f"{seed}-{p}")
    by_group = defaultdict(set)
    for r in recs:
        if r["outcome"].get("cause_code"):
            by_group[r["symptom_group"]["name"]].add((r["outcome"]["cause_code"], r["outcome"]["cause_name"]))
    out, n = [], {"빈 점검 결과": 0, "잘못 적힌 원인": 0, "빠진 조치": 0}
    for r in recs:
        r = json.loads(json.dumps(r, ensure_ascii=False))
        if rng.random() < p:
            ab = [s for s in r["steps"] if s["kind"] == "check" and s["result"] == "이상"]
            if ab:
                rng.choice(ab)["result"] = ""
                n["빈 점검 결과"] += 1
        if rng.random() < p and r["outcome"].get("cause_code"):
            others = sorted(by_group[r["symptom_group"]["name"]] - {(r["outcome"]["cause_code"], r["outcome"]["cause_name"])})
            if others:
                r["outcome"]["cause_code"], r["outcome"]["cause_name"] = rng.choice(others)
                n["잘못 적힌 원인"] += 1
        if rng.random() < p:
            acts = [i for i, s in enumerate(r["steps"]) if s["kind"] == "action"]
            if acts:
                del r["steps"][rng.choice(acts)]
                n["빠진 조치"] += 1
        out.append(r)
    return out, n


def main(history, gold_path, out):
    recs = [json.loads(l) for l in Path(history).read_text(encoding="utf-8").splitlines() if l.strip()]
    gold = C.gold_rows(gold_path, B.number_map())
    rows = []
    with driver() as drv, tempfile.TemporaryDirectory() as d:
        for p in RATES:
            tag = f"noise-{int(p * 100):02d}"
            nr, n = noisy(recs, p)
            f = Path(d) / f"{tag}.jsonl"
            f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in nr), encoding="utf-8")
            B.load(f, tag, drv=drv)
            _, cand, ind, rem, _, _ = C.graph_facts(drv, tag)
            for m in MIN_N:
                if isinstance(m, int):
                    keep = lambda xs, m=m: [x for x in xs if x["n"] >= m]
                else:
                    def keep(xs):
                        top = defaultdict(int)
                        for x in xs:
                            top[x["c"]] = max(top[x["c"]], x["n"])
                        return [x for x in xs if x["n"] >= 0.3 * top[x["c"]]]
                res, links, extra = C.compare(gold, keep(cand), keep(ind), keep(rem))
                per = {v: sum(1 for r in res if r["res"]["원인"] == v) for v in ("맞음", "빠짐", "틀림")}
                rows.append({"rate": p, "min_n": m, "noise": n, "cause": per, "links": links, "extra_causes": len(extra)})
            with drv.session() as s:
                B.clear(s, tag)
    out.mkdir(parents=True, exist_ok=True)
    (out / "잡음.json").write_text(json.dumps({"seed": SEED, "rows": rows}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    md = [f"잡음 섞인 이력 대조(씨앗 {SEED} · 원본 이력 손 0) — 원인 14 단위 · 연결 44 단위", "",
          "| 잡음 비율 | 넣은 잡음(빈 점검 결과 · 잘못 적힌 원인 · 빠진 조치) | 근거 최소 | 원인 맞음 · 빠짐 · 틀림 | 연결 맞음 · 빠짐 · 틀림 |", "|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['rate']:.0%} | {r['noise']['빈 점검 결과']} · {r['noise']['잘못 적힌 원인']} · {r['noise']['빠진 조치']} | {r['min_n'] if isinstance(r['min_n'], str) else str(r['min_n']) + '건'} | "
                  f"{r['cause']['맞음']} · {r['cause']['빠짐']} · {r['cause']['틀림']} | {r['links']['맞음']} · {r['links']['빠짐']} · {r['links']['틀림']} |")
    (out / "잡음.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    a = ap.parse_args()
    main(a.history, a.gold, Path(a.out))
