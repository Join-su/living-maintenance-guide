"""Fixed-flow list of a Pack and the structure check of a candidate (EV-016; llmops-lite-v2 EV-069 PK-1, D-101 ~ D-106).

    res = packrt.ir.fixed_flow(folder)
    res["pack"], res["revision"], res["items"], res["errors"]   # items {"id", "kind", "what", "basis", "value"}, sorted by id;
                                                                # errors = the load problems (then items is empty)
    found = packrt.ir.check_fixed_flow(items, candidate_folder)  # [] = the candidate keeps every item
    found[i]: {"item", "kind", "what", "found", "message"}; a candidate that does not load is one {"item": "load", "kind": "load"}

Reads pack.yaml and the schema / custom files only: no network, no DB, Pack code is never run. Free on purpose (not in the
list, D-103): node order, merging or removing nodes, LLM calls, prompts, middle state, llm provider/model, inner routers.

D-462 ① (packrt 0.13.0): the result-check item is `checks` — per first-router value (request kind), every way to the output keeps
a check that holds the original last check: the rules' signature (a rule's own text; a Pack function rule by its code, see
codesig) is the same or has more rules, and the check stands after the way's last model node (llm · decide) when the original's
did. A check of a way that is gone (a rewrite loop) goes with it. `fixed_flow` lists `checks`; the old `rules` items (lists
confirmed before 0.13.0) are still judged as before, until a person confirms the new list. 0.13.1: a way no model output reaches
(a template-only 'no issue' way) needs no check and does not drop its request kind's requirement; a graph where no request kind
keeps a requirement lists the old `rules` items instead of none (the strict side)."""
import json

from . import codesig, load_pack, semantic
from . import options as _options
from . import revision as _revision

KINDS = ("input", "output", "stores", "models", "evidence", "human", "rules", "checks", "requests", "permission", "side_effects", "options")
MODEL_KINDS = ("llm", "decide")
MAX_WAYS = 5000   # ways to the output followed per graph; more than this and the graph cannot be judged
NETWORK = ("none", "allowlisted")
SIDE_EFFECT = ("none", "dry_run", "approval_required", "allowed")


def _sha(value):
    return "sha256:" + _revision._sha(_revision.canonical_json(value))


def _base(node):
    return str(node.get("kind", "")).split("@")[0]


def _stores_read(node):
    """The stores a node reads: retrieve params.store, or a function node's params.stores (custom_code@3)."""
    p = node.get("params") or {}
    if _base(node) == "retrieve" and isinstance(p.get("store"), str):
        return [p["store"]]
    if _base(node) == "custom_code" and isinstance(p.get("stores"), list):
        return [s for s in p["stores"] if isinstance(s, str)]
    return []


def _path(graph, node_id):
    return f"graphs/{graph}/nodes/{node_id}"


def _state_type(t):
    return t.get("type") if isinstance(t, dict) else t


def _rank(order, value):
    return order.index(value) if value in order else len(order)   # an unknown value counts as above every known one


def _file_sha(folder, rel):
    try:
        return "sha256:" + _revision.file_hash(folder, rel)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _rules_sha(folder, rules):
    custom = {}
    for rule in rules or []:
        ref = rule.get("custom") if isinstance(rule, dict) else None
        if isinstance(ref, str) and ":" in ref:
            rel = ref.split(":")[0].replace(".", "/") + ".py"
            custom[rel] = _file_sha(folder, rel)
    return _sha({"rules": rules, "custom": custom})


def _rule_sig(folder, rule):
    """One rule's signature: 'custom <ref> <code sha>' for a Pack function rule, else 'rule <canonical rule text>'."""
    if isinstance(rule, dict) and set(rule) == {"custom"}:
        return f"custom {rule['custom']} {codesig.function_sig(folder, rule['custom']) or 'unreadable'}"
    return "rule " + _revision.canonical_json(rule).decode("utf-8")


def _sig_label(sig):
    kind, rest = sig.split(" ", 1)
    if kind == "custom":
        return rest.rsplit(" ", 1)[0]
    try:
        value = json.loads(rest)
        return next(iter(value)) if isinstance(value, dict) and value else rest
    except ValueError:
        return rest


class _TooMany(Exception):
    pass


