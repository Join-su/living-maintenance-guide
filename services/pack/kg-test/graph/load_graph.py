"""D-534 적재기 — 원본 층(kg-test/data/) + 관계 정의(relation_defs.yaml) + 매뉴얼 지식(rules.yaml 점검 · 조치 · 권한 · relations/ 표) → 그래프 DB 관계 층.
관계는 파일 관계 층(build_layer.py)과 같은 함수 · 같은 정의로 냄. 버전 꼬리표(layer 속성 = 정의 version)가 붙은 것만 지우고 다시 넣음 → 다시 넣으면 같은 결과(dump sha256).
규칙 표(exceptions.csv)는 넣지 않음 — 찾을 때 graph_node.py가 질의 틀에 넣어 씀.

kg-test/.venv-kg/bin/python kg-test/graph/load_graph.py [--raw data] [--defs relation_defs.yaml] [--layer <꼬리표 덮어쓰기>] [--dump]

그래프 꼴(틀 → 그래프):
  (:Line) ←[:ON_LINE]− (:Machine) ←[:AT]− (:Event 알람 · 센서 칸 · 계절)      name 칸 = 그래프 화면 글씨(알람 id · 권한)
  (:Event) −[:CONFIRMED_CAUSE]→ (:Cause 매뉴얼 안/밖)            확정 원인(승인된 보고서)
  (:Event) −[:PRIOR_WORK {kind, age_days}]→ (:Event)                 같은 설비 앞 정비(부품 교체 · 미룸)
  (:Event) −[:SAME_LINE_BEFORE {age_min}]→ (:Event)                  같은 라인 · 다른 설비 앞 알람(창 안)
  (:Event) −[:CHECKED {result}]→ (:Step) · −[:DID {note}]→ (:Step)    그 알람에서 한 점검 · 조치(보고서 칸)
  (:Step 점검) −[:CHECKS]→ (:Cause)                                   점검이 확인하는 원인
  (:Cause) −[:REMEDIED_BY]→ (:Step 조치 · 호출)                       원인 → 조치
  (:Step) −[:REQUIRES]→ (:Grade A · B · C)                             필요 권한
  (:Layer {version, defs, raw_sha256})                                 버전 정보"""
import argparse
import calendar
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

import yaml

from conn import KG, driver

import build_layer as B   # noqa: E402  (conn이 kg-test를 경로에 넣음)
import methods as M       # noqa: E402

FIELDS = M.FIELDS
BATCH = 500


def epoch(ts):
    return calendar.timegm(datetime.strptime(ts, M.FMT).timetuple())


def graph_rows(raw, defs_path, rules_path=KG / "rules.yaml", rel=KG / "relations"):
    """그래프에 넣을 노드 · 관계 목록(파이썬 값) — DB와 상관없이 같은 입력이면 같은 목록."""
    defs_bytes = Path(defs_path).read_bytes()
    defs = yaml.safe_load(defs_bytes)
    rules = yaml.safe_load(Path(rules_path).read_text(encoding="utf-8"))
    hist = {r["record_id"]: r for r in B._rows(Path(raw) / "history.jsonl")}
    nodes = B.nodes_from_raw(raw, defs)
    idx = B.Index(nodes)
    edges = [e for n in nodes for e in B.edges_for(n, idx, defs)]
    events = [{"id": n["id"], "name": n["id"], "ts": n["ts"], "epoch": epoch(n["ts"]), "machine": n["machine"], "line": n["line"], "season": n["season"],
               "work": n["work"], **{f: n["sensors"][f] for f in FIELDS}} for n in nodes]
    lines = sorted({n["line"] for n in nodes})
    machines = sorted({(n["machine"], n["line"]) for n in nodes})
    causes = [{"code": c, "name": v["name"], "in_manual": v["in_manual"]} for c, v in sorted(rules["causes"].items())]
    steps = [{"section": s, "kind": v["kind"], "name": v["name"], "minutes": v["minutes"], "grade": v["grade"]} for s, v in sorted(rules["steps"].items())]
    grades = [{"code": g, "mode": m, "name": f"{g} {m}"} for g, m in sorted(M.MODE.items())]
    with (rel / "check_cause.csv").open(encoding="utf-8") as f:
        checks = [{"step": r["check"], "cause": r["cause"]} for r in csv.DictReader(f)]
    with (rel / "cause_step.csv").open(encoding="utf-8") as f:
        remedies = [{"cause": r["cause"], "step": r["step"]} for r in csv.DictReader(f)]
    confirmed = [{"id": n["id"], "cause": n["cause"]} for n in nodes if n["cause"]]
    did = [{"id": i, "step": a["section"], "note": a.get("note")} for i, r in sorted(hist.items()) for a in r["actions"]]
    checked = [{"id": i, "step": c["section"], "result": c["result"]} for i, r in sorted(hist.items()) for c in r["check_results"]]
    meta = {"version": defs["version"], "defs": json.dumps(defs, ensure_ascii=False, sort_keys=True),
            "raw_sha256": json.dumps({f: B._sha((Path(raw) / f).read_bytes()) for f in B.RAW}, sort_keys=True)}
    return {"meta": meta, "lines": lines, "machines": machines, "events": events, "causes": causes, "steps": steps, "grades": grades,
            "checks": checks, "remedies": remedies, "confirmed": confirmed, "did": did, "checked": checked,
            "same_line": [e for e in edges if e["rel"] == "same_line"], "machine_work": [e for e in edges if e["rel"] == "machine_work"]}


