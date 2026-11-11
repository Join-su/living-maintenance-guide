"""D-543 ⑥ ② · ③: fixing a relation's condition when the advice went wrong — the wheel's hypothesis kind 'relation' on the node that
reads the graph, and the new graph version each fix makes.

The graph release a Pack uses is one file in its revision (D-545 · 133 (A): templates/graph/current.json — a folder packrt patch
writes; its shape is c1a15-pack's, handoff/d545/graph-shape.md), so a fix is one edit line (write_file of that file) and goes the
existing way: the wheel's new revision → the same tests → 판정 adopts, the version before rolls back. Nothing is stored beside it, and
the file keeps the shape's own fields only: the layer stays, the release_id is made again by the shape's rule.

A hypothesis value is {id, change, when}: change narrow · widen · off; when (narrow · widen) = the relation's new condition, made from
the cases' own values — never invented, never the agent's numbers:
  narrow — fix cases that used the relation (wasted): one more sensor bound halfway between them and the relation's evidence values
           (its context.sensors), on the first sensor that separates them (the condition's own sensors first) — the evidence still holds;
           D-546 덧 29: or, when the relation has no equipment condition and every wasted case is of another equipment than its evidence
           record (context.equipment_id), that equipment as the condition's equipment_id — both tried the same way, beside off
  widen  — fix cases whose expected step is the relation's but the condition did not hold (same symptom group): the bounds moved to
           take them in
  off    — the relation line taken out (its section stays; the version before has it)."""
import copy
import hashlib
import json
import re
from pathlib import Path

from . import exgraph