def _ways(gname, graph, out_key, folder):
    """The ways START → END of one graph that pass a node writing the output key (simple ways: no node twice; joins, router
    cases, validate pass / on_fail, review routes, on_error routes and loops as semantic.Graph has them). For each: its class
    (the first router's value, '*' without one), the nodes shown (after the first router up to the output writer), the last
    model node before the writer, and the rule signatures of the checks after it, of all checks and of the last check. A way is
    'free' when no model output can reach its output: no model node on it, the writer is no model node, and the writer reads no
    key a model node's writes carry on to (reads/writes over the whole graph — the strict side). Free ways need no check (0.13.1:
    a template-only 'no issue' way next to the model ways no longer drops the whole request kind's requirement)."""
    G = semantic.Graph(gname, graph)
    carried = set()
    for n in graph.get("nodes") or []:
        if _base(n) in MODEL_KINDS:
            carried |= set(n.get("writes") or [])
    changed = True
    while changed:
        changed = False
        for n in graph.get("nodes") or []:
            if set(n.get("reads") or []) & carried and not set(n.get("writes") or []) <= carried:
                carried |= set(n.get("writes") or [])
                changed = True
    first = next((b for b, _ in G.succ.get(semantic.START, []) if _base(G.nodes.get(b) or {}) == "router"), None)
    sigs = {}

    def node_sigs(nid):
        if nid not in sigs:
            sigs[nid] = frozenset(_rule_sig(folder, r) for r in ((G.nodes[nid].get("params") or {}).get("rules") or []))
        return sigs[nid]

    out, steps = [], [0]

    def record(path, cls):
        writes = [i for i, nid in enumerate(path) if out_key in (G.nodes[nid].get("writes") or [])]
        if not writes:
            return
        w = writes[-1]
        kinds = [_base(G.nodes[nid]) for nid in path[:w]]
        models = [i for i, k in enumerate(kinds) if k in MODEL_KINDS]
        checks = [i for i, k in enumerate(kinds) if k == "validate"]
        after = [i for i in checks if not models or i > models[-1]]
        shown = path[path.index(first) + 1 if first in path else 0:w + 1]
        writer = G.nodes[path[w]]
        free = not models and _base(writer) not in MODEL_KINDS and not set(writer.get("reads") or []) & carried
        out.append({"class": cls, "nodes": shown, "model": path[models[-1]] if models else None, "free": free,
                    "after": frozenset().union(*[node_sigs(path[i]) for i in after]),
                    "any": frozenset().union(*[node_sigs(path[i]) for i in checks]),
                    "last": path[checks[-1]] if checks else None, "last_after": bool(checks) and checks[-1] in after,
                    "last_sigs": node_sigs(path[checks[-1]]) if checks else frozenset()})
        if len(out) > MAX_WAYS:
            raise _TooMany()

    def go(node, path, cls, seen):
        steps[0] += 1
        if steps[0] > 50 * MAX_WAYS:
            raise _TooMany()
        for b, how in G.succ.get(node, []):
            c = cls
            if node == first and node in path and cls is None:
                c = how[1] if how[0] == "case" else "default"
            if b == semantic.END:
                record(path, c if c is not None else "*")
            elif b not in seen and b in G.nodes:
                go(b, path + [b], c, seen | {b})

    go(semantic.START, [], None, {semantic.START})
    return out


