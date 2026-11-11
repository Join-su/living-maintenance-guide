"""EV-035 ML-2 (approved E3, answer 045-01 · CHG-181): ④ of the improvement wheel — the responsible node and the hypotheses.

Responsible node (D-100): for every 고칠 case of the baseline Run, the signal node is the first node whose check failed; from it the
reads are followed back to the nodes that wrote them (only nodes this case passed through), and each is judged against its
expect.text, earliest first — the first whose output is judged wrong is the responsible node; none → the signal node itself. In
fake mode the judge is not a signal (EV-030 Q-9): the responsible node is left undecided and the signal node is used.

Hypotheses (D-94 · D-95 · D-99 · D-119): one node, one knob, one value, with the cases it should change. Sources: the start
document (Q-2 A) when it is for this base revision · the hypothesis agent (real mode, LLMOPS_AUTO_HYPOTHESIS=agent) · rules in fake
mode (the responsible node's number and choice knobs, cheapest change first; never prompt text) · the test harness. Each one is
checked: a node of this Pack · a knob of its kind · a value in range · not loosening a check (D-95) · predicting at least one case of
the 고칠 · 지킬 sets (hidden cases are never used, D-93) · in real mode not a larger model (ask first). The agent sees the
responsible node, its knobs and the 고칠 · 지킬 cases — never hidden cases, judge reasons or people's label reasons (D-94)."""
import json
import re
from pathlib import Path

from . import graphrel

# knob catalog by node kind (031 1절 K1 ~ K10; ranges are the default 'node 종류별 기본', D-119). loosen = which direction loosens (D-95)
KNOBS = {
    "retrieve": {"top_k": {"type": "int", "min": 2, "max": 6}, "search": {"type": "choice", "values": ["similarity", "mmr"]},
                 "fallback.min_results": {"type": "int", "min": 1, "max": 3, "loosen": "down"}},
    "validate": {"min_score": {"type": "float", "min": 0.0, "max": 1.0, "loosen": "down"}, "min_items": {"type": "int", "min": 1, "max": 5, "loosen": "down"},
                 "require_source_types": {"type": "list", "loosen": "remove"}, "deny": {"type": "list", "loosen": "remove"}},
    "llm": {"temperature": {"type": "float", "min": 0.0, "max": 0.3}, "max_tokens": {"type": "int", "min": 1500, "max": 4000},
            "model": {"type": "model"}, "prompt": {"type": "prompt"}},
    # EV-043 CHG-209 (contract-locked C, D-265 · D-270): retrieve@2's retriever knobs — the locked C list less chunk · overlap
    # (llmops-2 adds index.* here); no `loosen`: D-95 (never loosen a check) stays with validate · retrieve@1 only
    "retrieve@2": {"method": {"type": "choice", "values": ["vector", "lexical", "hybrid"]}, "fuse.k": {"type": "int", "min": 10, "max": 120},
                   "top_k": {"type": "int", "min": 5, "max": 20}, "score_threshold": {"type": "float", "min": 0.0, "max": 0.8},
                   "rerank.weights.sensor": {"type": "float", "min": 0.0, "max": 1.0}, "filter": {"type": "filter_key"},
                   "fallback.min_results": {"type": "int", "min": 1, "max": 5},
                   # EV-051 CHG-W2-11 · 12 (D-250 · D-312): the vector DB recipe of the node — step = a rule hypothesis's one step,
                   # start = the values tried when the node has no chunk size yet (sections whole), default = packrt's value when unset,
                   # needs = only on a node that has that knob set
                   "index.chunk_size": {"type": "int", "min": 200, "max": 2000, "step": 100, "start": [800, 400]},
                   "index.overlap": {"type": "int", "min": 0, "max": 400, "step": 50, "default": 0, "needs": "index.chunk_size"},
                   # D-418 C (contract-locked C 덧, packrt ≥ 0.12.5): the sensor rerank's candidate count — an int ≥ the node's top_k,
                   # unset = top_k (default_from); only on a node with a rerank dict. Last in the list so the rule lists before it keep their order
                   "rerank.pool": {"type": "int", "min": 5, "max": 50, "step": 5, "needs": "rerank", "default_from": "top_k"}},
}
MODEL_ORDER = ["gpt-4.1-nano", "gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"]   # price order (CHG-187 price list); a later one is a larger model (EV-069: nano · D-442: 4o-mini)
def model_rank(m):
    """Position in MODEL_ORDER of a model name or its dated name (gpt-4.1-mini-2025-04-14 → gpt-4.1-mini); None for other models."""
    hits = [x for x in MODEL_ORDER if isinstance(m, str) and (m == x or m.startswith(x + "-2"))]
    return MODEL_ORDER.index(max(hits, key=len)) if hits else None


UNDECIDED = "책임 node 판단 불가(가짜 judge) — 신호 node {node}로 둠"


def kind_of(node):
    return str(node.get("kind") or "").split("@")[0]


def knobs_of(node):
    """EV-043 CHG-209: the knob list of a node — by kind@version when listed (retrieve@2), else by kind name (retrieve@1 · validate · llm).
    D-476 (P-1): a function node's options are the ones its Pack declares (with_options), else none. D-525 덧 34: a node sharing its
    store (nodes_of) has no index options."""
    own = KNOBS.get(str(node.get("kind") or ""), KNOBS.get(kind_of(node), {}))
    if node.get("_options"):   # D-492 덧 25 (L-1): a retriever's declaration (split) adds to its own options, never hides them
        own = dict(node["_options"]) if kind_of(node) == "custom_code" else {**own, **node["_options"]}
    if node.get("_graph"):   # D-543 ⑥ ②: the node that reads the graph file — its relations' conditions (graphrel)
        own = {**own, "relation": graphrel.knob(node)}
    return {k: v for k, v in own.items() if not k.startswith("index.")} if node.get("_shared_store") else own


# ---------------------------------------------------------------- D-476 (보고 072): a function node's options, as its Pack declares them
OPTION_TYPES = {"number": "float", "integer": "int"}
ORDER_PREFIX, ORDER_NONE = "check_first_", "none"   # D-565 덧 3 (Pack 72d4414): check_first_<group> = the check to see first — none (as now) · s4_5 (= 4.5)


def is_order_knob(knob):
    return isinstance(knob, str) and knob.startswith(ORDER_PREFIX)


def order_value(section):
    """A section as the Pack's choice names it (packrt choices are names): 4.5 → s4_5."""
    return "s" + str(section).replace(".", "_")


def order_section(value):
    """The choice back as its section (s4_5 → 4.5); None for none · anything else."""
    return value[1:].replace("_", ".") if isinstance(value, str) and value.startswith("s") and value != ORDER_NONE else None


def pack_options(folder):
    """{node: {name: option spec}} of the Pack copy's declared function node options, read through packrt's node_options only (packrt ≥
    0.15.0) — the Pack's types, limits, step, loosen and label (P-2: the Pack's own ranges); spec['option'] = its graph (one name = one
    value in a graph, set_option) and its present value under '_value'. {} without a declaration, a readable Pack or that reading."""
    try:
        from packrt.ir import node_options
        rows = node_options(folder) if folder and Path(folder).is_dir() else []
    except Exception:   # noqa: BLE001 - no reading (packrt < 0.15.0) or a Pack that does not load: no options
        return {}
    out = {}
    for r in rows:
        t = r.get("type")
        spec = ({"type": "choice", "values": list(r.get("choices") or [])} if t == "choice" else {"type": "choice", "values": [False, True]} if t == "switch"
                else {"type": OPTION_TYPES[t], "min": r.get("min"), "max": r.get("max"), **({"step": r["step"]} if r.get("step") is not None else {})}
                if t in OPTION_TYPES else None)
        if spec is None:
            continue
        spec.update(**({"loosen": r["loosen"]} if r.get("loosen") in ("up", "down") else {}), option=r.get("graph"), **({"label": r["label"]} if r.get("label") else {}))
        out.setdefault(r.get("node"), {})[r.get("name")] = {**spec, "_value": r.get("value")}
    return out


def with_options(nodes, folder):
    """The node map with each declared function node's options attached (`_options` · `_option_values`); the same map when none.
    D-492 덧 15 · 25 (packrt 193 (가)): a retrieve@2 node's declaration (the manual DB's cut, split) is attached too."""
    opts = pack_options(folder)
    if not opts:
        return nodes
    out = dict(nodes)
    for name, o in opts.items():
        if name in out and (kind_of(out[name]) == "custom_code" or str(out[name].get("kind") or "").startswith("retrieve@2")):
            out[name] = {**out[name], "_options": {k: {kk: vv for kk, vv in v.items() if kk != "_value"} for k, v in o.items()},
                         "_option_values": {k: v["_value"] for k, v in o.items()}}
    return out


def option_view(node, knob):
    """What the hypothesis table shows of an option (Q-4 (가) — the Pack's label, its limits and the present value on mouse-over); None
    for anything that is not a declared option. D-543 ⑥ ②: the relation option — its changes and each relation's present condition."""
    if knob == "relation" and node.get("_graph"):
        return {"label": "관계 조건", "range": "좁힘 · 넓힘 · 끔", "now": " · ".join(f"{r['id']} {graphrel.where_text(r['when'])}"
                                                                         for r in node["_graph"]["relations"])}
    spec = (node.get("_options") or {}).get(knob)
    if not spec:
        return None
    if spec["type"] == "choice":
        rng = "켜기 · 끄기" if spec["values"] == [False, True] else " · ".join(map(str, spec["values"]))
    else:
        rng = f"{spec['min']} ~ {spec['max']}" + (f" · {spec['step']}씩" if spec.get("step") is not None else "")
    return {"label": spec.get("label") or knob, "range": rng, "now": (node.get("_option_values") or {}).get(knob)}


def nodes_of(rel):
    """{name: node}; D-525 덧 34: a node whose store another node of the graph uses too is marked (_shared_store) — its index options
    must stay the same as the other's (packrt's structure check), so one node's own index change is no option."""
    nodes = (rel.get("graph") or {}).get("nodes", [])
    store = lambda n: (n.get("params") or {}).get("store") if isinstance((n.get("params") or {}).get("store"), str) else None   # noqa: E731
    users = {}
    for n in nodes:
        if store(n):
            users.setdefault(store(n), []).append(n["name"])
    return {n["name"]: ({**n, "_shared_store": store(n)} if len(users.get(store(n)) or []) > 1 else n) for n in nodes}


def current(node, knob):
    """The node's present value of a knob (validate knobs live in its 'evidence' or 'unsafe_assertion' rule)."""
    if knob in (node.get("_option_values") or {}):   # D-476: a declared option's present value
        return node["_option_values"][knob]
    p = node.get("params") or {}
    if knob == "fallback.min_results":
        return (p.get("fallback") or {}).get("min_results")
    if knob in ("min_score", "min_items", "require_source_types"):
        return next(((r.get("evidence") or {}).get(knob) for r in p.get("rules") or [] if isinstance(r, dict) and "evidence" in r), None)
    if knob == "deny":
        return next(((r.get("unsafe_assertion") or {}).get("deny") for r in p.get("rules") or [] if isinstance(r, dict) and "unsafe_assertion" in r), None)
    if knob == "filter":   # EV-043: the keys a filter-key hypothesis may take out
        return list((p.get("filter") or {}).keys()) if isinstance(p.get("filter"), dict) else None
    if "." in knob:   # EV-043: dotted params (fuse.k · rerank.weights.sensor)
        cur = p
        for part in knob.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        return cur
    return p.get(knob)