GRAPH_FILE = "templates/graph/current.json"   # 133 (A) — the place agreed with c1a15-pack decides this one line
CHANGES = ("narrow", "widen", "off")
OPS = {"<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, ">": lambda a, b: a > b}   # graph-shape 3절
WHY = {"narrow": "관계 조건 좁힘 — 헛걸음 case({cases})가 쓴 관계, 근거는 그대로 두고 그 case는 빠지게(사례 값에서)",
       "equipment": "관계 조건 좁힘 — 헛걸음 case({cases})는 다른 설비, 근거 기록 설비({eq})로 한정(사례 값에서)",
       "off": "관계 끔 — 헛걸음 case({cases})가 쓴 관계",
       "widen": "관계 조건 넓힘 — 이 관계의 점검이 맞는 case({cases})에서 조건이 안 맞음(사례 값에서)"}


def read(folder):
    p = Path(folder) / GRAPH_FILE if folder else None
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p and p.is_file() else None
    except (OSError, ValueError):
        return None


def relations(release):
    return [r for r in (release or {}).get("exception_relations") or [] if isinstance(r, dict) and r.get("id")]


def _module_text(folder, dotted):
    p = Path(folder).joinpath(*dotted.split(".")).with_suffix(".py")
    try:
        return p.read_text(encoding="utf-8") if p.is_file() else None
    except (OSError, UnicodeDecodeError):
        return None


def _reads_graph(node, folder):
    """A function node whose code module names the graph file, or imports a module of the same package that does (a Pack whose judging
    module reads the graph through another of its modules) — one step of imports, read as text only."""
    fn = str((node.get("params") or {}).get("fn") or "")
    if str(node.get("kind") or "").split("@")[0] != "custom_code" or ":" not in fn:
        return False
    mod = fn.split(":")[0]
    text = _module_text(folder, mod)
    if text is None:
        return False
    if GRAPH_FILE in text:
        return True
    pkg = mod.rsplit(".", 1)[0] if "." in mod else ""
    names = set()
    for m in re.finditer(r"^\s*from\s+([\w.]+)\s+import\s+([\w ,]+)", text, re.M):
        names |= {f"{m.group(1)}.{x.strip()}" for x in m.group(2).split(",") if x.strip()} | {m.group(1)}
    for m in re.finditer(r"^\s*import\s+([\w.]+)", text, re.M):
        names.add(m.group(1))
    return any(GRAPH_FILE in (_module_text(folder, n) or "") for n in sorted(names) if n.split(".")[0] == (pkg.split(".")[0] or n.split(".")[0]) and n != mod)


def attach(nodes, folder):
    """The node map with `_graph` {folder, file, release_id, layer, relations} on the nodes that read the graph file; the same map
    when the copy has no graph file or no such node."""
    rel = read(folder)
    if rel is None:
        return nodes
    readers = [n for n, x in nodes.items() if _reads_graph(x, folder)]
    if not readers:
        return nodes
    g = {"folder": str(folder), "file": GRAPH_FILE, "release_id": rel.get("release_id"), "layer": rel.get("layer"),
         "groups": {g["code"]: g["name"] for g in rel.get("groups") or [] if isinstance(g, dict)},
         "relations": [{"id": r["id"], "mode": r.get("mode"), "when": r.get("when")} for r in relations(rel)]}
    return {**nodes, **{n: {**nodes[n], "_graph": g} for n in readers}}


def _query(value, depth=0):
    """The {group, sensors} the node was asked with in a visit input: graph_query's history_query {group, sensors}, or a C1-a15 Pack v8
    request {group, query: {sensor_snapshot}} (the two derived fields made as the Pack makes them)."""
    if depth > 3 or not isinstance(value, dict):
        return None
    if isinstance(value.get("sensors"), dict) and value.get("group"):
        return value
    snap = (value.get("query") or {}).get("sensor_snapshot") if isinstance(value.get("query"), dict) else None
    if isinstance(snap, dict) and value.get("group"):
        return {"group": value["group"], "sensors": exgraph.sensors({"sensor_values": {"snapshot": snap}}),
                **({"equipment_id": value["equipment_id"]} if isinstance(value.get("equipment_id"), str) else {})}   # D-546 덧 29: the alarm's equipment
    return next((q for v in value.values() if (q := _query(v, depth + 1))), None)


def with_cases(nodes, case_ids, run, fix, expected):
    """`_relation_cases` {relation id: {used: [...], missed: [...]}} on each graph node, from the base Run's fix cases: used = the case's
    visit of the node used the relation (node output graph marks); missed = it did not, the case's expected values name the
    relation's step, and the case is of its symptom group. Each item {case, group, sensors}."""
    from .node_detail import relations_of
    out = dict(nodes)
    for name, node in nodes.items():
        g = node.get("_graph")
        if not g:
            continue
        rc = {r["id"]: {"used": [], "missed": []} for r in g["relations"]}
        steps = {r["id"]: r for r in relations(read(g["folder"]))}
        for cid, c in zip(case_ids, (run or {}).get("cases") or []):
            if cid not in fix:
                continue
            for v in [v for v in c.get("visits") or [] if v.get("node") == name]:
                q, marks = _query(v.get("input")), relations_of(v.get("output"))
                if not q:
                    continue
                item = {"case": cid, "group": g["groups"].get(q["group"], q["group"]), "sensors": q["sensors"],   # the shape names a group by its name
                        **({"equipment": q["equipment_id"]} if isinstance(q.get("equipment_id"), str) else {})}
                used = set((marks or {}).get("used") or [])
                want = json.dumps((expected or {}).get(cid) or {}, ensure_ascii=False)
                for rid, r in steps.items():
                    if rid in used:
                        rc[rid]["used"].append(item)
                    elif f'"{r.get("step")}"' in want and item["group"] == (r.get("when") or {}).get("group"):
                        rc[rid]["missed"].append(item)
        named = any(relations_of(v.get("output")) for c in (run or {}).get("cases") or [] for v in c.get("visits") or [] if v.get("node") == name)
        if not named and not any(x["missed"] for x in rc.values()):   # a node that reads the graph but whose run never named a relation: no option
            out[name] = {k: v for k, v in node.items() if k != "_graph"}
            continue
        out[name] = {**node, "_relation_cases": rc}
    return out


def knob(node):
    """The 'relation' option as the agent and the fill see it: each relation with the fix cases that used it and that missed it."""
    rc = node.get("_relation_cases") or {}
    return {"type": "relation", "relations": [{**r, "used_in": [x["case"] for x in (rc.get(r["id"]) or {}).get("used") or []],
                                               "missed_in": [x["case"] for x in (rc.get(r["id"]) or {}).get("missed") or []]}
                                              for r in node["_graph"]["relations"]]}


def where_text(when):
    """A condition as the hypothesis table reads it: '온도 차 저하 · temp_diff_k <= 8.2' · a reference notice '온도 차 저하 · 설비 M-01'."""
    w = when or {}
    parts = [str(w.get("group"))] + [f"{f} {op} {v}" for f, op, v in w.get("sensors") or []] + ([f"설비 {w['equipment_id']}"] if w.get("equipment_id") else [])
    if w.get("recent"):   # D-570: the maintenance condition
        parts.append(f"최근 {w['recent'].get('days')}일 안 {w['recent'].get('section')} 정비")
    return " · ".join(parts)


def value_text(v):
    """A relation hypothesis value as one line ('EX-R-0001 좁힘 · temp_diff_k <= 7.9 · 설비 M-01'), as ④'s table reads it; None for
    anything else (D-546 덧 29: the deploy approval window's '1 가설')."""
    if not isinstance(v, dict) or v.get("change") not in CHANGES or not v.get("id"):
        return None
    w = v.get("when") or {}
    parts = [f"{f} {op} {x}" for f, op, x in w.get("sensors") or []] + ([f"설비 {w['equipment_id']}"] if w.get("equipment_id") else [])
    return " · ".join([f"{v['id']} {dict(zip(CHANGES, ('좁힘', '넓힘', '끔')))[v['change']]}"] + parts)


def holds(when, group, sensors):
    if (when or {}).get("recent"):   # D-570: it takes the equipment's history; the sensors alone never tell
        return False
    return group == (when or {}).get("group") and all(OPS[op](sensors[f], v) for f, op, v in (when or {}).get("sensors") or [])


def _mid(a, b):
    m = (a + b) / 2
    return int(round(m)) if isinstance(a, int) and isinstance(b, int) else round(m, 2)


def _merge(conds):
    """The tightest bound per (sensor, side), in a fixed order."""
    best = {}
    for f, op, v in conds:
        k = (f, op)
        best[k] = v if k not in best else (min(best[k], v) if op == "<=" else max(best[k], v))
    return sorted([f, op, v] for (f, op), v in best.items())


def narrow(rel, wasted):
    """The new condition that keeps the relation's evidence (sensors_at) and leaves the wasted cases out, or None."""
    ev, when = (rel.get("context") or {}).get("sensors") or {}, rel.get("when") or {}
    conds = [list(c) for c in when.get("sensors") or []]
    if not conds or not wasted:
        return None
    order = list(dict.fromkeys([c[0] for c in conds] + list(exgraph.FIELDS)))
    for f in order:
        e, ws = ev.get(f), [w["sensors"].get(f) for w in wasted]
        if e is None or any(x is None for x in ws):
            continue
        if all(x < e for x in ws):
            new = [f, ">=", _mid(max(ws), e)]
        elif all(x > e for x in ws):
            new = [f, "<=", _mid(min(ws), e)]
        else:
            continue
        merged = _merge(conds + [new])
        if merged != _merge(conds):
            return {**when, "sensors": merged}
    return None


def narrow_equipment(rel, wasted):
    """D-546 덧 29: the condition with the evidence record's equipment added — when the relation has no equipment condition and every
    wasted case is of another equipment (the evidence still holds, those cases no longer do) — or None."""
    ev, when = (rel.get("context") or {}).get("equipment_id"), rel.get("when") or {}
    eqs = [w.get("equipment") for w in wasted or []]
    if not isinstance(ev, str) or not ev or when.get("equipment_id") or not eqs or any(not isinstance(e, str) or e == ev for e in eqs):
        return None
    return {**when, "equipment_id": ev}


def widen(rel, missed):
    """The condition with its bounds moved to take the missed cases in, or None."""
    when = rel.get("when") or {}
    conds = [list(c) for c in when.get("sensors") or []]
    if not conds or not missed:
        return None
    out = []
    for f, op, b in conds:
        ms = [m["sensors"].get(f) for m in missed]
        if any(x is None for x in ms):
            return None
        out.append([f, op, max([b] + ms) if op == "<=" else min([b] + ms)])
    new = {**when, "sensors": _merge(out)}
    return new if new != {**when, "sensors": _merge(conds)} and all(holds(new, m["group"], m["sensors"]) for m in missed) else None


def _full(node, rid):
    return next((r for r in relations(read((node.get("_graph") or {}).get("folder"))) if r["id"] == rid), None)


def complete(node, value):
    """The condition of a narrow · widen value from the node's cases (the agent names the relation and the change only); a narrow no
    sensor can make is the equipment one (D-546 덧 29)."""
    rel = _full(node, (value or {}).get("id"))
    rc = (node.get("_relation_cases") or {}).get((value or {}).get("id")) or {}
    if not rel:
        return None
    if value.get("change") == "narrow":
        return narrow(rel, rc.get("used")) or narrow_equipment(rel, rc.get("used"))
    return widen(rel, rc.get("missed")) if value.get("change") == "widen" else None


def rule_steps(node, fix_ids, tried=()):
    """The rule hypotheses of the relation option: per relation, narrow then off for the wasted cases, widen for the missed ones."""
    out = []
    for r in node["_graph"]["relations"]:
        rc = (node.get("_relation_cases") or {}).get(r["id"]) or {}
        used, missed = [x["case"] for x in rc.get("used") or []], [x["case"] for x in rc.get("missed") or []]
        vals = []
        if used:
            full = _full(node, r["id"]) or {}
            w, we = narrow(full, rc.get("used")), narrow_equipment(full, rc.get("used"))
            vals += [({"id": r["id"], "change": "narrow", "when": x}, used) for x in (w, we) if x] + [({"id": r["id"], "change": "off"}, used)]
        if missed:
            w = complete(node, {"id": r["id"], "change": "widen"})
            vals += [({"id": r["id"], "change": "widen", "when": w}, missed)] if w else []
        for v, cases in vals:
            eq = (v.get("when") or {}).get("equipment_id") if v["change"] == "narrow" and not (r.get("when") or {}).get("equipment_id") else None
            if (node["name"], "relation", json.dumps(v)) not in tried:
                out.append({"node": node["name"], "knob": "relation", "value": v, "predicts": cases,
                            "why": WHY["equipment" if eq else v["change"]].format(cases=" · ".join(cases), eq=eq), "source": "rule"})
    return out


def check(v, node):
    """None when the value may be tried, else why not ('버림 — …')."""
    if not isinstance(v, dict):
        return "버림 — 관계 값 꼴이 아님"
    r = next((x for x in node["_graph"]["relations"] if x["id"] == v.get("id")), None)
    if not r:
        return f"버림 — 그래프에 없는 관계({v.get('id')})"
    if v.get("change") not in CHANGES:
        return f"버림 — 관계 고치기는 좁힘 · 넓힘 · 끔만({v.get('change')})"
    if v["change"] == "off":
        return None
    when = v.get("when")
    if not isinstance(when, dict):
        return "버림 — 사례 값으로 조건을 만들 수 없음"
    old = (r.get("when") or {}).get("group")
    if when.get("group") != old:
        return f"버림 — 증상군은 바꿀 수 없음({old} → {when.get('group')})"
    eq = when.get("equipment_id")
    if eq != (r.get("when") or {}).get("equipment_id") and eq != ((_full(node, r["id"]) or {}).get("context") or {}).get("equipment_id"):   # D-546 덧 29
        return f"버림 — 설비 조건은 근거 기록 설비만({eq})"
    for c in when.get("sensors") or []:
        if not (isinstance(c, list) and len(c) == 3):
            return "버림 — 조건 꼴이 아님"
        if c[0] not in exgraph.FIELDS:
            return f"버림 — 조건 칸이 허용 목록 밖({c[0]})"
        if c[1] not in OPS or not isinstance(c[2], (int, float)) or isinstance(c[2], bool):
            return "버림 — 조건 꼴이 아님"
    if {**when, "sensors": _merge(when.get("sensors") or [])} == {**(r.get("when") or {}), "sensors": _merge((r.get("when") or {}).get("sensors") or [])}:
        return "버림 — 지금 조건과 같음"
    return None


def release_id(rel):
    """graph-shape 1절: graph-<layer>-<sha256 8 of the content without release_id>."""
    body = json.dumps({k: v for k, v in rel.items() if k != "release_id"}, ensure_ascii=False, sort_keys=True)
    return f"graph-{rel['layer']}-{hashlib.sha256(body.encode('utf-8')).hexdigest()[:8]}"


def apply(release, value):
    """The new graph version: the relation's condition changed (where too) or, for off, its line taken out — the layer as it is, the
    release_id made again; None when the relation is not there. What it was is the version before."""
    new = copy.deepcopy(release)
    rows = new.get("exception_relations") or []
    r = next((x for x in rows if x.get("id") == value.get("id")), None)
    if r is None:
        return None
    if value["change"] == "off":
        new["exception_relations"] = [x for x in rows if x is not r]
    else:
        r["when"] = {**value["when"], "sensors": _merge(value["when"].get("sensors") or [])}
        r["where"] = ";".join(f"{f}{op}{v}" for f, op, v in r["when"]["sensors"])
    new["release_id"] = release_id(new)
    return new


def ops(node, value):
    """The edit line of a relation hypothesis: write_file of the graph file with the new version; None when it cannot be made."""
    g = node.get("_graph") or {}
    rel = read(g.get("folder"))
    new = apply(rel, value) if rel and isinstance(value, dict) and value.get("change") in CHANGES else None
    if new is None:
        return None
    return [{"op": "write_file", "path": GRAPH_FILE, "text": json.dumps(new, ensure_ascii=False, indent=1, sort_keys=True) + "\n"}]


def version_diff(folder_a, folder_b):
    """D-543 ⑥ ③: what the revision diff says of two copies' graph versions — {before, after (release ids), relations: [{id, before,
    after}]} (each a readable condition; a relation only one has: '없음'); None when either has no graph file or both the same version."""
    a, b = read(folder_a), read(folder_b)
    if a is None or b is None or a.get("release_id") == b.get("release_id"):
        return None
    ra, rb = ({r["id"]: r for r in relations(x)} for x in (a, b))
    say = lambda r: "없음" if r is None else where_text(r.get("when"))   # noqa: E731
    rows = [{"id": i, "before": say(ra.get(i)), "after": say(rb.get(i))} for i in sorted(set(ra) | set(rb)) if say(ra.get(i)) != say(rb.get(i))]
    return {"before": a.get("release_id"), "after": b.get("release_id"), "relations": rows}