def _facts(doc, folder):
    """What the list and the check compare, from a loaded Pack (defaults applied). Only the entry graphs count (the runtime runs
    the entry graph of an execution config, never a graph no entrypoint names), and each fact keeps the graphs it is in, so a
    candidate must keep it in every one of them (critic E3-A1 B1 · B2). Per graph the first entrypoint counts, and the output
    key is the first state key whose type is the output — as the engine picks them."""
    doc = _revision.apply_defaults(doc)
    graphs = doc.get("graphs") or {}
    schemas = doc.get("schemas") or {}
    f = {"input": {}, "output": {}, "stores": {}, "models": {}, "evidence": {}, "human": {}, "rules": {}, "requests": {},
         "network": "none", "side_effect": "none", "env": set(), "tools": set(), "checks": {}, "options": {}}
    for i, ep in enumerate(doc.get("entrypoints") or []):
        g = ep["graph"]
        for side in ("input", "output"):
            if g not in f[side]:
                f[side][g] = {"schema": ep[side], "sha": _file_sha(folder, schemas[ep[side]]) if ep[side] in schemas else None,
                              "basis": f"entrypoints/{i}/{side}"}
    entry = sorted(g for g in f["output"] if g in graphs)
    for g in entry:
        for n in graphs[g].get("nodes") or []:
            p, nid = n.get("params") or {}, n["id"]
            for name in _stores_read(n):
                s = f["stores"].setdefault(name, {"def_sha": _sha((doc.get("stores") or {}).get(name)), "nodes": [], "graphs": set()})
                s["nodes"].append(_path(g, nid))
                s["graphs"].add(g)
            if _base(n) == "predict" and p.get("model"):
                m = f["models"].setdefault(p["model"], {"nodes": [], "graphs": set()})
                m["nodes"].append(_path(g, nid))
                m["graphs"].add(g)
            elif _base(n) == "human_review":
                h = f["human"].setdefault(nid, {"basis": _path(g, nid), "graphs": {}})
                h["graphs"].setdefault(g, _sha(p))
            elif _base(n) == "custom_code" and isinstance(p.get("options"), dict):   # 0.15.0 (D-476): the declaration, not the values
                f["options"].setdefault(g, {})[nid] = _options.declaration(p)
            elif _base(n) == "validate":
                r = f["rules"].setdefault(_rules_sha(folder, p.get("rules")), {"basis": _path(g, nid), "graphs": set()})
                r["graphs"].add(g)
            caps = n.get("capabilities") or {}
            if _rank(NETWORK, caps.get("network", "none")) > _rank(NETWORK, f["network"]):
                f["network"] = caps.get("network")
            if _rank(SIDE_EFFECT, caps.get("side_effect", "none")) > _rank(SIDE_EFFECT, f["side_effect"]):
                f["side_effect"] = caps.get("side_effect")
    for tool in (doc.get("tools") or {}).values():
        caps = (tool or {}).get("capabilities") or {}
        if _rank(NETWORK, caps.get("network", "none")) > _rank(NETWORK, f["network"]):
            f["network"] = caps.get("network")
        if _rank(SIDE_EFFECT, caps.get("side_effect", "none")) > _rank(SIDE_EFFECT, f["side_effect"]):
            f["side_effect"] = caps.get("side_effect")
    env = doc.get("env") or {}
    f["env"] = set(env.get("required") or []) | set(env.get("optional") or [])
    f["tools"] = set(doc.get("tools") or {})
    for g in entry:
        graph = graphs[g]
        nodes = graph.get("nodes") or []
        out_key = next((k for k, t in (graph.get("state") or {}).items() if _state_type(t) == f["output"][g]["schema"]), None)
        f.setdefault("nodes", {})[g] = nodes
        by_id = {n["id"]: n for n in nodes}
        for store in sorted({s for n in nodes for s in _stores_read(n)}):
            carried = set()
            for n in nodes:
                if store in _stores_read(n):
                    carried |= set(n.get("writes") or [])
            changed = True
            while changed:   # a key read with a carried key carries the store on (reads/writes only)
                changed = False
                for n in nodes:
                    if set(n.get("reads") or []) & carried and not set(n.get("writes") or []) <= carried:
                        carried |= set(n.get("writes") or [])
                        changed = True
            if out_key is not None and out_key in carried:
                f["evidence"].setdefault(store, {})[g] = out_key
        try:
            f["checks"][g] = {"ways": _ways(g, graph, out_key, folder) if out_key is not None else [], "too_many": False}
        except _TooMany:
            f["checks"][g] = {"ways": [], "too_many": True}
        for e in graph.get("edges") or []:
            if isinstance(e, list) and len(e) == 2 and e[0] == "START" and _base(by_id.get(e[1]) or {}) == "router":
                p = by_id[e[1]].get("params") or {}
                f["requests"][g] = {"router": e[1], "values": sorted(set(p.get("cases") or {}) | {"default"})}
                break
    return f


