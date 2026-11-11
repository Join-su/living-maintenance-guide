"""D-533 적재 두 층 — 원본 층(data/history.jsonl 승인된 보고서 칸 · data/occurrences.jsonl 알람, 그대로 · 고치지 않음)에서 관계 층을 다시 만든다.
관계 정의 = relation_defs.yaml(버전). 예외 규칙(exceptions.csv)은 여기 굽지 않음 — 찾을 때 kg_node.py가 적용.
같은 원본 + 같은 정의 → 같은 바이트(시험).

python kg-test/build_layer.py [--raw <원본 폴더>] [--defs <정의>] [--out <층 폴더>]   # 기본 data · relation_defs.yaml · layer
→ <out>/<version>/nodes.jsonl · edges.jsonl · defs.yaml(쓴 정의 사본) · manifest.json(원본 · 정의 sha256 · 개수)"""
import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
FMT = "%Y-%m-%dT%H:%M"
RAW = ("history.jsonl", "occurrences.jsonl")


def _rows(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def work_kind(rec, defs):
    """정비 기록의 종류 — deferred: 미룸(메모 있는 조치) · replace: 부품 교체 · 그 밖 None."""
    acts = rec["actions"]
    if any(a.get("note") for a in acts):
        return "deferred"
    if any(a["section"] in defs["machine_work"]["replace_sections"] for a in acts):
        return "replace"
    return None


def nodes_from_raw(raw, defs):
    """알람 하나 = 노드 하나. 기록(승인된 보고서)이 있으면 확정 원인 · 정비 종류를 붙임(없으면 null — 시험 사례 · 아직 안 끝난 알람)."""
    recs = {r["record_id"]: r for r in _rows(Path(raw) / "history.jsonl")}
    season = {m: s for s, ms in defs["season"].items() for m in ms}
    out = []
    for o in _rows(Path(raw) / "occurrences.jsonl"):
        r = recs.get(o["record_id"])
        out.append({"id": o["record_id"], "ts": o["ts"], "machine": o["machine"], "line": o["line"], "group": o["group"],
                     "season": season[int(o["ts"][5:7])], "sensors": o["sensors"],
                     "cause": r["cause"] if r else None, "work": work_kind(r, defs) if r else None})
    return sorted(out, key=lambda n: (n["ts"], n["id"]))


class Index:
    """노드를 라인 · 설비별로 — 새 알람의 관계도 같은 함수(edges_for)로 낸다."""

    def __init__(self, nodes):
        self.by_line, self.by_machine = {}, {}
        for n in nodes:
            n["_t"] = datetime.strptime(n["ts"], FMT)
            self.by_line.setdefault(n["line"], []).append(n)
            self.by_machine.setdefault(n["machine"], []).append(n)


def edges_for(n, idx, defs):
    """노드 n(시각 n.ts) → 앞 노드로 가는 관계. 앞만 보므로 미래 0."""
    t = datetime.strptime(n["ts"], FMT)
    w = timedelta(minutes=defs["same_line"]["window_min"])
    out = [{"from": n["id"], "rel": "same_line", "to": m["id"], "machine": m["machine"], "age_min": int((t - m["_t"]).total_seconds() // 60)}
           for m in idx.by_line.get(n["line"], []) if m["machine"] != n["machine"] and t - w <= m["_t"] < t]
    d = timedelta(days=defs["machine_work"]["max_days"])
    out += [{"from": n["id"], "rel": "machine_work", "to": m["id"], "kind": m["work"], "age_days": round((t - m["_t"]).total_seconds() / 86400, 4)}
            for m in idx.by_machine.get(n["machine"], []) if m["work"] and t - d <= m["_t"] < t]
    return out


def build(raw, defs_path, out_root):
    defs_bytes = Path(defs_path).read_bytes()
    defs = yaml.safe_load(defs_bytes)
    nodes = nodes_from_raw(raw, defs)
    idx = Index(nodes)
    edges = [e for n in nodes for e in edges_for(n, idx, defs)]
    out = Path(out_root) / defs["version"]
    out.mkdir(parents=True, exist_ok=True)
    line = lambda r: json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"   # noqa: E731
    (out / "nodes.jsonl").write_text("".join(line({k: v for k, v in n.items() if k != "_t"}) for n in nodes), encoding="utf-8")
    (out / "edges.jsonl").write_text("".join(line(e) for e in edges), encoding="utf-8")
    (out / "defs.yaml").write_bytes(defs_bytes)
    manifest = {"version": defs["version"], "defs_sha256": _sha(defs_bytes),
                "raw_sha256": {f: _sha((Path(raw) / f).read_bytes()) for f in RAW},
                "counts": {"nodes": len(nodes), "with_record": sum(1 for n in nodes if n["cause"]),
                           **{f"edges_{r}": sum(1 for e in edges if e["rel"] == r) for r in ("same_line", "machine_work")}}}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return out, manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(HERE / "data"))
    ap.add_argument("--defs", default=str(HERE / "relation_defs.yaml"))
    ap.add_argument("--out", default=str(HERE / "layer"))
    a = ap.parse_args()
    out, m = build(a.raw, a.defs, a.out)
    print(json.dumps({"out": out.name, **m["counts"]}, ensure_ascii=False))
