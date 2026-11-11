"""Revision diff of two Agent Pack releases (EV-002 CHG-13): the normalized pack.yaml of each release copy, compared by
node, edge and params, plus the list of changed files (raw byte hashes). Read only: nothing is written."""
import json
from pathlib import Path

from packrt.ir import strict_yaml
from packrt.ir.revision import apply_defaults

from .guard import redact


class DiffError(Exception):
    pass


def _show(v):
    return json.dumps(v, ensure_ascii=False, separators=(", ", ": ")) if not isinstance(v, str) else v


def _walk(path, a, b, out):
    if a == b:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            p = f"{path}.{k}" if path else str(k)
            if k not in a:
                out.append({"path": p, "change": "추가", "after": _show(b[k])})
            elif k not in b:
                out.append({"path": p, "change": "삭제", "before": _show(a[k])})
            else:
                _walk(p, a[k], b[k], out)
        return
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and any(isinstance(x, (dict, list)) for x in a + b):
        for i, (x, y) in enumerate(zip(a, b)):
            _walk(f"{path}[{i}]", x, y, out)
        return
    out.append({"path": path, "change": "변경", "before": _show(a), "after": _show(b)})


def _edge_key(e):
    return json.dumps(e, ensure_ascii=False, sort_keys=True)


def _graph(name, a, b, out):
    a, b = a or {}, b or {}
    rest_a = {k: v for k, v in a.items() if k not in ("nodes", "edges")}
    rest_b = {k: v for k, v in b.items() if k not in ("nodes", "edges")}
    _walk(name, rest_a, rest_b, out)
    na = {n["id"]: n for n in a.get("nodes", []) if isinstance(n, dict)}
    nb = {n["id"]: n for n in b.get("nodes", []) if isinstance(n, dict)}
    order = [n for n in na] + [n for n in nb if n not in na]
    for nid in order:
        if nid not in nb:
            out.append({"path": f"{name}.{nid}", "change": "node 삭제", "before": na[nid].get("kind", "")})
        elif nid not in na:
            out.append({"path": f"{name}.{nid}", "change": "node 추가", "after": nb[nid].get("kind", "")})
        else:
            _walk(f"{name}.{nid}", {k: v for k, v in na[nid].items() if k != "id"}, {k: v for k, v in nb[nid].items() if k != "id"}, out)
    ea = {_edge_key(e): e for e in a.get("edges", [])}
    eb = {_edge_key(e): e for e in b.get("edges", [])}
    for k, e in ea.items():
        if k not in eb:
            out.append({"path": f"{name}.edges", "change": "edge 삭제", "before": _show(e)})
    for k, e in eb.items():
        if k not in ea:
            out.append({"path": f"{name}.edges", "change": "edge 추가", "after": _show(e)})


def normalized(copy_dir):
    text = (Path(copy_dir) / "pack.yaml").read_text(encoding="utf-8")
    doc, _ = strict_yaml.load(text)
    return apply_defaults(doc)


def diff_docs(a, b):
    """Changes from a to b. Graph nodes are matched by id and edges as a set; everything else by key path."""
    out = []
    for k in sorted(set(a) | set(b)):
        if k == "graphs":
            ga, gb = a.get("graphs") or {}, b.get("graphs") or {}
            for g in sorted(set(ga) | set(gb)):
                if g not in gb:
                    out.append({"path": f"graphs.{g}", "change": "graph 삭제"})
                elif g not in ga:
                    out.append({"path": f"graphs.{g}", "change": "graph 추가"})
                else:
                    _graph(g, ga[g], gb[g], out)
        elif k not in a:
            out.append({"path": k, "change": "추가", "after": _show(b[k])})
        elif k not in b:
            out.append({"path": k, "change": "삭제", "before": _show(a[k])})
        else:
            _walk(k, a[k], b[k], out)
    return out


def diff_files(raw_a, raw_b):
    out = []
    for p in sorted(set(raw_a) | set(raw_b)):
        if p not in raw_b:
            out.append({"path": p, "change": "삭제"})
        elif p not in raw_a:
            out.append({"path": p, "change": "추가"})
        elif raw_a[p] != raw_b[p]:
            out.append({"path": p, "change": "변경"})
    return out


def revision_diff(rel_a, rel_b):
    """rel_a/rel_b: release records. Refuses anything but two Agent Pack releases with the same Pack id."""
    pa, pb = rel_a.get("pack"), rel_b.get("pack")
    if not pa or not pb:
        raise DiffError("revision 차이는 Agent Pack release끼리만 볼 수 있습니다")
    if pa["id"] != pb["id"]:
        raise DiffError(f"같은 Pack id의 release끼리만 비교합니다 — {pa['id']} ↔ {pb['id']}")
    head = {"a": {"rel": rel_a["id"], "name": rel_a["name"], "revision": pa["revision"]},
            "b": {"rel": rel_b["id"], "name": rel_b["name"], "revision": pb["revision"]}}
    if pa["revision"] == pb["revision"]:
        return {**head, "same": True, "changes": [], "files": []}
    try:
        changes = diff_docs(normalized(pa["copy"]), normalized(pb["copy"]))
    except (OSError, strict_yaml.StrictYAMLError, UnicodeDecodeError) as e:
        raise DiffError(f"release 사본의 pack.yaml을 읽을 수 없습니다 — {e}")
    changes = [{k: redact(v) if k in ("before", "after") else v for k, v in c.items()} for c in changes]   # values shown on screen: key-like text masked
    from .graphrel import version_diff   # D-543 ⑥ ③: the graph version each revision carries
    return {**head, "same": False, "changes": changes, "files": diff_files(pa["raw_files"], pb["raw_files"]), "graph": version_diff(pa["copy"], pb["copy"])}
