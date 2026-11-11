"""D-534 같은 100건 비교 — 파일 관계 층 노드(kg_node) ↔ 그래프 DB 노드(graph_node). 출력은 비율 · 개수 · 시간만(사례별 정답 0).
kg-test/.venv-kg/bin/python kg-test/graph/compare.py [--out kg-test/results]   → graph_compare.json · graph_compare.md"""
import argparse
import inspect
import json
import time
from pathlib import Path

import yaml

from conn import KG
from graph_node import TEMPLATES, GraphNode, _where

import evaluate as E   # noqa: E402
import kg_node as N    # noqa: E402
import methods as M    # noqa: E402

CATS = E.CATS + ["all"]


def _agg(rows):
    out = {}
    for cat in CATS:
        rs = [s for c, s in rows if cat == "all" or c == cat]
        out[cat] = {f: round(sum(s[f] for s in rs) / len(rs), 3) for f in ("recall", "precision", "first")}
        out[cat]["n"] = len(rs)
    return out


def text_size(srcs):
    s = "\n".join(srcs)
    return {"chars": len(s), "lines": sum(1 for l in s.splitlines() if l.strip())}


def main(out):
    data = KG / "data"
    rules = yaml.safe_load((KG / "rules.yaml").read_text(encoding="utf-8"))
    k = M.Knowledge()
    cases = E._rows(data / "cases.jsonl")
    ids = [c["record_id"] for c in cases]
    cfg = yaml.safe_load((KG / "node.yaml").read_text(encoding="utf-8"))
    file_node = N.Node.from_config(k, KG / "node.yaml", raw=data, case_ids=ids)
    graph_node = GraphNode(N.load_rules(KG / cfg["rules"], k.causes), cfg["options"], k, layer=file_node.layer.manifest["version"], case_ids=ids)
    res = {"file": [], "graph": []}
    secs = {"file": [], "graph": []}
    same = {"steps": 0, "used": 0}
    used = {"file": {}, "graph": {}}
    for c in cases:
        alarm = {x: c[x] for x in ("record_id", "ts", "machine", "line", "group", "sensors")}   # 정답 칸은 노드에 넘기지 않음
        outs = {}
        for name, node in (("file", file_node), ("graph", graph_node)):
            t0 = time.perf_counter()
            o = node.run(k, alarm)
            secs[name].append(time.perf_counter() - t0)
            outs[name] = o
            res[name].append((c["category"], E.score(k, rules, c, o)))
            used[name][o["used"]] = used[name].get(o["used"], 0) + 1
        same["steps"] += outs["file"]["steps"] == outs["graph"]["steps"]
        same["used"] += outs["file"]["used"] == outs["graph"]["used"]
    graph_node.close()
    size = {"file": text_size([inspect.getsource(f) for f in (N.Node.run, N.Node._in_scope, N.Node._weight, N.holds, N.visible, N._cmp, N._value)]),
            "graph": text_size(list(TEMPLATES) + [inspect.getsource(_where), inspect.getsource(GraphNode._alarm_hit), inspect.getsource(GraphNode._evidence),
                                                  inspect.getsource(GraphNode.run)])}
    size["graph_cypher_only"] = text_size(list(TEMPLATES))
    summary = {"n": len(cases), "same_steps": same["steps"], "same_rule": same["used"], "used": used, "text": size,
               "graph_queries": graph_node.queries,
               "time_ms": {n: {"total": round(sum(v) * 1000, 1), "per_case": round(sum(v) * 1000 / len(v), 2), "max": round(max(v) * 1000, 1)} for n, v in secs.items()},
               "score": {n: _agg(r) for n, r in res.items()}}
    out.mkdir(parents=True, exist_ok=True)
    (out / "graph_compare.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    md = [M.V0, "", f"같은 100건 · 불러온 단계가 같은 사례 {same['steps']} / {len(cases)} · 쓴 규칙 줄이 같은 사례 {same['used']} / {len(cases)}", ""]
    for f, title in (("first", "첫 단계 맞힘"), ("recall", "불러와야 할 것 중 불러온 비율"), ("precision", "불러온 것 중 맞는 비율")):
        md += [f"### {title}", "", "| 방식 | " + " | ".join(f"{E.CAT_NAME[c]}({summary['score']['file'][c]['n']})" for c in CATS) + " |", "|---|" + "---|" * len(CATS)]
        for n, label in (("file", "파일 관계 층(D-533)"), ("graph", "그래프 DB(D-534)")):
            md.append(f"| {label} | " + " | ".join(f"{summary['score'][n][c][f]:.2f}" for c in CATS) + " |")
        md.append("")
    md += ["### 질의 글 길이 · 걸린 시간", "", "| 방식 | 근거 찾기 글(줄 · 글자) | 100건 전체 | 한 건 평균 | 한 건 최대 |", "|---|---|---|---|---|"]
    for n, label in (("file", "파일 관계 층(파이썬)"), ("graph", f"그래프 DB(Cypher 틀 {size['graph_cypher_only']['lines']}줄 · {size['graph_cypher_only']['chars']}자 + 파이썬)")):
        t = summary["time_ms"][n]
        md.append(f"| {label} | {size[n]['lines']}줄 · {size[n]['chars']}자 | {t['total']:.0f} ms | {t['per_case']:.1f} ms | {t['max']:.0f} ms |")
    md += ["", f"그래프 질의 수 {graph_node.queries}(100건) · 쓴 규칙 줄 — 파일 {json.dumps(used['file'], ensure_ascii=False, sort_keys=True)} · 그래프 {json.dumps(used['graph'], ensure_ascii=False, sort_keys=True)}"]
    (out / "graph_compare.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(KG / "results"))
    main(Path(ap.parse_args().out))