def _items(f):
    items = []

    def add(iid, kind, what, basis, value):
        items.append({"id": iid, "kind": kind, "what": what, "basis": basis, "value": value})

    for side, word in (("input", "입력"), ("output", "출력")):
        if f[side]:
            names = " · ".join(sorted({v["schema"] for v in f[side].values()}))
            add(side, side, f"{word} 형식은 그대로({names})", " · ".join(f[side][g]["basis"] for g in sorted(f[side])),
                {g: {"schema": v["schema"], "sha": v["sha"]} for g, v in f[side].items()})
    for s, v in f["stores"].items():
        add(f"stores:{s}", "stores", f"증명된 데이터 {s}는 반드시 씀 — 읽는 node · 때는 바뀌어도 됨", " · ".join(v["nodes"]),
            {"store": s, "def_sha": v["def_sha"], "graphs": sorted(v["graphs"])})
    for m, v in f["models"].items():
        add(f"models:{m}", "models", f"예측 모델 {m}는 같은 이름으로 씀", " · ".join(v["nodes"]), {"model": m, "graphs": sorted(v["graphs"])})
    for s, to in f["evidence"].items():
        outs = " · ".join(sorted({f["output"][g]["schema"] for g in to}))
        first = {g: next(n for n in f["stores"][s]["nodes"] if n.startswith(f"graphs/{g}/")) for g in to}
        basis = " · ".join(f"{first[g]} → graphs/{g}/state/{k}" for g, k in sorted(to.items()))
        add(f"evidence:{s}", "evidence", f"데이터 {s}가 출력({outs})까지 이어짐(근거 연결)", basis, {"store": s, "to": dict(sorted(to.items()))})
    for nid, v in f["human"].items():
        add(f"human:{nid}", "human", f"사람 관문 {nid}는 그대로", v["basis"], {"node": nid, "graphs": dict(sorted(v["graphs"].items()))})
    def old_rules(g):   # every check node of the graph stays, as before 0.13.0 (the strict side)
        for n, (sha, v) in enumerate(((s, v) for s, v in f["rules"].items() if g in v["graphs"]), 1):
            add(f"rules:{g}:{n}", "rules", f"결과 검사 규칙 {n}({v['basis'].rsplit('/', 1)[-1]})이 남음 — node 이름은 바뀌어도 됨",
                v["basis"], {"rules_sha": sha, "graphs": sorted(v["graphs"])})

    for g, c in sorted(f["checks"].items()):
        if c["too_many"]:   # a graph whose ways cannot be followed keeps the old item
            old_rules(g)
            continue
        classes, last = {}, set()
        by_class = {}
        for w in c["ways"]:
            if not w["free"]:   # a way no model output reaches needs no check (0.13.1)
                by_class.setdefault(w["class"], []).append(w)
        for cls, ways in sorted(by_class.items()):
            if not all(w["last"] for w in ways):
                continue   # a way to the output without a check in the original: nothing to keep for this request kind
            mode = "after_model" if all(w["last_after"] for w in ways) else "any"
            classes[cls] = {"mode": mode, "need": sorted({tuple(sorted(w["last_sigs"])) for w in ways})}
            last |= {w["last"] for w in ways}
        if classes:
            order = [n["id"] for n in f["nodes"][g]]
            names = sorted(last, key=order.index)
            add(f"checks:{g}", "checks",
                f"출력에 닿는 길마다 원래 마지막 검사({' · '.join(names)})와 같거나 더 엄한 검사가 있음 — node 이름 · 다시 쓰기 길은 바뀌어도 됨",
                " · ".join(_path(g, n) for n in names),
                {"graph": g, "classes": {k: {"mode": v["mode"], "need": [list(x) for x in v["need"]]} for k, v in classes.items()}})
        else:   # no request kind keeps a requirement (0.13.1): the old items, never nothing
            old_rules(g)
    if f["requests"]:
        values = sorted({x for v in f["requests"].values() for x in v["values"]})
        add("requests", "requests", f"받는 요청 종류({' · '.join(values)})는 줄지 않음",
            " · ".join(_path(g, v["router"]) for g, v in sorted(f["requests"].items())),
            {g: dict(v) for g, v in f["requests"].items()})
    for g, nodes in sorted(f["options"].items()):
        add(f"options:{g}", "options", f"함수 node 옵션 선언({' · '.join(sorted(nodes))} — 이름 · 형 · 한도 · 느슨한 쪽)은 그대로 — 값은 바뀌어도 됨",
            " · ".join(f"{_path(g, nid)}/params/options" for nid in sorted(nodes)), {"graph": g, "nodes": dict(sorted(nodes.items()))})
    add("permission", "permission", f"권한(network {f['network']} · side_effect {f['side_effect']})은 올라가지 않음",
        "graphs/*/nodes/*/capabilities · tools/*/capabilities", {"network": f["network"], "side_effect": f["side_effect"]})
    env, tools = sorted(f["env"]), sorted(f["tools"])
    add("side_effects", "side_effects",
        f"부작용 바탕(env {' · '.join(env) or '없음'} · tools {' · '.join(tools) or '없음'})은 늘지 않음", "env · tools",
        {"env": env, "tools": tools})
    return sorted(items, key=lambda i: i["id"])


