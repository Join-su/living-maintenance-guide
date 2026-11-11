"""D-531 · D-533 비교 — 같은 시험 사례 100건에 세 방식(+ 2′ · 옛 3)을 돌려 갈래별로 잰다. 출력은 비율 · 개수만(사례별 정답 0).

python kg-test/evaluate.py [--data <폴더>] [--out <폴더>] [--node <node.yaml>]   # 기본 = kg-test/data · kg-test/results · kg-test/node.yaml
3 = D-533 관계 질의 노드(관계 층 + 규칙 표 · kg_node.py) · 3(옛) = D-531 관계 셈(methods.relation · 지우지 않고 나란히)
잴 것: 불러와야 할 것 중 불러온 비율(recall) · 불러온 것 중 맞는 비율(precision) · 첫 단계 맞힘 · 권한 위반(등급 A가 아닌데 '단독') ·
매뉴얼 밖 원인일 때 맞는 호출 · 인계를 불러왔나 · 설비 조치(5절)를 불러왔나."""
import argparse
import json
from pathlib import Path

import yaml

import kg_node as N
import methods as M

HERE = Path(__file__).resolve().parent
CATS = ["normal", "E1", "E2", "E3", "E4", "E5", "mixed"]
CAT_NAME = {"normal": "보통", "E1": "E1 계절", "E2": "E2 라인 동시", "E3": "E3 연쇄", "E4": "E4 직전 정비", "E5": "E5 센서", "mixed": "섞인", "all": "전체"}


def _rows(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def score(k, rules, case, out):
    gold = case["gold"]
    g, loaded = set(gold["steps"]), [s["section"] for s in out["steps"]]
    hit = len(g & set(loaded))
    outside = not rules["causes"][gold["cause"]]["in_manual"]
    calls = [s for s in gold["steps"] if k.steps[s]["kind"] == "call"]
    return {"recall": hit / len(g), "precision": hit / len(loaded) if loaded else 0.0, "first": float(bool(loaded) and loaded[0] == gold["first"]),
            "violations": sum(1 for s in out["steps"] if s["mode"] == "단독" and rules["steps"][s["section"]]["grade"] != "A"),
            "loaded": len(loaded), "outside": outside,
            "called": float(all(c in loaded for c in calls)) if outside else None,
            "touched": float(any(k.steps[s]["kind"] == "action" for s in loaded)) if outside else None}


def methods(k, data, case_ids, node_config=HERE / "node.yaml"):
    node = N.Node.from_config(k, node_config, raw=data, case_ids=case_ids)
    old = {n: f for n, f in M.METHODS.items() if not n.startswith("3")}
    return {**old, "3 관계 질의 노드(D-533)": node.run, "3(옛) 관계 셈(D-531)": M.relation}


def run(data, rules_path=HERE / "rules.yaml", node_config=HERE / "node.yaml"):
    rules = yaml.safe_load(Path(rules_path).read_text(encoding="utf-8"))
    k = M.Knowledge(rules_path)
    cases = _rows(data / "cases.jsonl")
    case_ids = [c["record_id"] for c in cases]
    hist = M.History(_rows(data / "history.jsonl"), _rows(data / "occurrences.jsonl"), case_ids)
    ms = methods(k, data, case_ids, node_config)
    per = {name: [] for name in ms}
    for c in cases:
        alarm = {x: c[x] for x in ("record_id", "ts", "machine", "line", "group", "sensors")}   # 정답 칸은 방식에 넘기지 않음
        for name, fn in ms.items():
            per[name].append((c["category"], score(k, rules, c, fn(k, alarm, hist))))
    table = {}
    for name, rows in per.items():
        table[name] = {}
        for cat in CATS + ["all"]:
            rs = [s for ct, s in rows if cat == "all" or ct == cat]
            agg = {"n": len(rs)}
            for f in ("recall", "precision", "first", "loaded"):
                agg[f] = round(sum(s[f] for s in rs) / len(rs), 3)
            agg["violations"] = sum(s["violations"] for s in rs)
            out = [s for s in rs if s["outside"]]
            agg["outside_n"] = len(out)
            agg["called"] = round(sum(s["called"] for s in out) / len(out), 3) if out else None
            agg["touched"] = round(sum(s["touched"] for s in out) / len(out), 3) if out else None
            table[name][cat] = agg
    return table


def markdown(table):
    lines = []
    for f, title in (("recall", "불러와야 할 것 중 불러온 비율"), ("precision", "불러온 것 중 맞는 비율"), ("first", "첫 단계 맞힘"), ("loaded", "불러온 단계 수(평균)")):
        lines += [f"### {title}", "", "| 방식 | " + " | ".join(f"{CAT_NAME[c]}({table[next(iter(table))][c]['n']})" for c in CATS + ["all"]) + " |",
                  "|---|" + "---|" * (len(CATS) + 1)]
        for name, row in table.items():
            lines.append(f"| {name} | " + " | ".join(f"{row[c][f]:.2f}" for c in CATS + ["all"]) + " |")
        lines.append("")
    lines += ["### 매뉴얼 밖 원인 사례(E2 · E4 · E5 · 그 밖) · 권한", "", "| 방식 | 사례 | 맞는 호출 · 인계 불러옴 | 설비 조치(5절)도 불러옴 | 권한 위반(전체) |", "|---|---|---|---|---|"]
    for name, row in table.items():
        a = row["all"]
        lines.append(f"| {name} | {a['outside_n']} | {a['called']:.2f} | {a['touched']:.2f} | {a['violations']} |")
    return M.V0 + "\n\n" + "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(HERE / "data"))
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--node", default=str(HERE / "node.yaml"))
    a = ap.parse_args()
    t = run(Path(a.data), node_config=Path(a.node))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(t, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (out / "table.md").write_text(markdown(t), encoding="utf-8")
    print(markdown(t))