def present(node, knob, spec):
    """current(), or when unset the knob's default: another knob's value (default_from — D-418: rerank.pool = top_k) or a fixed one."""
    cur = current(node, knob)
    if cur is None:
        cur = current(node, spec["default_from"]) if spec.get("default_from") else spec.get("default")
    return cur


def rule_index(node, key):
    return next((i for i, r in enumerate((node.get("params") or {}).get("rules") or []) if isinstance(r, dict) and key in r), None)


# ---------------------------------------------------------------- the change lines of one hypothesis (the person's edit shapes)
def _ops_retrieve2(knob, value, p, nid, graph_id):
    """EV-043 CHG-209: retrieve@2 knob → edit ops. A dotted knob changes only its dict (None when the node has no such dict); the
    sensor weight writes both weights (retrieval = 1 − sensor, unrounded — the D-267 sum-to-1 check); a filter key comes out of the
    filter, and the last key takes the filter away."""
    one = lambda param, v: [{"op": "set", "graph": graph_id, "node": nid, "param": param, "value": v}]
    if knob in ("method", "top_k", "score_threshold"):
        return one(knob, value)
    if knob == "fuse.k":
        return one("fuse", {**p["fuse"], "k": value}) if isinstance(p.get("fuse"), dict) else None
    if knob == "fallback.min_results":
        return one("fallback", {**p["fallback"], "min_results": value}) if isinstance(p.get("fallback"), dict) else None
    if knob == "rerank.weights.sensor":
        if not isinstance(p.get("rerank"), dict) or not isinstance(value, (int, float)):
            return None
        return one("rerank", {**p["rerank"], "weights": {"retrieval": 1 - value, "sensor": value}})
    if knob == "rerank.pool":   # D-418 C: only the pool inside the rerank dict
        return one("rerank", {**p["rerank"], "pool": value}) if isinstance(p.get("rerank"), dict) else None
    if knob.startswith("index."):   # EV-051 CHG-W2-11: the index dict is made when the node has none (sections whole)
        return one("index", {**(p["index"] if isinstance(p.get("index"), dict) else {}), knob.split(".", 1)[1]: value})
    if knob == "filter":
        f = p.get("filter")
        if not isinstance(f, dict) or value not in f:
            return None
        rest = {k: v for k, v in f.items() if k != value}
        return one("filter", rest) if rest else [{"op": "unset", "graph": graph_id, "node": nid, "param": "filter"}]
    return None


def option_ops(h, node, graph_id, prompt_text=None):
    """The edit ops an option hypothesis makes (the same op shapes as the node popover): set · write_file. None when it cannot be made."""
    knob, value, p, nid = h["knob"], h["value"], node.get("params") or {}, node["name"]
    spec = (node.get("_options") or {}).get(knob)
    if spec:   # D-476: one declared option value — every node of that graph declaring the name gets it (packrt set_option)
        return [{"op": "set_option", "graph": spec["option"], "node": nid, "option": knob, "value": value}]
    if knob in ("top_k", "search", "temperature", "max_tokens", "model"):
        return [{"op": "set", "graph": graph_id, "node": nid, "param": knob, "value": value}]
    if str(node.get("kind") or "") == "retrieve@2":   # EV-043 CHG-209
        return _ops_retrieve2(knob, value, p, nid, graph_id)
    if knob == "fallback.min_results":
        return [{"op": "set", "graph": graph_id, "node": nid, "param": "fallback", "value": {**(p.get("fallback") or {}), "min_results": value}}]
    if knob in ("min_score", "min_items", "require_source_types"):
        i = rule_index(node, "evidence")
        return None if i is None else [{"op": "set", "graph": graph_id, "node": nid, "param": f"rules[{i}].evidence",
                                       "value": {**p["rules"][i]["evidence"], knob: value}}]
    if knob == "deny":
        i = rule_index(node, "unsafe_assertion")
        return None if i is None else [{"op": "set", "graph": graph_id, "node": nid, "param": f"rules[{i}].unsafe_assertion",
                                       "value": {**p["rules"][i]["unsafe_assertion"], "deny": value}}]
    if knob == "prompt" and isinstance(value, dict) and prompt_text is not None and p.get("prompt"):
        text = prompt_text
        line = str(value.get("line") or "").strip()
        if not line:
            return None
        after = value.get("insert_after")
        if after:
            if after not in text:
                return None
            text = text.replace(after, after.rstrip("\n") + "\n" + line, 1)
        else:
            text = text.rstrip("\n") + "\n" + line + "\n"
        return [{"op": "write_file", "path": p["prompt"], "text": text}]
    return None


# ---------------------------------------------------------------- the Pack's own limits (D-463 덧 29)
def pack_limits(folder):
    """{'top_k': (n, '<schema>.<field>')}: the smallest maxItems of an array of hits (items with record_id or score) in the Pack's schemas/
    — a retrieve node's hits flow out through such a field, so a top_k over it fails every case at run; {} when none is found."""
    import json as _json
    best = None
    for f in sorted((Path(folder) / "schemas").glob("*.json")) if (Path(folder) / "schemas").is_dir() else []:
        try:
            doc = _json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stack = [(doc, "")]
        while stack:
            d, path = stack.pop()
            if not isinstance(d, dict):
                continue
            for k, v in (d.get("properties") or {}).items():
                if isinstance(v, dict) and v.get("type") == "array" and isinstance(v.get("maxItems"), int):
                    it = v.get("items") or {}
                    keys = set(it.get("required") or []) | set((it.get("properties") or {}).keys())
                    if keys & {"record_id", "score"} and (best is None or v["maxItems"] < best[0]):
                        best = (v["maxItems"], f"{f.stem}.{k}")
                stack.append((v, k))
            for k in ("$defs", "definitions"):
                for v in (d.get(k) or {}).values():
                    stack.append((v, ""))
    return {"top_k": best} if best else {}


# ---------------------------------------------------------------- D-478 덧 26: an output_schema break, the field only
_SCHEMA_FAULT = re.compile(r"출력이 (\w+) 형식에 맞지 않습니다: (.*)", re.S)
_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _schema_doc(folder, name):
    """The Pack schema `name` (pack.yaml `schemas:` → its file, else a schemas/*.json titled so); None when not found or unreadable."""
    import json as _json
    folder = Path(folder) if folder else None
    if not folder or not folder.is_dir():
        return None
    rel = None
    try:
        import yaml
        rel = ((yaml.safe_load((folder / "pack.yaml").read_text(encoding="utf-8")) or {}).get("schemas") or {}).get(name)
    except Exception:   # noqa: BLE001 - no pack.yaml or no mapping: look by title
        rel = None
    files = [folder / rel] if isinstance(rel, str) else sorted((folder / "schemas").glob("*.json")) if (folder / "schemas").is_dir() else []
    for f in files:
        try:
            doc = _json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rel, str) or doc.get("title") == name:
            return doc
    return None


def _fields(doc, want):
    """Dotted field paths of a schema where want(prop_name, spec, parent_spec) holds ('[]' for an array item)."""
    out, stack = [], [(doc, "")]
    while stack:
        d, path = stack.pop()
        if not isinstance(d, dict):
            continue
        for k, v in (d.get("properties") or {}).items():
            here = f"{path}.{k}" if path else k
            if want(k, v, d):
                out.append(here)
            stack.append((v, here))
        if isinstance(d.get("items"), dict):
            stack.append((d["items"], f"{path}[]"))
        for k in ("allOf", "anyOf", "oneOf"):
            stack += [(v, path) for v in d.get(k) or []]
        for k in ("$defs", "definitions"):
            stack += [(v, "") for v in (d.get(k) or {}).values()]
    return sorted(set(out))


def schema_fault(case, folder):
    """D-478 덧 26 (packrt 184 덧 1): a failed case whose node output broke its schema (packrt NodeError code output_schema) →
    {node, code, schema, rule, fields, …} — an enum break names the field(s) whose enum is that list and the list (the Pack's own
    values), a missing required field its place, an unexpected field-like name itself, a type break the expected type. The value
    the model wrote is never kept (it may carry the case's data). None for any other case."""
    import ast
    err = (case or {}).get("error") or {}
    m = _SCHEMA_FAULT.search(str(err.get("summary") or ""))
    if not m:
        return None
    name, msg = m.group(1), m.group(2)
    doc = _schema_doc(folder, name) or {}
    out = {"node": err.get("node"), "code": "output_schema", "schema": name}
    if (e := re.search(r"is not one of (\[.*\])", msg, re.S)):
        try:
            allowed = ast.literal_eval(e.group(1))
        except (ValueError, SyntaxError):
            allowed = None
        allowed = allowed if isinstance(allowed, list) and all(isinstance(x, (str, int, float, bool, type(None))) for x in allowed) else None
        fields = _fields(doc, lambda k, v, d: isinstance(v, dict) and allowed is not None and v.get("enum") == allowed)
        fields += [f + "[]" for f in _fields(doc, lambda k, v, d: isinstance(v, dict) and isinstance(v.get("items"), dict) and allowed is not None
                                             and v["items"].get("enum") == allowed)]
        return {**out, "rule": "enum", "fields": sorted(set(fields)), **({"allowed": allowed} if allowed is not None else {})}
    if (e := re.search(r"'([^']+)' is a required property", msg)):
        f = e.group(1) if _FIELD.match(e.group(1)) else None
        paths = _required_at(doc, f) if f else []
        return {**out, "rule": "required", "fields": paths or ([f] if f else [])}
    if (e := re.search(r"\(((?:'[^']*'(?:, )?)+) (?:was|were) unexpected\)", msg)):
        names = re.findall(r"'([^']*)'", e.group(1))
        return {**out, "rule": "extra", "fields": [x for x in names if _FIELD.match(x)]}
    if (e := re.search(r"is not of type '(\w+)'", msg)):
        return {**out, "rule": "type", "expected": e.group(1), "fields": []}
    return {**out, "rule": "other", "fields": []}


def _required_at(doc, field):
    """The dotted places where `field` is a required property of an object in the schema."""
    out, stack = [], [(doc, "")]
    while stack:
        d, path = stack.pop()
        if not isinstance(d, dict):
            continue
        if field in (d.get("required") or []):
            out.append(f"{path}.{field}" if path else field)
        for k, v in (d.get("properties") or {}).items():
            stack.append((v, f"{path}.{k}" if path else k))
        if isinstance(d.get("items"), dict):
            stack.append((d["items"], f"{path}[]"))
        for k in ("allOf", "anyOf", "oneOf"):
            stack += [(v, path) for v in d.get(k) or []]
        for k in ("$defs", "definitions"):
            stack += [(v, "") for v in (d.get(k) or {}).values()]
    return sorted(set(out))