def _pack_of(doc):
    p = doc.get("pack") if isinstance(doc, dict) else None
    if isinstance(p, dict) and isinstance(p.get("id"), str) and isinstance(p.get("version"), str):
        return {"id": p["id"], "version": p["version"]}
    return None


def fixed_flow(folder):
    """The fixed-flow list of the Pack in folder (D-102 · D-103): what an efficiency candidate must keep."""
    res = load_pack(folder)
    if not res.ok:
        return {"pack": _pack_of(res.doc), "revision": None, "items": [], "errors": list(res.errors)}
    return {"pack": _pack_of(res.doc), "revision": res.revision, "items": _items(_facts(res.doc, res.folder)), "errors": []}


def _missing(want, got):
    """The graphs of want (a list, or a dict keyed by graph) that got lacks, as one ' · ' line ('' when none)."""
    return " · ".join(sorted(g for g in want if g not in got))


def _found(item, f):
    """One line of what the candidate does instead, or None when it keeps the item."""
    kind, v = item["kind"], item["value"]
    if kind in ("input", "output"):
        word = "입력" if kind == "input" else "출력"
        for g, want in sorted(v.items()):
            got = f[kind].get(g)
            if got is None:
                return f"{g}의 {word}이 없음"
            if got["schema"] != want["schema"]:
                return f"{g}의 {word} 형식이 {got['schema']}"
            if got["sha"] != want["sha"]:
                return f"{g}의 {word} 형식 {want['schema']} 내용이 다름"
        return None
    if kind == "stores":
        got = f["stores"].get(v["store"])
        if got is None:
            return f"데이터 {v['store']}를 읽는 node가 없음"
        if got["def_sha"] != v["def_sha"]:
            return f"데이터 {v['store']}의 정의가 다름"
        miss = _missing(v["graphs"], got["graphs"])
        return f"데이터 {v['store']}를 읽는 node가 {miss}에 없음" if miss else None
    if kind == "models":
        got = f["models"].get(v["model"])
        if got is None:
            return f"예측 모델 {v['model']}를 쓰는 node가 없음(지금 {' · '.join(sorted(f['models'])) or '없음'})"
        miss = _missing(v["graphs"], got["graphs"])
        return f"예측 모델 {v['model']}를 쓰는 node가 {miss}에 없음" if miss else None
    if kind == "evidence":
        miss = sorted(g for g in v["to"] if g not in f["evidence"].get(v["store"], {}))
        return f"데이터 {v['store']}가 {' · '.join(miss)}의 출력에 닿지 않음" if miss else None
    if kind == "human":
        got = f["human"].get(v["node"])
        if got is None:
            return f"사람 관문 {v['node']}가 없음"
        miss = _missing(v["graphs"], got["graphs"])
        if miss:
            return f"사람 관문 {v['node']}가 {miss}에 없음"
        diff = sorted(g for g, sha in v["graphs"].items() if got["graphs"][g] != sha)
        return f"사람 관문 {v['node']}의 설정이 {' · '.join(diff)}에서 다름" if diff else None
    if kind == "rules":
        got = f["rules"].get(v["rules_sha"])
        if got is None:
            return "같은 규칙을 쓰는 결과 검사 node가 없음"
        miss = _missing(v["graphs"], got["graphs"])
        return f"같은 규칙을 쓰는 결과 검사 node가 {miss}에 없음" if miss else None
    if kind == "checks":
        got = f["checks"].get(v["graph"])
        if got is None:
            return f"{v['graph']} 그래프가 없음"
        if got["too_many"]:
            return f"{v['graph']}의 출력에 닿는 길이 너무 많아(> {MAX_WAYS}) 볼 수 없음"
        for cls, req in sorted(v["classes"].items()):
            needs = [set(n) for n in req["need"]]
            for w in got["ways"]:
                if w["class"] != cls or w["free"]:
                    continue
                have = w["after"] if req["mode"] == "after_model" else w["any"]
                if any(n <= have for n in needs):
                    continue
                way = f"{cls} 길 {' → '.join(w['nodes'])}"
                if not have:
                    if req["mode"] == "after_model" and w["model"]:
                        return f"{way}: 모델 node {w['model']} 뒤 검사 없이 출력"
                    return f"{way}: 검사 없이 출력"
                near = min(needs, key=lambda n: (len(n - have), sorted(n)))
                parts = []
                for sig in sorted(near - have):
                    label = _sig_label(sig)
                    same = sig.startswith("custom ") and any(h.startswith("custom ") and _sig_label(h) == label for h in have)
                    parts.append(f"함수 {label} 몸이 다름" if same else f"규칙 {label} 빠짐")
                return f"{way}: 원래 마지막 검사와 같거나 더 엄한 검사가 없음 — {' · '.join(parts)}"
        return None
    if kind == "requests":
        for g, want in sorted(v.items()):
            got = f["requests"].get(g)
            if got is None:
                return f"{g}의 첫 갈래(router)가 없음"
            miss = sorted(set(want["values"]) - set(got["values"]))
            if miss:
                return f"{g}의 첫 갈래가 받지 않는 값: {' · '.join(miss)}"
        return None
    if kind == "permission":
        up = []
        if _rank(NETWORK, f["network"]) > _rank(NETWORK, v["network"]):
            up.append(f"network {f['network']}")
        if _rank(SIDE_EFFECT, f["side_effect"]) > _rank(SIDE_EFFECT, v["side_effect"]):
            up.append(f"side_effect {f['side_effect']}")
        return f"권한이 올라감: {' · '.join(up)}" if up else None
    if kind == "side_effects":
        new = [f"env {x}" for x in sorted(f["env"] - set(v["env"]))] + [f"tools {x}" for x in sorted(f["tools"] - set(v["tools"]))]
        return f"새 이름: {' · '.join(new)}" if new else None
    if kind == "options":
        got = f["options"].get(v["graph"], {})
        for nid, want in sorted(v["nodes"].items()):
            if nid not in got:
                return f"{v['graph']}의 함수 node {nid}에 옵션 선언이 없음"
            if got[nid] != want:
                diff = sorted(k for k in set(want) | set(got[nid]) if want.get(k) != got[nid].get(k))
                return f"{v['graph']}의 함수 node {nid} 옵션 선언이 다름: {' · '.join(diff)}"
        return None
    raise KeyError(kind)