def _batches(rows):
    for i in range(0, len(rows), BATCH):
        yield rows[i:i + BATCH]


def clear(session, layer):
    session.run("MATCH (n {layer: $v}) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 1000 ROWS", v=layer).consume()


def load(raw, defs_path, layer=None, drv=None):
    g = graph_rows(raw, defs_path)
    v = layer or g["meta"]["version"]
    own = drv is None
    drv = drv or driver()
    q = [   # (질의, 목록)
        ("UNWIND $rows AS r CREATE (:Line {layer: $v, id: r})", g["lines"]),
        ("UNWIND $rows AS r MATCH (l:Line {layer: $v, id: r[1]}) CREATE (:Machine {layer: $v, id: r[0]})-[:ON_LINE]->(l)", g["machines"]),
        ("UNWIND $rows AS r MATCH (m:Machine {layer: $v, id: r.machine}) CREATE (e:Event {layer: $v}) SET e += r CREATE (e)-[:AT]->(m)", g["events"]),
        ("UNWIND $rows AS r CREATE (c:Cause {layer: $v}) SET c += r", g["causes"]),
        ("UNWIND $rows AS r CREATE (s:Step {layer: $v}) SET s += r", g["steps"]),
        ("UNWIND $rows AS r CREATE (g:Grade {layer: $v}) SET g += r", g["grades"]),
        ("UNWIND $rows AS r MATCH (s:Step {layer: $v, section: r.section}), (g:Grade {layer: $v, code: r.grade}) CREATE (s)-[:REQUIRES]->(g)", g["steps"]),
        ("UNWIND $rows AS r MATCH (s:Step {layer: $v, section: r.step}), (c:Cause {layer: $v, code: r.cause}) CREATE (s)-[:CHECKS]->(c)", g["checks"]),
        ("UNWIND $rows AS r MATCH (c:Cause {layer: $v, code: r.cause}), (s:Step {layer: $v, section: r.step}) CREATE (c)-[:REMEDIED_BY]->(s)", g["remedies"]),
        ("UNWIND $rows AS r MATCH (e:Event {layer: $v, id: r.id}), (c:Cause {layer: $v, code: r.cause}) CREATE (e)-[:CONFIRMED_CAUSE]->(c)", g["confirmed"]),
        ("UNWIND $rows AS r MATCH (e:Event {layer: $v, id: r.id}), (s:Step {layer: $v, section: r.step}) CREATE (e)-[:DID {note: r.note}]->(s)", g["did"]),
        ("UNWIND $rows AS r MATCH (e:Event {layer: $v, id: r.id}), (s:Step {layer: $v, section: r.step}) CREATE (e)-[:CHECKED {result: r.result}]->(s)", g["checked"]),
        ("UNWIND $rows AS r MATCH (a:Event {layer: $v, id: r.from}), (b:Event {layer: $v, id: r.to}) CREATE (a)-[:SAME_LINE_BEFORE {age_min: r.age_min}]->(b)", g["same_line"]),
        ("UNWIND $rows AS r MATCH (a:Event {layer: $v, id: r.from}), (b:Event {layer: $v, id: r.to}) "
         "CREATE (a)-[:PRIOR_WORK {kind: r.kind, age_days: r.age_days}]->(b)", g["machine_work"]),
    ]
    try:
        with drv.session() as s:
            for label, key in (("Event", "id"), ("Line", "id"), ("Machine", "id"), ("Cause", "code"), ("Step", "section")):
                s.run(f"CREATE INDEX kg_{label.lower()} IF NOT EXISTS FOR (n:{label}) ON (n.layer, n.{key})").consume()
            s.run("CREATE INDEX kg_event_epoch IF NOT EXISTS FOR (n:Event) ON (n.layer, n.epoch)").consume()
            clear(s, v)
            s.run("CREATE (m:Layer {layer: $v}) SET m += $meta", v=v, meta=g["meta"]).consume()
            for cypher, rows in q:
                for b in _batches(rows):
                    s.run(cypher, rows=b, v=v).consume()
    finally:
        if own:
            drv.close()
    return v, {k: len(x) for k, x in g.items() if isinstance(x, list)}


def dump(layer, drv):
    """그 꼬리표의 노드 · 관계를 정렬한 글 → sha256(다시 넣으면 같은지 보는 값 · 내부 id 안 씀)."""
    with drv.session() as s:
        nodes = s.run("MATCH (n {layer: $v}) RETURN labels(n) AS l, properties(n) AS p", v=layer).data()
        rels = s.run("MATCH (a {layer: $v})-[r]->(b {layer: $v}) RETURN type(r) AS t, properties(r) AS p, "
                     "labels(a)[0] AS la, coalesce(a.id, a.code, a.section) AS ka, labels(b)[0] AS lb, coalesce(b.id, b.code, b.section) AS kb", v=layer).data()
    lines = sorted(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in nodes + rels)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(), {"nodes": len(nodes), "rels": len(rels)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(KG / "data"))
    ap.add_argument("--defs", default=str(KG / "relation_defs.yaml"))
    ap.add_argument("--layer")
    ap.add_argument("--dump", action="store_true")
    a = ap.parse_args()
    v, counts = load(a.raw, a.defs, a.layer)
    out = {"layer": v, **counts}
    if a.dump:
        with driver() as d:
            out["dump_sha256"], out["dump_counts"] = dump(v, d)
    print(json.dumps(out, ensure_ascii=False))