# ---------------------------------------------------------------- checks (CHG-181 ④)
SHARED_STORE = "버림 — store {store}를 다른 node와 함께 씀(index 옵션은 함께 바꿔야 함)"
def option_check(h, nodes, visible, real, limits=None):
    """None when the option hypothesis may be tried, else the reason it is discarded ('버림 — …'). limits (D-463 덧 29): the Pack's own limits
    (pack_limits) — a retrieve node's top_k over the Pack schema's hit list is dropped before any Run."""
    node = nodes.get(h.get("node"))
    if not node:
        return f"버림 — 이 Pack에 없는 node {h.get('node')}"
    spec = knobs_of(node).get(h.get("knob"))
    if not spec and node.get("_shared_store") and str(h.get("knob")).startswith("index."):
        return SHARED_STORE.format(store=node["_shared_store"])   # D-525 덧 34
    if not spec and kind_of(node) == "custom_code" and node.get("_options"):
        return f"버림 — 선언에 없는 옵션({h.get('knob')})"   # D-476
    if not spec:
        return f"버림 — {kind_of(node)} node의 옵션이 아님({h.get('knob')})"
    lim = (limits or {}).get("top_k")
    if lim and h.get("knob") == "top_k" and str(node.get("kind") or "").startswith("retrieve") and "min" in spec:
        spec = {**spec, "min": min(spec["min"], lim[0])}   # D-687 9-b: the range the agent was shown (over the limit keeps its own words below)
    v, cur = h.get("value"), present(node, h["knob"], spec)   # EV-051: an unset overlap is packrt's 0 · D-418: an unset pool is top_k
    t = spec["type"]
    if t == "filter_key":   # EV-043: one key of the node's present filter, taken out
        if not isinstance(v, str) or v not in (cur or []):
            return f"버림 — 필터에 없는 키({v})"
    elif t in ("int", "float"):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or (t == "int" and int(v) != v) or not spec["min"] <= v <= spec["max"]:
            return f"버림 — 범위 밖 값({h['knob']} {v}, {spec['min']} ~ {spec['max']})"
        if spec.get("step") is not None and abs((v - spec["min"]) / spec["step"] - round((v - spec["min"]) / spec["step"])) > 1e-9:   # D-476: the Pack's step
            return f"버림 — 한도 칸 위가 아닌 값({h['knob']} {v}, {spec['min']} ~ {spec['max']} · {spec['step']}씩)"
        if cur is not None and ((spec.get("loosen") == "down" and v < cur) or (spec.get("loosen") == "up" and v > cur)):
            return "버림(D-95) — 검사를 느슨하게 함"
    elif t == "choice" and v not in spec["values"]:
        return f"버림 — 고를 수 없는 값({v})"
    elif t == "choice" and spec.get("loosen") in ("up", "down") and cur in spec["values"]:   # D-476: later in the list = up (switch: false → true)
        i, j = spec["values"].index(v), spec["values"].index(cur)
        if (spec["loosen"] == "up" and i > j) or (spec["loosen"] == "down" and i < j):
            return "버림(D-95) — 검사를 느슨하게 함"
    elif t == "list":
        if not isinstance(v, list):
            return "버림 — 목록 값이 아님"
        if spec.get("loosen") == "remove" and cur and any(x not in v for x in cur):
            return "버림(D-95) — 검사를 느슨하게 함"
    elif t == "model":
        if model_rank(v) is None:
            return f"버림 — 쓸 수 없는 모델({v})"
        if real and model_rank(cur) is not None and model_rank(v) > model_rank(cur):
            return "버림 — 더 큰 모델은 먼저 묻기"
    elif t == "prompt" and not (isinstance(v, dict) and str(v.get("line") or "").strip()):
        return "버림 — 더할 프롬프트 줄이 없음"
    if h["knob"] in ("index.chunk_size", "index.overlap"):   # EV-051 CHG-W2-11 (N-8): packrt's rule — overlap below the chunk size
        size = v if h["knob"] == "index.chunk_size" else current(node, "index.chunk_size")
        over = v if h["knob"] == "index.overlap" else (current(node, "index.overlap") or 0)
        if size is None and h["knob"] == "index.overlap":
            return "버림 — 청크 크기가 없으면 오버랩을 바꿀 수 없음"
        if size is not None and over >= size:
            return f"버림 — 오버랩은 청크보다 작아야 함(오버랩 {over} · 청크 {size})"
    if h["knob"] == "top_k" and str(node.get("kind") or "").startswith("retrieve") and (limits or {}).get("top_k") and isinstance(v, (int, float)) and v > limits["top_k"][0]:
        return f"버림 — Pack schema 한도 넘음(top_k {v} > {limits['top_k'][0]} · {limits['top_k'][1]} maxItems)"
    if h["knob"] == "rerank.pool" and not isinstance(current(node, "rerank"), dict):
        return "버림 — 재정렬(rerank)이 없는 node"
    if (msg := _pool_vs_top_k(node, h["knob"], v)):
        return msg
    if v == cur:
        return "버림 — 지금 값과 같음"
    if not set(map(str, h.get("predicts") or [])) & visible:
        return "버림 — 바뀔 case가 보이는 세트에 없음"
    return None


def _pool_vs_top_k(node, knob, v):
    """D-418 C (packrt's load rule): the rerank pool is never below top_k — a pool under the node's top_k, or a top_k over its set pool."""
    top_k, pool = current(node, "top_k"), current(node, "rerank.pool")
    if knob == "rerank.pool" and isinstance(top_k, int) and isinstance(v, int) and v < top_k:
        return f"버림 — 후보 수는 top_k보다 작을 수 없음(후보 {v} · top_k {top_k})"
    if knob == "top_k" and str(node.get("kind") or "") == "retrieve@2" and isinstance(pool, int) and isinstance(v, int) and v > pool:
        return f"버림 — top_k는 후보 수보다 클 수 없음(top_k {v} · 후보 {pool})"
    return None


# ---------------------------------------------------------------- rule hypotheses (fake mode: cheapest change first)
def option_rule_hypotheses(node, fix_ids, tried=(), knobs=None):
    """Number and choice knobs of the node within range, one step at a time, never loosening; prompt text is never written here.
    knobs (A-27): the option list to step — the agent's iteration fills from what the agent may be shown (fill_knobs)."""
    knobs = knobs_of(node) if knobs is None else knobs
    out = []
    for knob, spec in knobs.items():
        if spec["type"] == "relation" or is_order_knob(knob):   # D-560 #6 · D-565 #7: the relation · check-order kinds' own steps
            continue
        if spec.get("needs") and current(node, spec["needs"]) is None:   # EV-051 CHG-W2-12: no overlap where there is no chunk size
            continue
        cur = present(node, knob, spec)
        s = spec.get("step", 1)   # EV-051 CHG-W2-12: the knob's own step (1 for the others)
        if spec["type"] == "int" and cur is None and spec.get("start"):
            steps = list(spec["start"])
        elif spec["type"] == "int" and isinstance(cur, int):
            steps = [cur + s, cur - s, cur + 2 * s, cur - 2 * s]
        elif spec["type"] == "float" and isinstance(cur, (int, float)):
            steps = [round(cur + 0.05, 2), round(cur + 0.1, 2)] if spec.get("loosen") == "down" else [round(cur + 0.1, 2), round(cur + 0.2, 2)]
            if str(node.get("kind") or "") == "retrieve@2":   # EV-043 (approval review A3-N2 (d)): a retriever number may also step down
                steps += [round(cur - 0.1, 2)]
        elif spec["type"] == "choice" and cur in spec["values"]:
            steps = [x for x in spec["values"] if x != cur]
        else:
            continue
        for v in steps:
            if spec["type"] in ("int", "float") and not spec["min"] <= v <= spec["max"]:
                continue
            if spec.get("loosen") == "down" and cur is not None and v < cur:
                continue
            if _pool_vs_top_k(node, knob, v):   # D-418 C
                continue
            h = {"node": node["name"], "knob": knob, "value": v, "predicts": list(fix_ids), "why": "규칙 가설(가짜 모드) — 싼 변경부터", "source": "rule"}
            if (h["node"], knob, json.dumps(v)) not in tried:
                out.append(h)
    def step(h):   # the size of the change: numbers by distance in the knob's steps, a choice counts as one step
        spec, cur = knobs[h["knob"]], current(node, h["knob"])
        if cur is None and spec.get("start"):   # EV-051: the start values are 1, 2, … steps away, in their order
            return spec["start"].index(h["value"]) + 1
        cur = present(node, h["knob"], spec)
        return abs(h["value"] - cur) / spec.get("step", 1) if isinstance(h["value"], (int, float)) and isinstance(cur, (int, float)) else 1
    return sorted(out, key=step)



# ---------------------------------------------------------------- D-549 덧 3 0단계: the slot a hypothesis kind plugs into
# A hypothesis is {kind, node, knob, value, …}; no kind = "option". Each kind gives its screen word (label), its rule steps on a node
# (make), its allowed range (check: None, or the '버림 — …' reason), its change lines (ops) and whether its whole space may be tried
# (exhaustive — the B part of D-549). One kind today, as before. dev-2's relation condition (narrow · widen · off on a graph relation,
# graphrel) is meant to plug in here as kind "relation"; the efficiency wheel's structure shapes (STRUCT_SHAPES) keep their own path.
def bundle_parts(h):
    """D-549 B: an option bundle's options as option hypotheses, in its order (the bundle's own predicts on each)."""
    return [{"node": h.get("node"), "knob": k, "value": v, "predicts": (h.get("predicts") or (h.get("detail") or {}).get("predicts") or [])}
            for k, v in (h.get("value") or {}).items()] if isinstance(h.get("value"), dict) else []


def bundle_check(h, nodes, visible, real, limits=None):
    """An option bundle may be tried when each of its options may (the first reason otherwise) and the set keeps packrt's pairs."""
    from . import autogrid
    parts = bundle_parts(h)
    if not parts:
        return "버림 — 묶음에 옵션이 없음"
    for p in parts:
        why = option_check(p, nodes, visible, real, limits=limits)
        if why:
            return why
    return None if autogrid.pair_ok(nodes[h["node"]], h["value"]) else autogrid.DROP_PAIR


def bundle_ops(h, node, graph_id, prompt_text=None):
    return change_bundle(bundle_parts(h), node, graph_id, prompt_text)


def relation_rule_hypotheses(node, fix_ids, tried=(), knobs=None):
    """D-543 ⑥ ② (D-560 #6 its own kind): narrow · off for the wasted cases, widen for the missed ones (graphrel) — on a node that reads
    the graph file, when the option list given (A-27) has the relation."""
    knobs = knobs_of(node) if knobs is None else knobs
    return graphrel.rule_steps(node, fix_ids, tried) if (knobs.get("relation") or {}).get("type") == "relation" else []


def relation_check(h, nodes, visible, real, limits=None):
    """None when the relation change may be tried, else the reason ('버림 — …'): the node is there, reads the graph file, and the change
    is one graphrel allows (a relation of the graph · narrow · widen · off · its condition)."""
    node = nodes.get(h.get("node"))
    if not node:
        return f"버림 — 이 Pack에 없는 node {h.get('node')}"
    if (knobs_of(node).get("relation") or {}).get("type") != "relation":
        return f"버림 — {kind_of(node)} node의 옵션이 아님(relation)"
    return graphrel.check(h.get("value"), node)