def _violation(item_id, kind, what, found):
    return {"item": item_id, "kind": kind, "what": what, "found": found, "message": f"고정 흐름 '{what}'을 어김 — 후보에서는 {found}"}


def check_fixed_flow(items, folder):
    """Check a candidate Pack against a fixed-flow list (D-103): [] when it keeps every item, else one violation per item
    broken, in list order. The items are only read. A candidate that does not load is one 'load' violation; an item this
    function does not know is a violation too (nothing is passed unchecked)."""
    res = load_pack(folder)
    if not res.ok:
        first = res.errors[0]["message"] if res.errors else "알 수 없는 문제"
        return [{"item": "load", "kind": "load", "what": "후보 Pack을 불러옴", "found": first,
                 "message": f"후보 Pack을 불러오지 못함 — {first}"}]
    f = _facts(res.doc, res.folder)
    out = []
    for item in items:
        if not isinstance(item, dict):
            out.append(_violation("?", "?", "?", "항목 모양이 다름"))
            continue
        iid, kind, what = str(item.get("id", "?")), str(item.get("kind", "?")), str(item.get("what", "?"))
        if kind not in KINDS:
            out.append(_violation(iid, kind, what, "이 검사가 모르는 항목이라 지켰는지 볼 수 없음"))
            continue
        try:
            found = _found(item, f)
        except (KeyError, TypeError, AttributeError, ValueError):
            found = "항목 모양이 달라 지켰는지 볼 수 없음"
        if found is not None:
            out.append(_violation(iid, kind, what, found))
    return out

