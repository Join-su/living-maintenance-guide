"""Flow checks of a patched Pack (EV-017, option table v1.0 section 6.6). Run by `packrt.ir.patch` after stages 0-2; loading a
Pack for registration (`load_pack`) does not call them.

- F1 reads flow: on every path from START to a node, each key the node reads was written before it (input keys exist from
  the start). A `join` runs after all its sources, so it counts the keys any of them wrote. Loop edges are followed.
- F2 template keys: `{state.key...}` in an llm prompt file or a render template file, and `{state.key...}` / `state.key`
  references in params, name keys the node reads.
- F3 function node position: a function node (custom_code) that breaks F1 gets its own message — it can be moved, but the
  new place must still give it what it reads.
Each problem is (level, path, message), like stage 2."""
from pathlib import Path

from . import semantic


def check(doc, folder):
    out = []
    folder = Path(folder)
    for gname, g in (doc.get("graphs") or {}).items():
        out += _graph(doc, folder, gname, g)
    return out


def _graph(doc, folder, gname, g):
    out = []
    err = lambda path, msg: out.append(("error", path, msg))
    G = semantic.Graph(gname, g)
    base = ("graphs", gname)
    idx = {n["id"]: i for i, n in enumerate(g.get("nodes", []))}
    state = g.get("state") or {}
    input_types = {ep.get("input") for ep in doc.get("entrypoints") or [] if ep.get("graph") == gname}
    initial = {k for k, t in state.items() if semantic._state_type(t) in input_types}
    before = written_before(G, g, initial)
    for nid, n in G.nodes.items():
        kind, _ = semantic.kind_of(n)
        path = base + ("nodes", idx[nid])
        reads = n.get("reads", [])
        # F1 / F3
        for j, key in enumerate(reads):
            if key in state and key not in before.get(nid, set()):
                if kind == "custom_code":
                    err(path + ("reads", j), f"함수 node '{nid}'는 이 자리에 둘 수 없습니다 — reads '{key}'를 앞에서 쓰지 않는 경로가 있습니다"
                                             "(연결 위치만 바꿀 수 있고, 옮긴 자리에서도 reads가 채워져야 합니다)")
                else:
                    err(path + ("reads", j), f"node '{nid}'에 닿는 경로 중 reads '{key}'를 앞에서 쓰지 않는 경로가 있습니다")
        # F2
        p = n.get("params") or {}
        for param in ("prompt", "template"):
            if (kind, param) in (("llm", "prompt"), ("render", "template")) and isinstance(p.get(param), str):
                f = folder / p[param]
                if f.is_file():
                    refs = set(semantic.STATE_REF.findall(f.read_text(encoding="utf-8")))
                    for key in sorted(refs - set(reads)):
                        err(path + ("params", param), f"node '{nid}'의 {p[param]}가 reads에 없는 '{key}'를 씁니다({{state.{key}…}})")
        for key in sorted(semantic._state_refs(p) - set(reads)):
            err(path + ("params",), f"node '{nid}'의 params가 reads에 없는 '{key}'를 가리킵니다")
    return out


def written_before(G, g, initial):
    """State keys certainly written on every path from START to each node, before the node runs. Like the stage 2 check of the
    review subject, but a join edge counts as one way in whose keys are the union of all its sources."""
    allkeys = set(initial)
    for n in G.nodes.values():
        allkeys |= set(n.get("writes", []))
    ways = {}   # node -> list of ways in; a way is a tuple of source nodes whose keys are all available (a join) or one node
    for a, b, how in G.targets():
        if how[0] != "join":
            ways.setdefault(b, []).append((a,))
    for e in g.get("edges", []):
        if isinstance(e, dict) and "join" in e:
            ways.setdefault(e["to"], []).append(tuple(e["join"]))
    before = {nid: set(allkeys) for nid in G.nodes}

    def after(x):
        if x == semantic.START:
            return set(initial)
        if x not in G.nodes:
            return set()
        return before[x] | set(G.nodes[x].get("writes", []))

    changed = True
    while changed:
        changed = False
        for nid in G.nodes:
            ins = [set().union(*[after(s) for s in way]) for way in ways.get(nid, [])]
            new = set.intersection(*ins) if ins else set()
            if new != before[nid]:
                before[nid] = new
                changed = True
    return before