def relation_ops(h, node, graph_id, prompt_text=None):
    """D-543 ⑥ ③: a new graph version = one write_file of the graph file; None off a node that reads no graph."""
    return graphrel.ops(node, h["value"]) if node.get("_graph") else None


def order_rule_hypotheses(node, fix_ids, tried=(), knobs=None):
    """D-565 #7 (덧 3): one step per (symptom group, person's check) the 헛걸음 fix cases point to (autoorder.pairs, most cases first) —
    that group's check_first_<group> set to the person's check when the Pack offers it there and it is not the present value."""
    knobs = knobs_of(node) if knobs is None else knobs
    out = []
    for p in node.get("_order_pairs") or []:
        knob, v = f"{ORDER_PREFIX}{p['group'].lower()}", order_value(p["before"])
        spec, cases = knobs.get(knob), [c for c in p["cases"] if c in fix_ids]
        names = [t["node"] for t in (node.get("_order_twins") or {}).get(knob) or []] or [node["name"]]
        if (spec or {}).get("type") != "choice" or v not in spec["values"] or v == present(node, knob, spec) or not cases \
                or any((n, knob, json.dumps(v)) in tried for n in names):   # tried on a twin node = tried
            continue
        out.append({"node": node["name"], "knob": knob, "value": v, "predicts": cases,
                    "why": f"헛걸음 {len(cases)}건 · 사람이 고른 점검 {p['before']}(추천 {p['after']})", "source": "rule"})
    return out


def order_check(h, nodes, visible, real, limits=None):
    """None when the check-order value may be tried: the node declares that group's choice and the value is one of its checks (not none —
    going back to the present order is never a fix of a 헛걸음)."""
    node = nodes.get(h.get("node"))
    if not node:
        return f"버림 — 이 Pack에 없는 node {h.get('node')}"
    spec = knobs_of(node).get(h.get("knob"))
    if (spec or {}).get("type") != "choice":
        return f"버림 — {kind_of(node)} node의 옵션이 아님({h.get('knob')})"
    v = h.get("value")
    return None if order_section(v) and v in spec["values"] else f"버림 — 점검 순서 꼴이 아님(그 증상군 Pack 선택지의 점검 하나 s<절> · 받은 값 {v!r})"


def order_ops(h, node, graph_id, prompt_text=None):
    """One set_option line on every node declaring the option (autoorder.twins), the step's node first — a first Run and a next Run in
    different orders would undo it."""
    spec = (node.get("_options") or {}).get(h["knob"])
    if not spec:
        return None
    at = [{"node": node["name"], "graph": spec["option"]}]
    at += [t for t in (node.get("_order_twins") or {}).get(h["knob"]) or [] if t["node"] != node["name"]]
    return [{"op": "set_option", "graph": t["graph"], "node": t["node"], "option": h["knob"], "value": h["value"]} for t in at]


HYP_KINDS = {"option": {"label": "옵션", "make": option_rule_hypotheses, "check": option_check, "ops": option_ops, "exhaustive": True},
             # D-549 B: one combination of a small option space (autogrid) — the space is made by ④, never by the rule steps
             "bundle": {"label": "옵션 묶음", "make": lambda *a: [], "check": bundle_check, "ops": bundle_ops, "exhaustive": False},
             # D-560 #6: dev-2's graph relation condition (narrow · widen · off) — after the option steps, as when it was an option
             "relation": {"label": "관계 조건", "make": relation_rule_hypotheses, "check": relation_check, "ops": relation_ops, "exhaustive": False},
             # D-565 #7: the check order (헛걸음 → the person's section before the Run's) — a node option, applied on its own (D-565)
             "order": {"label": "점검 순서", "make": order_rule_hypotheses, "check": order_check, "ops": order_ops, "exhaustive": False}}
UNKNOWN_KIND = "버림 — 모르는 가설 종류({kind})"


def hyp_kind(h):
    """A store row keeps its kind in detail; a relation row (knob 'relation', D-560 #6) carries none — its knob says it."""
    h = h or {}
    return (h.get("kind") or (h.get("detail") or {}).get("kind") or ("relation" if h.get("knob") == "relation" else "order" if is_order_knob(h.get("knob"))
            else "option"))


def rule_hypotheses(node, fix_ids, tried=(), knobs=None):
    """Every kind's rule steps on the node, the kinds in HYP_KINDS order (today: the option steps, cheapest change first). knobs (A-27):
    the option list the option kind steps."""
    return [h for k in HYP_KINDS.values() for h in k["make"](node, fix_ids, tried, knobs)]


def check(h, nodes, visible, real, limits=None):
    """None when the hypothesis may be tried, else the reason it is discarded ('버림 — …') — by its kind's allowed range."""
    k = HYP_KINDS.get(hyp_kind(h))
    return k["check"](h, nodes, visible, real, limits=limits) if k else UNKNOWN_KIND.format(kind=hyp_kind(h))


def ops_of(h, node, graph_id, prompt_text=None):
    """The change lines a hypothesis makes, by its kind; None when it cannot be made."""
    k = HYP_KINDS.get(hyp_kind(h))
    return k["ops"](h, node, graph_id, prompt_text) if k else None


def change_bundle(hyps, node, graph_id, prompt_text=None):
    """D-549 덧 3 0단계: a candidate row = one bundle of change lines — the row's hypotheses on one node made into ops, prompt lines one
    after another on the same text, a retrieve@2 knob building on the dict the one before set (EV-051 CHG-W2-13). None when one cannot be
    made. (dev-2's relation fix, one write_file of the graph file, is a bundle of one.)"""
    ops, at, text = [], node, prompt_text
    for h in [p for x in hyps for p in (bundle_parts(x) if hyp_kind(x) == "bundle" else [x])]:   # D-549 B: a bundle is its options
        o = ops_of({"knob": h["knob"], "value": h["value"], **({"kind": h["kind"]} if h.get("kind") else {})}, at, graph_id, text)
        if not o:
            return None
        if o[0]["op"] == "write_file":
            text = o[0]["text"]
            ops = [x for x in ops if x.get("op") != "write_file"]
        ops += o
        if str(node.get("kind") or "") == "retrieve@2":
            sets = {x["param"]: x["value"] for x in o if x.get("op") == "set" and isinstance(x.get("value"), dict)}
            at = {**at, "params": {**(at.get("params") or {}), **sets}}
    return ops

BREADTH = 4   # D-469 덧 3: the hypotheses one iteration tries, each on its own option


def fill_knobs(node, show_index, limits=None):
    """A-27 (A-26 iteration 2: index.chunk_size 800 · top_k 11 filled, both dropped): the options a rule fill of the agent's iteration
    may step — what the agent is shown (agent_knobs: makeable · index only where the server may build) within the Pack's limits."""
    return capped({**node, "_limits": limits} if limits else node, agent_knobs(node, show_index))


def fill_order(nodes, fix_ids, tried, tried_knobs, idle=(), knobs=None, first=()):
    """The rule steps a fill goes through: each option's first step, nodes in `nodes` order — A-27: the nodes named in `first` (the
    person-checked case's chain) before the others, one option each in turn, so each of them gets a place before any gets two."""
    def firsts(n):
        got = {}
        for h in rule_hypotheses(n, fix_ids, tried, knobs(n) if knobs else None):
            got.setdefault(h["knob"], h)
        return [h for k, h in got.items() if (n["name"], k) not in tried_knobs]
    live = [n for n in nodes if n["name"] not in idle]   # D-478 덧 17: a node whose option changes moved no case so far is not filled
    by = {n["name"]: n for n in live}
    lead = [firsts(by[name]) for name in dict.fromkeys(first) if name in by]
    while any(lead):
        for q in lead:
            if q:
                yield q.pop(0)
    for n in live:
        if n["name"] not in first:
            yield from firsts(n)


def fill_breadth(hyps, nodes, tried_knobs, fix_ids, tried, idle=(), knobs=None, first=()):
    """D-469 덧 2 · 3: the agent's hypotheses as given, then — while fewer than BREADTH options are covered — the first rule step of each
    option not tried yet in this wheel (tried_knobs = {(node, knob)}) and not in this answer, in fill_order (source 'rule')."""
    out = list(hyps)
    have = {(h.get("node"), h.get("knob")) for h in out}
    for h in fill_order(nodes, fix_ids, tried, tried_knobs, idle, knobs, first):
        if len(have) >= BREADTH:
            break
        if (h["node"], h["knob"]) in have:
            continue
        out.append({**h, "why": "규칙 가설 — 아직 안 시험한 옵션(탐색 폭, D-469 덧 3)"})
        have.add((h["node"], h["knob"]))
    return out


FIXED = "바로잡음: {old} → {new}(보인 값 하나)"


def fix_value(h, nodes):
    """A-27 (A-26: the agent wrote filter [] twice, shown ['symptom_group']): a filter_key value given as null · an empty list · a list of
    one key is read as that key when it is clear — the filter's one key, or the one key listed; else the hypothesis as given."""
    node = nodes.get(h.get("node")) or {}
    spec = knobs_of(node).get(h.get("knob"))
    v = h.get("value")
    if spec and spec["type"] == "relation" and isinstance(v, dict) and v.get("change") in ("narrow", "widen") and "when" not in v:
        when = graphrel.complete(node, v)   # D-543 ⑥ ②: the agent names the relation and the change, the cases give the condition
        return {**h, "value": {**v, "when": when}} if when else h
    if not spec or spec["type"] != "filter_key" or isinstance(v, str):
        return h
    keys = current(node, "filter") or []
    new = keys[0] if (v is None or v == []) and len(keys) == 1 else v[0] if isinstance(v, list) and len(v) == 1 and v[0] in keys else None
    if new is None:
        return h
    return {**h, "value": new, "why": f"{h.get('why') or ''} ({FIXED.format(old=json.dumps(v, ensure_ascii=False), new=new)})".strip()}


def doc_hypotheses(doc):
    return [{**h, "source": "document"} for h in (doc or {}).get("hypotheses") or [] if isinstance(h, dict)]


# ---------------------------------------------------------------- the hypothesis agent (real mode only; CHG-181 ②)
AGENT_SYSTEM = ("You propose fixes for one node of an agent pack. Answer only JSON: {\"hypotheses\": [{\"node\", \"knob\", \"value\", \"predicts\": [case ids], "
                "\"why\", \"evidence\", \"effect\"}]}. evidence is the list of cells in the given cases' records that show the fault, each by its "
                "address {\"case\": a given case id, \"path\": the dotted path inside that case as given, for example \"output.hits.0.cause\" or "
                "\"first_output.history.1.title\"} — never a path into expected or field_truth; effect is what the change should do to those cases; a "
                "hypothesis whose addresses are not there, or without effect or predicts, is tried after the others. Use only the given node and knobs and stay inside the ranges. For a prompt knob, value is {\"insert_after\": an exact line of "
                "the prompt or null, \"line\": the one line to add}. For a filter_key knob, value is one of its values as a string (for example "
                "\"symptom_group\"), not a list or null: that key is taken out of the filter. Never loosen a check. other_nodes, when given, are the nodes before it on the "
                "cases' path with their own knobs — a case judged in the wrong order (the first choice, the first check) is often fixed there, for "
                "example by how a retriever ranks what the judging node sees. The cases' expected values are for scoring only: the pack cannot read "
                "them when it runs, so never propose a change that points at or reads them. When the node has no knobs (only_other_nodes), propose "
                "only on other_nodes. history, when given, holds your earlier hypotheses and what became of each — do not repeat them. "
                "already_tried, when given, holds the changes earlier runs of this improvement already tested on the same version and cases "
                "(wheel names the run) and what became of each — never propose one of them again (another value of the same knob is fine). untried "
                "lists the knobs not tried yet in this wheel — propose up to 4 hypotheses, each on a different knob, untried ones first, so every knob is "
                "tried at least once. A node marked no_effect had every option change measured so far move no case (tried = how many): what it "
                "writes is not used by the judgment now — propose on it only with a reason that changes that. An other node with fault_cases is "
                "where the cases' output broke its schema (their fault names the field and the allowed values) — a fix there is in scope. "
                "With fault_quota, at least one of your hypotheses is on such a node. For a relation knob, value is {\"id\": one of its "
                "relations, \"change\": \"narrow\" | \"widen\" | \"off\"} — narrow or off a relation its used_in cases wasted a check on, widen one "
                "its missed_in cases needed; the new condition is made from the cases' own values, so give no numbers. A node with pack_limit has "
                "its top_k now (top_k_now) over what the pack can let out (top_k_max): any change on it is refused unless top_k comes to "
                "top_k_max or below, so on that node propose only top_k within the limit. Write why and effect in Korean (한국어), in short "
                "plain sentences a maintenance engineer reads. Never write the field names pack_limit, top_k_now or top_k_max in them.")

_VALUE = {"anyOf": [{"type": "number"}, {"type": "string"}, {"type": "boolean"}, {"type": "null"},
                     {"type": "object", "additionalProperties": False, "required": ["insert_after", "line"],
                      "properties": {"insert_after": {"type": ["string", "null"]}, "line": {"type": "string"}}}]}
AGENT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["hypotheses"], "properties": {"hypotheses": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
                               "required": ["node", "knob", "value", "predicts", "why", "evidence", "effect"],
                               "properties": {"node": {"type": "string"}, "knob": {"type": "string"}, "value": _VALUE,
                                              "predicts": {"type": "array", "items": {"type": "string"}}, "why": {"type": "string"},
                                              "evidence": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                                     "required": ["case", "path"],
                                                                                     "properties": {"case": {"type": "string"}, "path": {"type": "string"}}}},
                                              "effect": {"type": "string"}}}}}}
AGENT_FORMAT = {"type": "json_schema", "json_schema": {"name": "hypotheses", "strict": True, "schema": AGENT_SCHEMA}}   # D-549 덧 12: the answer's shape fixed

ANSWER_WORDS = ("expected", "expectation", "field_truth", "evaluation_truth", "max_key", "tie_order", "정답", "기대값")
DROP_ANSWERS = "버림 — 정답 읽기"   # D-463 덧 24 (c′)


def reads_answers(h):
    """D-463 덧 24 (c′): a hypothesis whose change points at or reads the suite's answers (the expected checks · the field truth) —
    the pack cannot see them when it runs, so such a change only games the score."""
    text = json.dumps(h.get("value"), ensure_ascii=False).lower()
    return any(w in text for w in ANSWER_WORDS)


def makeable(node, knob, spec):
    """D-525 덧 44 (A-25): whether a change of this option can become a change line on this node at all — an option needing another
    only where that one is set (overlap · rerank pool), a retrieve@2 dotted option only where its dict is there (fuse · fallback ·
    rerank), a filter key only where the filter has keys. A declared option (D-476) always can."""
    if knob in (node.get("_options") or {}):
        return True
    if spec.get("needs") and current(node, spec["needs"]) is None:
        return False
    if str(node.get("kind") or "") != "retrieve@2":
        return True
    if knob == "filter":
        return bool(current(node, "filter"))
    head = knob.split(".")[0]
    return "." not in knob or head == "index" or isinstance((node.get("params") or {}).get(head), dict)


def agent_knobs(node, show_index=True):
    """D-525 덧 44: the options the agent is shown for a node — only those makeable(); a filter key's values are the filter's keys (the
    one taken out); index options only when the server may build real vector DB versions (show_index, CHG-W2-14)."""
    out = {}
    for k, spec in knobs_of(node).items():
        if (show_index or not k.startswith("index.")) and makeable(node, k, spec):
            out[k] = {**spec, "values": current(node, "filter")} if spec["type"] == "filter_key" else spec
    return out


def over_limit(node):
    """D-652 9-b: (top_k now, the Pack's limit) when a retrieve node's set top_k is already over the Pack schema's limit — the structure
    check (editing 013-66) then refuses any change on that node unless it brings top_k within the limit; else None."""
    lim = ((node or {}).get("_limits") or {}).get("top_k")
    now = ((node or {}).get("params") or {}).get("top_k")
    if lim and str((node or {}).get("kind") or "").startswith("retrieve") and isinstance(now, int) and not isinstance(now, bool) and now > lim[0]:
        return now, lim[0]
    return None


def capped(node, knobs):
    """D-469 (4) · D-463 덧 29: a retrieve node's top_k shown within the Pack schema's limit (the node copy carries it as _limits).
    D-652 9-b: on a node already over the limit only top_k is shown (any other change there is refused by the structure check)."""
    lim = ((node or {}).get("_limits") or {}).get("top_k")
    if lim and "top_k" in knobs and str(node.get("kind") or "").startswith("retrieve") and isinstance(knobs["top_k"], dict) and "max" in knobs["top_k"]:
        hi = min(knobs["top_k"]["max"], lim[0])
        knobs = {**knobs, "top_k": {**knobs["top_k"], "max": hi, **({"min": min(knobs["top_k"]["min"], hi)} if "min" in knobs["top_k"] else {})}}   # D-687 9-b: one range
    if over_limit(node):
        return {k: v for k, v in knobs.items() if k == "top_k"}
    return knobs


FIELD_WORDS = (("pack_limit", "Pack 한도"), ("top_k_now", "지금 top_k"), ("top_k_max", "최대 top_k"))


def plain_reason(h):
    """D-687 9-b: the field names the agent was given do not reach the screen in its why · effect, even when it writes them."""
    out = dict(h)
    for k in ("why", "effect"):
        if isinstance(out.get(k), str):
            for a, b in FIELD_WORDS:
                out[k] = out[k].replace(a, b)
    return out


def _limit_note(node):
    ov = over_limit(node)
    return {"pack_limit": {"top_k_max": ov[1], "top_k_now": ov[0]}} if ov else {}


def agent_input(node, prompt_text, cases, knobs, others=(), show_index=True):
    """What the agent sees: the node (kind · knobs now · expect.text · prompt text), the knob list, and the 고칠 · 지킬 cases with the node's
    output there and why it failed — no hidden case, no judge reason, no person's label reason (D-94). others (EV-079, D-462 C): the
    knobbed nodes before it on the cases' path (a retriever's order decides what a judging node sees) — any of them may be proposed."""
    out = {"node": {"id": node["name"], "kind": node.get("kind"), "expect": (node.get("expect") or {}).get("text"),
                    "knobs_now": {k: present(node, k, knobs[k]) for k in knobs}, "prompt": prompt_text,   # D-469 덧 6 (나): the value in effect
                    "knobs_from_default": [k for k in knobs if current(node, k) is None and present(node, k, knobs[k]) is not None],
                    **_limit_note(node)},   # D-652 9-b
           "knobs": capped(node, knobs), "cases": cases}
    if node.get("_idle"):   # D-478 덧 17
        out["node"]["no_effect"] = {"tried": node["_idle"]}
    if others:
        out["other_nodes"] = [{"id": o["name"], "kind": o.get("kind"), "knobs": capped(o, agent_knobs(o, show_index)),   # D-525 덧 44
                               "knobs_now": {k: present(o, k, sp) for k, sp in agent_knobs(o, show_index).items()},
                               "knobs_from_default": [k for k, sp in agent_knobs(o, show_index).items() if current(o, k) is None and present(o, k, sp) is not None],
                               **({"no_effect": {"tried": o["_idle"]}} if o.get("_idle") else {}),   # D-478 덧 17
                               **_limit_note(o),   # D-652 9-b
                               **({"fault_cases": o["_fault"]} if o.get("_fault") else {})} for o in others]   # D-478 덧 27
        if not knobs:   # D-463 덧 25 (d): the responsible node has no knobs — only the other nodes can change (copy round A-9)
            out["only_other_nodes"] = True
        if any(o.get("_fault") for o in others):   # D-492 ②: one of the iteration's hypotheses on a fault node
            out["fault_quota"] = 1
    return out


AGENT_LIMIT = 24000   # characters of the agent's message


def agent_message(node, prompt_text, cases, knobs, others=(), limit=AGENT_LIMIT, history=(), show_index=True):
    """The agent's message as whole JSON within `limit`: the node, its knobs and the other nodes always whole and before the cases; when
    it does not fit, whole cases are dropped from the end and cases_cut says how many (copy round A-10: a text cut lost other_nodes)."""
    base = agent_input(node, prompt_text, [], knobs, others, show_index)
    mine, earlier = [h for h in history or () if not h.get("wheel")], [h for h in history or () if h.get("wheel")]   # D-513: already_tried
    head = {**{k: v for k, v in base.items() if k != "cases"}, **({"history": mine} if mine else {}), **({"already_tried": earlier} if earlier else {})}   # D-463 덧 26 ①
    tried = {(h.get("node"), h.get("knob")) for h in history or () if not h.get("wheel") and h.get("measured", True)}   # D-469 (4) · 덧 6 (가): measured ones only · A-28: this wheel's own
    idle = {base["node"]["id"]} if base["node"].get("no_effect") else set()
    idle |= {o["id"] for o in base.get("other_nodes") or [] if o.get("no_effect")}   # D-478 덧 17: an idle node's options are not 'untried'
    head["untried"] = [{"node": n, "knob": k} for n, ks in [(base["node"]["id"], base["knobs"])] + [(o["id"], o["knobs"]) for o in base.get("other_nodes") or []]
                       for k in ks if (n, k) not in tried and n not in idle]
    keep = list(cases)
    while True:
        out = {**head, **({"cases_cut": len(cases) - len(keep)} if len(keep) < len(cases) else {}), "cases": keep}
        text = json.dumps(out, ensure_ascii=False, default=str)
        if len(text) <= limit or not keep:
            return text
        keep = keep[:-1]


def ask_agent(gw, provider, model, node, prompt_text, cases, show_index=False, others=(), history=()):
    """show_index: the server may build real-embedding vector DB versions (EV-051 CHG-W2-21, LLMOPS_AUTO_INDEX_REAL) — else the agent
    sees no index.* knob (CHG-W2-14), on the nodes before it neither (D-525 덧 44); only options it can make (agent_knobs)."""
    knobs = agent_knobs(node, show_index)
    msg = agent_message(node, prompt_text, cases, knobs, others, history=history, show_index=show_index)
    out = gw.chat([{"role": "system", "content": AGENT_SYSTEM}, {"role": "user", "content": msg}],
                  purpose="hypothesis", provider=provider, model=model, timeout=60, response_format=AGENT_FORMAT)
    try:
        from . import autogrid
        from .gateway import strip_fence
        data = json.loads(strip_fence(out["text"]))
        hs = data.get("hypotheses") if isinstance(data, dict) else None
        sent = json.loads(msg).get("cases") or []   # D-549 덧 12: the basis read at its addresses in the cases as they were sent
        answers = autogrid.answer_strings(sent)
        mark = lambda h: {**plain_reason(h), "source": "agent", **({"basis_missing": m} if (m := autogrid.basis_missing(h, sent)) else {}),   # noqa: E731
                          **({"basis_address": x} if (x := autogrid.bad_address(h, sent, answers)) else {})}
        return [mark(h) for h in hs if isinstance(h, dict)] if isinstance(hs, list) else [], out.get("usage")
    except (ValueError, TypeError):
        return [], out.get("usage")


# ---------------------------------------------------------------- the responsible node (D-100)
def signal_node(case, on_fail=None):
    """The node of the case's first failed check that stands (EV-089 · D-491: one its re-check on the on_fail branch passed is not a
    signal; without on_fail every failure stands, as before), or None."""
    from . import autocand
    left = autocand.open_failures(case, on_fail)
    return left[0][0] if left else None


def chain(case, nodes, signal):
    """The nodes this case passed through that wrote what the signal node reads, followed back; earliest first."""
    order = [v["node"] for v in case.get("visits") or []]
    seen, want, out = set(), list((nodes.get(signal) or {}).get("reads") or []), []
    while want:
        key = want.pop()
        for n in order[:order.index(signal)] if signal in order else order:
            if n not in seen and key in ((nodes.get(n) or {}).get("writes") or []):
                seen.add(n)
                out.append(n)
                want += list((nodes.get(n) or {}).get("reads") or [])
    return sorted(out, key=order.index)


TAKE_NO_MAKER = "첫 걸음에서 받은 칸({keys})을 만든 node를 기록에서 못 찾음"


def take_chain(case, nodes, signal):
    """D-525 덧 6: (nodes, keys, None) for a two-step case whose signal node, in the second step, reads what no node of that step wrote
    (the step's input) — the first-step nodes that made what 'then.take' carried into that input: each taken key's maker is the
    first step's last visit whose output holds the value the second input has there, followed back by chain() in that step, earliest
    first. ([], keys, why) when a maker is not in the record; ([], [], None) when it is no such case (one step · the signal in step 1)."""
    from .runs import _path_get
    take, steps = (case.get("then") or {}).get("take") or {}, case.get("steps") or []
    if not take or len(steps) < 2 or signal not in [v["node"] for v in case.get("visits") or []]:
        return [], [], None
    on = chain(case, nodes, signal) + [signal]
    wrote = {s for v in case.get("visits") or [] for s in (nodes.get(v["node"]) or {}).get("writes") or []}
    if not {s for n in on for s in (nodes.get(n) or {}).get("reads") or []} - wrote:
        return [], [], None
    first, second, keys, makers = steps[0] or {}, (steps[1] or {}).get("input"), list(take), []
    for dst, src in take.items():
        want = _path_get(second, dst) if isinstance(second, dict) else None
        maker = None
        for v in reversed(first.get("visits") or []):
            out = v.get("output")
            out = json.loads(out) if isinstance(out, str) else out
            if want is not None and isinstance(out, dict) and want in [_path_get(out, src)] + [_path_get(x, src) for x in out.values()]:
                maker = v["node"]
                break
        if maker is None:
            return [], keys, TAKE_NO_MAKER.format(keys=" · ".join(keys))
        makers.append(maker)
    order = [v["node"] for v in first.get("visits") or []]
    got = {n for m in makers for n in chain(first, nodes, m) + [m]}
    return sorted(got, key=order.index), keys, None


NO_WRITER = ("router@", "validate@")   # kinds that do not write a step's answer


def final_signal(case, checks, nodes):
    """EV-079 (copy round A-8): (step, node) for an accuracy case whose final checks fail — the first failing check's step (step 1 → the
    first step's own record) and the node that wrote that step's answer last (its last visited node that writes state, not a router
    or a check); (None, None) when every final check passes or no node wrote."""
    from . import suites
    for chk in checks or []:
        src = ((case.get("steps") or [{}])[0] or {}) if chk.get("step") == 1 else case
        ok, _, _ = suites.check(suites._final_value(src), chk)
        if ok is True:
            continue
        for v in reversed(src.get("visits") or []):
            n = nodes.get(v.get("node")) or {}
            if n.get("writes") and not str(n.get("kind") or "").startswith(NO_WRITER):
                return src, v["node"]
        return None, None
    return None, None


def responsible(case, nodes, judge, signal=None):
    """{"signal", "rows": [{"node", "ok"}], "node", "decided"}: judge(node_name, visit) → True (right) · False (wrong) · None (no judge).
    signal (EV-079): the signal node when it is not a failed node check (final_signal)."""
    sig = signal or signal_node(case)
    if not sig:
        return None
    visits = {v["node"]: v for v in case.get("visits") or []}
    rows, found = [], None

    def input_of(n):   # CHG-189 '입력': what n reads came right from the nodes before it (✓ all right · ✗ one wrong · None unknown)
        before = [r for r in rows if set((nodes.get(r["node"]) or {}).get("writes") or []) & set((nodes.get(n) or {}).get("reads") or [])]
        if any(r["ok"] is False for r in before):
            return False
        return True if all(r["ok"] is True for r in before) else None
    for n in chain(case, nodes, sig):
        ok = judge(n, visits.get(n))
        rows.append({"node": n, "ok": ok, "input_ok": input_of(n)})
        if ok is False and found is None:
            found = n
    decided = any(r["ok"] is not None for r in rows)
    return {"signal": sig, "rows": rows + [{"node": sig, "ok": None, "input_ok": input_of(sig), "signal": True}], "node": found or sig, "decided": decided}


def prompt_text_of(rel, node):
    path = (node.get("params") or {}).get("prompt")
    copy = (rel.get("pack") or {}).get("copy")
    if not path or not copy:
        return None
    f = Path(copy) / path
    try:
        return f.read_text(encoding="utf-8") if f.is_file() and f.stat().st_size < 64000 else None
    except OSError:
        return None


def shares_prompt(nodes, node):
    """Other nodes that use the same prompt file (a write_file changes them too — D-99 'only its own node')."""
    path = (node.get("params") or {}).get("prompt")
    return [n for n, x in nodes.items() if n != node["name"] and path and (x.get("params") or {}).get("prompt") == path]


# ---------------------------------------------------------------- EV-069 CHG-EF-4 (D-106 · D-433): the efficiency wheel's structure hypotheses
# Rules only, in both modes (no hypothesis agent — A1-B8). Three shapes: ① a llm node's model the one right below it in MODEL_ORDER among
# the Pack's models.yaml chat models (D-442: gpt-4.1-mini → gpt-4o-mini, or gpt-4.1-nano without it; price list) ② two llm nodes of one schema where the later one reads the
# earlier one's output: the later path goes (packrt delete preview) ③ a standard node whose written keys no other node reads goes.
# A function node (custom_code) is never a hypothesis. The node set a hypothesis declares is the one its ops touch (Q-EF-7).
EFF_MODEL, EFF_MERGE, EFF_REMOVE = "구조:모델 교체", "구조:합치기", "구조:노드 빼기"
EFF_CASCADE = "구조:싼 모델 먼저"   # D-652 ②: the Pack's own check and fail way, the cheaper model first and the present one when the check fails
STANDARD_KINDS = ("llm", "validate", "retrieve", "router", "render")


def pack_chat_models(rel):
    """The chat models the Pack's models.yaml allows (every provider), or [] when it cannot be read."""
    import yaml
    copy = (rel.get("pack") or {}).get("copy")
    try:
        doc = yaml.safe_load((Path(copy) / "models.yaml").read_text(encoding="utf-8")) if copy else None
    except (OSError, yaml.YAMLError):
        return []
    return [m for p in ((doc or {}).get("providers") or {}).values() if isinstance(p, dict) for m in ((p.get("models") or {}).get("chat") or [])]


def structure_hypotheses(rel, chat_models):
    nodes = (rel.get("graph") or {}).get("nodes") or []
    readers = {}
    for n in nodes:
        for k in n.get("reads") or []:
            readers.setdefault(k, set()).add(n["name"])
    llms = [n for n in nodes if kind_of(n) == "llm"]
    out = []
    for n in llms:
        r = model_rank((n.get("params") or {}).get("model"))
        below = [m for m in chat_models if r is not None and model_rank(m) is not None and model_rank(m) < r]   # D-442: right below in the Pack's list
        to = max(below, key=model_rank) if below else None
        out.append({"node": n["name"], "knob": EFF_MODEL, "value": to, "from": (n.get("params") or {}).get("model")})
    merged = set()
    for a in llms:
        for b in llms:
            out_of = lambda n: (n.get("params") or {}).get("output") or (n.get("params") or {}).get("schema")   # packrt llm@1 'output'
            same = out_of(a) and out_of(a) == out_of(b)
            if a is not b and same and set(a.get("writes") or []) & set(b.get("reads") or []) and b["name"] not in merged:
                merged.add(b["name"])
                out.append({"node": b["name"], "knob": EFF_MERGE, "value": a["name"]})
    out += cascade_hypotheses(nodes, chat_models)
    for n in nodes:
        if kind_of(n) in STANDARD_KINDS and n["name"] not in merged and n.get("writes") and \
                not any(readers.get(k, set()) - {n["name"]} for k in n.get("writes") or []):
            out.append({"node": n["name"], "knob": EFF_REMOVE, "value": None})
    return out


def cascade_hypotheses(nodes, chat_models):
    """D-652 ②: '싼 모델 먼저' on the Pack's own check — an llm node A, a validate node V reading A's output whose on_fail is an llm node B
    smaller than A: A takes the model right below its own in the Pack's list, B takes A's present model. No new node, no new check."""
    by = {n["name"]: n for n in nodes}
    out = []
    for a in (n for n in nodes if kind_of(n) == "llm"):
        model = (a.get("params") or {}).get("model")
        r = model_rank(model)
        below = [m for m in chat_models if r is not None and model_rank(m) is not None and model_rank(m) < r]
        if not below:
            continue
        to = max(below, key=model_rank)
        for v in (n for n in nodes if kind_of(n) == "validate" and set(n.get("reads") or []) & set(a.get("writes") or [])):
            b = by.get((v.get("params") or {}).get("on_fail"))
            rb = model_rank(((b or {}).get("params") or {}).get("model"))
            if not b or b is a or kind_of(b) != "llm" or rb is None or rb >= r:
                continue
            out.append({"node": a["name"], "knob": EFF_CASCADE, "value": f"{to} · 검사 실패면 {b['name']}가 {model}", "from": model,
                        "to": to, "fallback": b["name"], "fallback_to": model, "check": v["name"]})
    return out


def structure_check(h, rel, priced):
    """None when the hypothesis may be tried, else '버림 — …' (models.yaml · price list · function node)."""
    node = nodes_of(rel).get(h["node"])
    if not node:
        return f"버림 — 이 Pack에 없는 node {h['node']}"
    if kind_of(node) not in STANDARD_KINDS:
        return "버림 — 함수 node는 바꾸지 않음"
    if h["knob"] == EFF_MODEL:
        if not h.get("value"):
            return "버림 — models.yaml에 한 칸 작은 모델 없음"
        if not priced(h["value"]):
            return f"버림 — 가격표에 없는 모델({h['value']})"
    if h["knob"] == EFF_CASCADE:
        fb = nodes_of(rel).get(h.get("fallback"))
        if not fb:
            return f"버림 — 이 Pack에 없는 node {h.get('fallback')}"
        if kind_of(fb) not in STANDARD_KINDS:
            return "버림 — 함수 node는 바꾸지 않음"
        for m in (h.get("to"), h.get("fallback_to")):
            if not priced(m):
                return f"버림 — 가격표에 없는 모델({m})"
    return None


def merge_nodes(rel, h):
    """The nodes a merge takes away (contract CHG-EF-4 ②): the later llm node and the validate nodes that check its own output (they
    read what it writes and nothing the earlier node writes) — C1-a15: rewrite_report · check_report_2; check_report stays."""
    nodes = nodes_of(rel)
    later, earlier = nodes.get(h["node"]) or {}, nodes.get(h["value"]) or {}
    lw, ew = set(later.get("writes") or []), set(earlier.get("writes") or [])
    checks = [n["name"] for n in nodes.values() if kind_of(n) == "validate" and set(n.get("reads") or []) & lw and not set(n.get("reads") or []) & ew]
    return [h["node"]] + checks


def _one_chain(nodes, edges):
    """True when the taken nodes hang together through the plain edges between them (a single node always does)."""
    left, todo = set(nodes[1:]), [nodes[0]]
    while todo:
        n = todo.pop()
        for a, b in edges:
            for x, y in ((a, b), (b, a)):
                if x == n and y in left:
                    left.discard(y)
                    todo.append(y)
    return not left


def structure_ops(rel, h):
    """(ops, the node set they touch) of a structure hypothesis; (None, why) when the graph would not hold together."""
    graph = (rel.get("pack") or {}).get("entrypoint") or (rel.get("graph") or {}).get("id")
    if h["knob"] == EFF_MODEL:
        return [{"op": "set", "graph": graph, "node": h["node"], "param": "model", "value": h["value"]}], [h["node"]]
    if h["knob"] == EFF_CASCADE:
        return ([{"op": "set", "graph": graph, "node": h["node"], "param": "model", "value": h["to"]},
                 {"op": "set", "graph": graph, "node": h["fallback"], "param": "model", "value": h["fallback_to"]}],
                sorted([h["node"], h["fallback"]]))
    return removal_ops(rel, merge_nodes(rel, h) if h["knob"] == EFF_MERGE else [h["node"]])


def _touched(ops, gone_nodes):
    touched = set(gone_nodes)
    for op in ops:
        e = op.get("edge")
        if isinstance(op.get("node"), str):
            touched.add(op["node"])
        if isinstance(e, list):
            touched.update(x for x in e if isinstance(x, str))
        elif isinstance(e, dict):
            touched.update(x for x in [e.get("from"), e.get("to"), *(e.get("join") or [])] if isinstance(x, str))
    return sorted(touched - {"END", "START"})


def removal_ops(rel, gone_nodes):
    """(ops, the node set they touch) taking `gone_nodes` out and joining the graph again; (None, why) when it cannot be joined.
    D-463 ② (packrt 0.13.0): packrt's `bridge_ops` moves every way into the group — pair edges, router cases, on_fail, joins — to one
    exit; without it (packrt 0.12.6) the delete preview and one plain chain joined again (contract v5 L1)."""
    graph = (rel.get("pack") or {}).get("entrypoint") or (rel.get("graph") or {}).get("id")
    from packrt.ir import patch
    if hasattr(patch, "bridge_ops"):
        try:
            b = patch.bridge_ops((rel.get("pack") or {}).get("copy"), graph, list(gone_nodes))
        except Exception as e:   # noqa: BLE001 - packrt refused the group: that hypothesis only
            return None, f"버림 — 빼면 다시 이을 수 없음({type(e).__name__})"
        after = b.get("after") or {}
        if not b.get("ops") or not after.get("ok"):
            first = next((x.get("message") for x in after.get("errors") or [] if isinstance(x, dict)), None) or "다시 이은 그래프 검사 실패"
            return None, f"버림 — 빼면 다시 이을 수 없음({str(first)[:120]})"
        return b["ops"], _touched(b["ops"], list(gone_nodes) + list(b.get("pruned") or []))
    from packrt.ir.patch import delete_impact
    ops, seen = [], set()
    for n in gone_nodes:   # packrt's preview of each node (its remove_node · remove_edge · join add_edge ops), once each
        for op in delete_impact((rel.get("pack") or {}).get("copy"), graph, node=n)["ops"]:
            key = json.dumps(op, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                ops.append(op)
    # contract v5 (L1, CHG-EF-4): the taken nodes' plain chain is joined again — every outside node before them to every outside node
    # after them (packrt's preview leaves the gap; observed on eff_min · C1-a15 retrieve_manual). ⑤'s apply_patch checks the whole set.
    gone = [op["edge"] for op in ops if op.get("op") == "remove_edge" and isinstance(op.get("edge"), list)]
    before = sorted({e[0] for e in gone if e[1] in gone_nodes and e[0] not in gone_nodes})
    after_ = sorted({e[1] for e in gone if e[0] in gone_nodes and e[1] not in gone_nodes})
    if not before or not after_:
        return None, "버림 — 빼면 그래프가 이어지지 않음(앞 · 뒤 node 없음)"
    # E3-A5 N2: 'every before × every after' means one chain only — taken nodes apart from each other are dropped (L0, safe side)
    if not _one_chain(gone_nodes, gone):
        return None, "버림 — 빼는 node가 한 사슬이 아님"
    if not any(op.get("op") == "add_edge" for op in ops):
        ops += [{"op": "add_edge", "graph": graph, "edge": [a, b]} for a in before for b in after_]
    return ops, _touched(ops, gone_nodes)


def join_removals(rel, info):
    """D-463 ②: an agent candidate that takes nodes out — LLMOps joins the graph again with removal_ops (the agent's own edge ops on
    those nodes go; its other ops stay after). (info, None) with the ops and node set completed, or (info, '버림 — …')."""
    gone = [o["node"] for o in info["ops"] if o.get("op") == "remove_node" and isinstance(o.get("node"), str)]
    if not gone:
        return info, None
    rops, touched = removal_ops(rel, gone)
    if rops is None:
        return info, touched

    def on_group(o):
        e = o.get("edge")
        ends = e if isinstance(e, list) else [e.get("from"), e.get("to"), *(e.get("join") or [])] if isinstance(e, dict) else []
        return o.get("op") in ("remove_node", "add_edge", "remove_edge") and (o.get("op") == "remove_node" or any(x in gone for x in ends))
    seen = {json.dumps(o, sort_keys=True, ensure_ascii=False) for o in rops}
    rest = [o for o in info["ops"] if not on_group(o) and json.dumps(o, sort_keys=True, ensure_ascii=False) not in seen]
    return {**info, "ops": rops + rest, "nodes": sorted(set(info["nodes"]) | set(touched))}, None


# ---------------------------------------------------------------- D-462 A (D-460 · D-461): the structure candidate agent (efficiency ④)
# One call (gpt-4.1) gives 10 candidates as packrt ops; the rules here come first (op kinds · function nodes · models), then ④ runs
# packrt's structure check, the fixed flow and the reached check on each (automain._eff_agent_hypotheses), and up to 6 go to ⑤.
STRUCT_SHAPES = {"model": "모델 교체", "add": "단계 더함", "remove": "단계 뺌", "merge": "합치기", "branch": "갈래", "cascade": "싼 모델 먼저"}
STRUCT_OPS = ("set", "add_node", "remove_node", "add_edge", "remove_edge")   # no prompt text this time (write_file · create_file)
STRUCT_ADDABLE = ("llm@1", "validate@2", "router@1", "render@1")
STRUCT_SET = {"llm": ("model", "temperature", "max_tokens"), "router": ("cases", "default"), "validate": ("on_fail",)}   # D-652 ②: packrt EDITABLE validate on_fail
STRUCT_PROPOSALS, STRUCT_TESTS, STRUCT_MODEL_ONLY = 10, 6, 2
# the params an added node must have (packrt 0.13.0 pack.schema.json $defs.node allOf, the addable kinds)
STRUCT_REQUIRED = {"llm@1": ["provider", "model", "prompt", "output"], "validate@2": ["rules"], "router@1": ["by", "cases", "default"],
                   "render@1": ["template", "format", "to"]}
STRUCT_SYSTEM = ("You propose structure changes that make one agent pack cheaper, need fewer LLM calls or answer faster, keeping every "
                 "item of its fixed flow. Answer only JSON: {\"candidates\": [{\"title\", \"shape\", \"ops\", \"why\", \"expect\"}]} with exactly "
                 "rules.proposals candidates, the most promising first. Write title and why in Korean — the screen shows them as they are "
                 "(D-463 덧 30); node ids, params and other ops values stay as the graph writes them. shape is one of " + ", ".join(STRUCT_SHAPES) + "; at most "
                 "rules.model_only_max candidates only change a model — the others change the structure (add · remove · merge steps, a branch, "
                 "a cheaper model first and the present one when its check fails). ops are packrt patch ops of rules.ops only, on graph "
                 "graph.id, shaped like rules.op_examples; a node with change false is never set or removed; nodes read and write only keys of "
                 "graph.state — a new state key cannot be made (a cheaper first try writes the same key as the node it stands in front of); "
                 "graph.edges are plain edges; graph.routes are branches — a router sends by its params.cases (set cases to send a route to "
                 "another node, never an edge op on a route) and a validate node sends a failing answer to its params.on_fail node (not "
                 "changed this time). models only from allowed_models, never larger than the largest model the pack uses. history holds earlier candidates "
                 "and why they failed — do not repeat them. removable lists node groups packrt can take out and join again, measured first "
                 "(ok, or why not): take nodes out only as a group marked ok. An added node has every rules.required_params of its kind. "
                 "Give rules.proposals candidates when the graph allows, structure ones too when "
                 "unsure — the rule checks and the tests decide, not you.")
STRUCT_EXAMPLES = [{"op": "set", "graph": "<graph.id>", "node": "<node id>", "param": "model", "value": "<allowed model>"},
                   {"op": "add_node", "graph": "<graph.id>", "node": {"id": "<new id>", "kind": "llm@1", "reads": ["<state key>"], "writes": ["<state key>"],
                                                                       "params": {"provider": "openai", "model": "<allowed model>", "prompt": "<an existing prompt file>", "output": "<schema>"}}},
                   {"op": "add_node", "graph": "<graph.id>", "node": {"id": "<new id>", "kind": "validate@2", "reads": ["<state key>"], "writes": ["<state key>"],
                                                                       "params": {"rules": "<params.rules of an existing validate node>", "on_fail": "<node id>"}}},
                   {"op": "set", "graph": "<graph.id>", "node": "<router id>", "param": "cases", "value": {"<case>": "<node id>"}},
                   {"op": "remove_node", "graph": "<graph.id>", "node": "<node id> (LLMOps joins its ways in and out again)"},
                   {"op": "add_edge", "graph": "<graph.id>", "edge": ["<from>", "<to>"]},
                   {"op": "remove_edge", "graph": "<graph.id>", "edge": ["<from>", "<to>"]}]
_NODE_VIEW = ("model", "temperature", "max_tokens", "output", "prompt", "by", "cases", "default", "on_fail", "rules")


def pack_state(rel):
    """{state key: type} of the release's entry graph (pack.yaml graphs.<entry>.state), or {} when it cannot be read."""
    import yaml
    copy, entry = (rel.get("pack") or {}).get("copy"), (rel.get("pack") or {}).get("entrypoint") or (rel.get("graph") or {}).get("id")
    try:
        doc = yaml.safe_load((Path(copy) / "pack.yaml").read_text(encoding="utf-8")) if copy else None
    except (OSError, yaml.YAMLError):
        return {}
    st = ((((doc or {}).get("graphs") or {}).get(entry) or {}).get("state")) or {}
    return {k: v if isinstance(v, str) else (v or {}).get("type") for k, v in st.items()} if isinstance(st, dict) else {}


def _edges(g):
    """(plain edges, router routes) as [from, to] — the release graph keeps the screen's edge records (source · target · conditional)."""
    plain, routes = [], []
    for e in g.get("edges") or []:
        if isinstance(e, dict):
            (routes if e.get("conditional") else plain).append([e.get("source"), e.get("target")])
        elif isinstance(e, (list, tuple)) and len(e) == 2:
            plain.append(list(e))
    return plain, routes


def _changeable(node):
    return kind_of(node) in STANDARD_KINDS


def structure_agent_input(rel, fixed_items, by_node, allowed, history, removable=None):
    """What the structure candidate agent reads (D-461 A-1): the step graph (editable params only), the fixed flow, the base Runs' per-node
    measures, the price list of the allowed models, the rules and the earlier iterations' results — no case text, no hidden case."""
    from .autocand import CHAT_USD_PER_1M, chat_price
    g = rel.get("graph") or {}
    nodes = [{"id": n["name"], "kind": n.get("kind"), "reads": n.get("reads") or [], "writes": n.get("writes") or [], "change": _changeable(n),
              "params": {k: v for k, v in (n.get("params") or {}).items() if k in _NODE_VIEW}} for n in g.get("nodes") or []]
    prices = {k: v for k, v in CHAT_USD_PER_1M.items() if any(chat_price(m) == v and (m == k or m.startswith(k + "-2")) for m in allowed)}
    plain, routes = _edges(g)
    return {"graph": {"id": (rel.get("pack") or {}).get("entrypoint") or g.get("id"), "state": pack_state(rel), "nodes": nodes, "edges": plain, "routes": routes},
            "fixed_flow": [{"id": i.get("id"), "what": i.get("what")} for i in fixed_items or []],
            "per_node": by_node, "prices_usd_per_1m": prices, "allowed_models": list(allowed),
            "rules": {"proposals": STRUCT_PROPOSALS, "model_only_max": STRUCT_MODEL_ONLY, "ops": list(STRUCT_OPS), "addable": list(STRUCT_ADDABLE),
                      "set": {k: list(v) for k, v in STRUCT_SET.items()}, "op_examples": STRUCT_EXAMPLES, "required_params": STRUCT_REQUIRED},
            "removable": list(removable or []), "history": list(history or [])}


STRUCT_LIMIT = 48000   # characters of the structure agent's message


def structure_message(payload, limit=STRUCT_LIMIT):
    """The agent's message as whole JSON within `limit` (리드 C 81f782b 꼴): the graph, fixed flow, measures, prices, models and rules
    always whole; when it does not fit, the oldest history entries go first, then the removable groups packrt refused (the ok ones
    stay), each counted in history_cut · removable_cut."""
    hist, rem = list(payload.get("history") or []), list(payload.get("removable") or [])
    ok, refused = [g for g in rem if g.get("ok")], [g for g in rem if not g.get("ok")]
    cut_h = cut_r = 0
    while True:
        out = {**payload, "history": hist[cut_h:], "removable": ok + refused[:len(refused) - cut_r],
               **({"history_cut": cut_h} if cut_h else {}), **({"removable_cut": cut_r} if cut_r else {})}
        text = json.dumps(out, ensure_ascii=False, default=str)
        if len(text) <= limit:
            return text
        if cut_h < len(hist):
            cut_h += 1
        elif cut_r < len(refused):
            cut_r += 1
        else:
            return text   # nothing left to drop: the graph itself is larger than the limit — sent whole rather than cut


def ask_structure_agent(gw, model, payload):
    """One call to the structure candidate agent (an OpenAI model — the allowed agent models are OpenAI's). (candidates, usage)."""
    out = gw.chat([{"role": "system", "content": STRUCT_SYSTEM},
                   {"role": "user", "content": structure_message(payload)}],
                  purpose="structure", provider="openai", model=model, timeout=120)
    try:
        from .gateway import strip_fence
        data = json.loads(strip_fence(out["text"]))
        cs = data.get("candidates") if isinstance(data, dict) else None
        return [{**c, "source": "agent"} for c in cs if isinstance(c, dict)] if isinstance(cs, list) else [], out.get("usage")
    except (ValueError, TypeError):
        return [], out.get("usage")


def agent_cost(model, usage):
    """(USD by the price list, tokens in, tokens out) of one agent call; (None, 0, 0) when the reply had no usage."""
    from .autocand import chat_price
    if not usage:
        return None, 0, 0
    tin = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    tout = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    p = chat_price(model) or (0.0, 0.0)
    return round((tin * p[0] + tout * p[1]) / 1_000_000, 6), tin, tout


def _model_values(op):
    if op.get("op") == "set" and op.get("param") == "model":
        return [op.get("value")]
    if op.get("op") == "add_node" and isinstance(op.get("node"), dict):
        m = (op["node"].get("params") or {}).get("model")
        return [m] if m is not None else []
    return []


def check_candidate(c, rel, allowed):
    """(None | '버림 — …', info) for one agent candidate before packrt sees it: op kinds · nodes of this Pack · function nodes · node kinds
    added · set params · models (allowed, not larger than the Pack's largest). info = {shape, ops (graph id set), nodes it touches,
    model_only, sha} — the node set is what ⑤ holds the row's change to (Q-EF-7)."""
    graph = (rel.get("pack") or {}).get("entrypoint") or (rel.get("graph") or {}).get("id")
    nodes = nodes_of(rel)
    ops = [{**o, "graph": graph} if o.get("op") in STRUCT_OPS else dict(o) for o in (c.get("ops") or []) if isinstance(o, dict)]
    shape = STRUCT_SHAPES.get(c.get("shape"), str(c.get("shape") or "구조"))
    model_only = bool(ops) and all(o.get("op") == "set" and o.get("param") == "model" for o in ops)
    touched, added = set(), {o["node"].get("id") for o in ops if o.get("op") == "add_node" and isinstance(o.get("node"), dict)}
    for o in ops:
        e = o.get("edge")
        if isinstance(o.get("node"), str):
            touched.add(o["node"])
        elif isinstance(o.get("node"), dict) and o["node"].get("id"):
            touched.add(o["node"]["id"])
        if isinstance(e, list):
            touched.update(x for x in e if isinstance(x, str))
        elif isinstance(e, dict):
            touched.update(x for x in [e.get("from"), e.get("to"), *(e.get("join") or [])] if isinstance(x, str))
    touched -= {"START", "END"}
    info = {"shape": shape, "ops": ops, "nodes": sorted(touched), "model_only": model_only,
            "sha": __import__("hashlib").sha256(json.dumps(ops, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]}

    def why():
        if not ops:
            return "버림 — 변경 줄 없음"
        for o in ops:
            if o.get("op") not in STRUCT_OPS:
                return f"버림 — 바꿀 수 없는 변경 종류({o.get('op')})"
            if o["op"] in ("set", "remove_node") and not isinstance(o.get("node"), str):
                return f"버림 — 변경 줄 모양 틀림({o['op']}에 node 없음)"
            if o["op"] == "add_node" and not (isinstance(o.get("node"), dict) and o["node"].get("id")):
                return "버림 — 변경 줄 모양 틀림(add_node에 node id 없음)"
            if o["op"] in ("add_edge", "remove_edge") and not isinstance(o.get("edge"), (list, dict)):
                return f"버림 — 변경 줄 모양 틀림({o['op']}에 edge 없음)"
        routes = {tuple(r) for r in _edges(rel.get("graph") or {})[1]}
        for o in ops:
            e = o.get("edge")
            if o["op"] in ("add_edge", "remove_edge") and isinstance(e, list) and tuple(e) in routes:
                return f"버림 — 갈래는 연결이 아님({e[0]} → {e[1]}: router cases로 바꿈)"
        state = pack_state(rel)
        new_keys = sorted({k for o in ops if o["op"] == "add_node" for k in (o["node"].get("reads") or []) + (o["node"].get("writes") or [])} - set(state)) if state else []
        if new_keys:
            return f"버림 — 새 state 키는 못 만듦({', '.join(new_keys)})"
        for n in sorted(touched - added):
            if n not in nodes:
                return f"버림 — 이 Pack에 없는 node {n}"
        for o in ops:
            if o["op"] in ("set", "remove_node") and not _changeable(nodes.get(o.get("node")) or {}):
                return f"버림 — 함수 node는 바꾸지 않음({o.get('node')})"
            if o["op"] == "add_node" and (not isinstance(o.get("node"), dict) or o["node"].get("kind") not in STRUCT_ADDABLE):
                return f"버림 — 더할 수 없는 node 종류({(o.get('node') or {}).get('kind') if isinstance(o.get('node'), dict) else o.get('node')})"
            if o["op"] == "add_node":
                gap = [k for k in STRUCT_REQUIRED.get(o["node"]["kind"], []) if k not in (o["node"].get("params") or {})]
                if gap:
                    return f"버림 — 더한 node 필수 칸 빠짐({o['node']['kind']}: {', '.join(gap)})"
            if o["op"] == "set" and o.get("param") not in STRUCT_SET.get(kind_of(nodes.get(o.get("node")) or {}), ()):
                return f"버림 — 바꿀 수 없는 칸({o.get('node')} · {o.get('param')})"
        top = max((model_rank((n.get("params") or {}).get("model")) for n in nodes.values() if kind_of(n) == "llm"
                   and model_rank((n.get("params") or {}).get("model")) is not None), default=None)
        for m in (m for o in ops for m in _model_values(o)):
            if m not in allowed:
                return ("버림 — Pack 안 가장 큰 모델보다 큼" if top is not None and model_rank(m) is not None and model_rank(m) > top
                        else "버림 — 허용 모델 아님") + f"({m})"
            if top is not None and model_rank(m) is not None and model_rank(m) > top:
                return f"버림 — Pack 안 가장 큰 모델보다 큼({m})"
        return None
    return why(), info
