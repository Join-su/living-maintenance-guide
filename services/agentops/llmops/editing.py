"""Edits of an Agent Pack release (EV-022 CHG-108 · 109 · A-11; S-1 · S-2 · S-8 · S-13 · S-14; manual p.8 · 9 · 10 · 20 · 21 ·
22 · 34 · 43). An edit is the starting release plus a list of change lines, kept by LLMOps (`release_edit`, one open edit per
Project); the Pack work folder and the release copy are never written. Every change goes through packrt `apply_patch` on the
whole list, so a line is kept only when the whole edit still passes stages 0-2 and the flow checks. A line is one packrt
operation, or the value edits of one node merged into one line (manual p.10: the same node edited twice is one item); each
request is one group. Nothing here runs a Pack.

Functions are pure over the line list plus the patch call; the store and the HTTP layer live in store.py and app.py."""
import copy as _copy
import re as _re_mod
import shutil
import tempfile
from pathlib import Path

from packrt.ir.patch import apply_patch


VALUE_OPS = {"set", "unset", "add_rule", "remove_rule"}
PARAM_LABEL = {"top_k": "가져올 개수", "search": "찾는 방식", "fallback": "부족할 때", "prompt": "프롬프트", "model": "모델", "provider": "provider",
               "temperature": "temperature", "max_tokens": "max_tokens", "cases": "갈 조건", "default": "기본 갈 곳", "template": "템플릿",
               "on_fail": "실패할 때", "routes": "검토 뒤 갈 곳", "timeout": "시간 제한", "query": "질의 문장", "filter": "조건",
               "score_threshold": "점수 하한", "where": "조건", "select": "고를 칸", "limit": "개수"}


def edge_ends(edge):
    """(sources, target) of a pack.yaml edges item: [a, b], {join: [...], to}, {from, to, max_iterations}."""
    if isinstance(edge, list) and len(edge) == 2:
        return [edge[0]], edge[1]
    if isinstance(edge, dict) and "join" in edge:
        return list(edge.get("join") or []), edge.get("to")
    if isinstance(edge, dict):
        return [edge.get("from")], edge.get("to")
    return [], None


def describe(op):
    """kind, sign, node, short Korean summary of one op (the change-list card, manual p.9 · 20)."""
    name = op.get("op")
    if name in VALUE_OPS:
        param = op.get("param") or ("rules" if "rule" in name else "")
        label = "규칙 값" if param.startswith("rules") or "rule" in name else PARAM_LABEL.get(param, param)
        return "value", "✎", op.get("node"), label
    if name in ("add_node", "remove_node"):
        nid = op["node"].get("id") if isinstance(op.get("node"), dict) else op.get("node")
        kind = op["node"].get("kind") if isinstance(op.get("node"), dict) else ""
        return "node", "＋" if name == "add_node" else "−", nid, f"node {nid}" + (f" · {kind}" if kind else "")
    if name in ("add_edge", "remove_edge"):
        src, dst = edge_ends(op.get("edge"))
        return "edge", "＋" if name == "add_edge" else "−", None, f"연결 {' · '.join(map(str, src))} → {dst}"
    if name in ("write_file", "create_file"):
        return "file", "＋" if name == "create_file" else "✎", None, f"파일 {op.get('path')}"
    return "other", "✎", None, str(name)


def touches(op):
    """Node ids and file paths an op uses (for 'reverting this line needs reverting that one', manual p.34 #15)."""
    name = op.get("op")
    if name in VALUE_OPS or name == "remove_node":
        out = {op.get("node")}
        if name == "set" and op.get("param") == "prompt" and isinstance(op.get("value"), str):
            out.add(op["value"])
        return out
    if name == "add_node":
        if not isinstance(op.get("node"), dict):
            return set()
        prompt = (op["node"].get("params") or {}).get("prompt")   # a new node using a file made by create_file (E-S-11)
        return {op["node"].get("id")} | ({prompt} if isinstance(prompt, str) else set())
    if name in ("add_edge", "remove_edge"):
        src, dst = edge_ends(op.get("edge"))
        return set(src) | {dst}
    if name in ("write_file", "create_file"):
        return {op.get("path")}
    return set()


def creates(op):
    if op.get("op") == "add_node" and isinstance(op.get("node"), dict):
        return {op["node"].get("id")}
    if op.get("op") == "create_file":
        return {op.get("path")}
    return set()


def all_ops(lines):
    return [o for ln in lines for o in ln["ops"]]


SUB_LABEL = {"min_items": "최소 근거 수", "min_score": "최소 매뉴얼 점수", "require_source_types": "출처", "min_results": "최소", "relax": "다시 찾을 때 풀 조건",
             "chunk_size": "청크 크기", "overlap": "오버랩", "pool": "재정렬 개수"}   # EV-051 CHG-W2-14: a retrieve@2 node's index · D-418: rerank.pool
VALUE_WORD = {"manual": "매뉴얼", "history": "정비 이력", "failure_type": "고장 유형"}


def _word(x):
    return VALUE_WORD.get(x, str(x)) if isinstance(x, str) else str(x)


def base_value(doc, graph, node, param):
    """The starting release's value of a node param ('top_k' or 'rules[0].evidence'), or None (manual p.9 card: what changed)."""
    import re
    try:
        n = next(x for x in doc["graphs"][graph]["nodes"] if x.get("id") == node)
    except (KeyError, StopIteration, TypeError):
        return None
    params = n.get("params") or {}
    m = re.fullmatch(r"rules\[(\d+)\]\.(\w+)", str(param or ""))
    if m:
        rules = params.get("rules") or []
        i = int(m.group(1))
        return (rules[i] or {}).get(m.group(2)) if i < len(rules) and isinstance(rules[i], dict) else None
    return params.get(param)


def _two(x):
    """A value as the change line shows it: a float with more than two decimals to two (EV-043 A3-N4 — the stored value stays as it is,
    e.g. rerank weights 1 − 0.7 = 0.30000000000000004 reads 0.3); every other value unchanged."""
    if isinstance(x, float) and round(x, 2) != x:
        return round(x, 2)
    if isinstance(x, dict):
        return {k: _two(v) for k, v in x.items()}
    return x


def _value_detail(op, before):
    """'최소 매뉴얼 점수 0.4 · 출처 + 정비 이력' / '가져올 개수 4 → 5' — the values a 'set' changed against the starting release."""
    if op.get("op") != "set" or before is None:
        return []
    old, new = before(op.get("node"), op.get("param")), op.get("value")
    if isinstance(new, dict):
        old = old if isinstance(old, dict) else {}
        out = []
        order = list(SUB_LABEL)
        for k, v in sorted(new.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order)):   # the form's order
            lab, o = SUB_LABEL.get(k, k), old.get(k)
            if v == o:
                continue
            if isinstance(v, list) and isinstance(o, list):
                out += [f"{lab} + {_word(x)}" for x in v if x not in o] + [f"{lab} − {_word(x)}" for x in o if x not in v]
            else:
                out.append(f"{lab} {_two(o)} → {_two(v)}" if o is not None else f"{lab} {', '.join(map(_word, v)) if isinstance(v, list) else _two(v)}")
        out += [f"{SUB_LABEL.get(k, k)} 지움" for k in old if k not in new]
        return out
    if old is None:   # a param the starting release does not have (e.g. a node this edit made): the value alone
        return [f"{', '.join(map(_word, new)) if isinstance(new, list) else _two(new)}"]
    return [] if old == new else [f"{_two(old)} → {_two(new)}"]


def _summary(ops, before=None):
    kind, sign, node, _ = describe(ops[0])
    if kind != "value":
        return describe(ops[0])[3]
    labels = []
    for o in ops:
        lab = describe(o)[3]
        detail = _value_detail(o, before)
        if detail and len(detail) == 1 and not detail[0].startswith(tuple(SUB_LABEL.values())):
            lab = f"{lab} {detail[0]}"   # '가져올 개수 4 → 5'
        elif detail:
            lab = " · ".join([lab, *detail])
        if lab not in labels:
            labels.append(lab)
    return " · ".join(labels)


def add(lines, ops, source="person", before=None, fills=None, ai=None):
    """The line list after one request (one group). Value edits of a node already in the list join that node's line; a 'set'
    of the same param replaces the earlier one. Returns a new list (the input is not changed). `before(node, param)` gives the
    starting release's value, so the card names what changed (manual p.9 · 10). EV-073: `fills` = a blank node this request
    gives a kind (its line goes, the request's own add_node · connections take its place); `ai` = the AI suggestion's model ·
    prompt hash · mode, kept on every line the request makes."""
    out = _copy.deepcopy(lines)
    if fills:
        out = [ln for ln in out if not (ln.get("kind") == BLANK and ln["node"] == fills)]
    group = max([ln["group"] for ln in out], default=0) + 1
    nxt = max([ln["n"] for ln in out], default=0) + 1
    for op in ops:
        # EV-073 CHG-AI-1 (D-443): a blank node is a line with no packrt op (packrt adds only ADDABLE kinds) — removing it or
        # changing its role text changes that line only
        mine = {ln["node"]: ln for ln in out if ln.get("kind") == BLANK}
        if op.get("op") == "add_blank":
            b = blank_of(op)
            out.append({"n": nxt, "group": group, "ops": [], "kind": BLANK, "sign": "＋", "node": b["id"], "summary": BLANK_LABEL,
                        "label": BLANK_LABEL, "source": source, "blank": b, **({"ai": ai} if ai else {})})
            nxt += 1
            continue
        if op.get("op") == "remove_node" and op.get("node") in mine:
            out.remove(mine[op["node"]])
            continue
        if op.get("op") == "set" and op.get("node") in mine and op.get("param") == "expect":
            text = (op.get("value") or {}).get("text") if isinstance(op.get("value"), dict) else op.get("value")
            mine[op["node"]]["blank"]["expect"] = {"text": str(text or "")}
            continue
        # manual p.17 ('변경 목록에서 이 node의 3줄이 사라짐(만들고 지운 것)'): removing a connection or a node this edit made takes the
        # line that made it away instead of adding a removal line; a new node's value lines go with it
        if op.get("op") == "remove_edge":
            made = next((ln for ln in out if len(ln["ops"]) == 1 and ln["ops"][0].get("op") == "add_edge" and ln["ops"][0].get("edge") == op.get("edge")), None)
            if made is not None:
                out.remove(made)
                continue
        # manual p.13 ③: the values a node made in this edit reads are chosen on its popover — packrt has no op for a node's reads,
        # so LLMOps changes the reads of the add_node line that made it (the stored edit stays a list of packrt ops)
        if op.get("op") == "set" and op.get("param") == "reads" and isinstance(op.get("value"), list):
            made = next((o for ln in out for o in ln["ops"] if o.get("op") == "add_node" and isinstance(o.get("node"), dict) and o["node"].get("id") == op.get("node")), None)
            if made is not None:
                made["node"]["reads"] = [str(k) for k in op["value"]]
                continue
        # E-S-09 (manual p.14 '이름은 제안 · 바꿀 수 있음', p.32 #2): a node made in this edit gets another name on its popover —
        # LLMOps renames it in every line of this edit (its add_node, its connections, the router cases and the value lines that
        # name it); packrt checks the result like any other edit (a name used twice or not allowed is refused there too)
        if op.get("op") == "set" and op.get("param") == "id" and isinstance(op.get("value"), str):
            old, new = op.get("node"), op["value"]
            if old in {nid for ln in out for o in ln["ops"] for nid in creates(o)}:
                out = _renamed(out, old, new, before)
                continue
        if op.get("op") == "add_edge":   # putting back a connection this edit removed (a join widened, then its node deleted)
            undone = next((ln for ln in out if len(ln["ops"]) == 1 and ln["ops"][0].get("op") == "remove_edge" and ln["ops"][0].get("edge") == op.get("edge")), None)
            if undone is not None:
                out.remove(undone)
                continue
        if op.get("op") == "remove_node" and op.get("node") in {nid for ln in out for o in ln["ops"] for nid in creates(o)}:
            out = [ln for ln in out if not any(o.get("op") == "add_node" and isinstance(o.get("node"), dict) and o["node"].get("id") == op["node"] for o in ln["ops"])
                   and not (ln["kind"] == "value" and ln["node"] == op["node"])]
            continue
        kind, sign, node, _ = describe(op)
        if kind == "value":
            same = next((ln for ln in out if ln["kind"] == "value" and ln["node"] == node), None)
            if same is not None:
                if op.get("op") == "set":
                    same["ops"] = [o for o in same["ops"] if not (o.get("op") == "set" and o.get("param") == op.get("param"))]
                same["ops"].append(_copy.deepcopy(op))
                same["summary"] = _summary(same["ops"], before)
                same["label"] = " · ".join(dict.fromkeys(describe(o)[3] for o in same["ops"]))   # review N-b: the change view's kind cell
                continue
        out.append({"n": nxt, "group": group, "ops": [_copy.deepcopy(op)], "kind": kind, "sign": sign, "node": node,
                    "summary": _summary([op], before), "label": describe(op)[3], "source": source, **({"ai": ai} if ai else {})})
        nxt += 1
    return out


# ---------------------------------------------------------------- EV-073 blank nodes (D-443, manual p.35 · 36)
BLANK = "blank"
BLANK_LABEL = "빈 node"
BLANK_MSG = "빈 node '{}' — 종류 미정"
_ID = _re_mod.compile(r"^[a-z][a-z0-9_]{0,63}$")


def blank_of(op):
    """The blank part of an add_blank op: id, role text (expect), where it hangs — `from` (nodes before it), `to` (nodes after),
    `split` (it sits on the from → to connection, which stays until it gets a kind), `route` ({router, key} when made from a
    router's branch), `graph`. Raises ValueError for a bad id."""
    node = op.get("node") or {}
    nid = node.get("id")
    if not isinstance(nid, str) or not _ID.match(nid):
        raise ValueError(f"node 이름이 맞지 않습니다: {nid!r}")
    text = (node.get("expect") or {}).get("text") if isinstance(node.get("expect"), dict) else ""
    route = op.get("route") if isinstance(op.get("route"), dict) else None
    return {"id": nid, "graph": op.get("graph"), "expect": {"text": str(text or "")}, "from": [str(x) for x in op.get("from") or []],
            "to": [str(x) for x in op.get("to") or []], "split": bool(op.get("split")), "route": route}


def blanks(lines):
    return [ln["blank"] for ln in lines if ln.get("kind") == BLANK]


def with_blanks(view, graph, lines):
    """The check view and graph of an edit with its blank nodes: one problem per blank node (confirming stays locked, saving
    does not), and the blank node with its connections drawn into the graph (`blank: true`)."""
    bs = blanks(lines)
    if not bs:
        return view, graph
    view = {**view, "ok": False, "errors": list(view.get("errors") or []) + [{"stage": "blank", "line": None, "path": None, "message": BLANK_MSG.format(b["id"])} for b in bs],
            "blank": [b["id"] for b in bs]}
    if graph:
        graph = _copy.deepcopy(graph)
        pos = {n["id"]: (n.get("x", 0), n.get("y", 0)) for n in graph["nodes"]}
        for b in bs:
            x, y = pos.get((b["from"] or [None])[0], (0, 0))
            graph["nodes"].append({"id": b["id"], "name": b["id"], "x": x + 200, "y": y + 40, "loc": None, "dynamic": False, "kind": None, "params": {},
                                   "reads": [], "writes": [], "expect": b["expect"], "blank": True})
            for s, t in [(f, b["id"]) for f in b["from"]] + [(b["id"], t) for t in b["to"]]:
                graph["edges"].append({"id": f"{s}>{t}", "source": s, "target": t, "label": (b["route"] or {}).get("key", "") if t == b["id"] else "",
                                       "kind": "", "conditional": False, "blank": True})
    return view, graph


def _renamed(lines, old, new, before=None):
    """The lines with node `old` (made in this edit) called `new` everywhere it is named."""
    sub = lambda v: new if v == old else v
    def edge(e):
        if isinstance(e, list):
            return [sub(x) for x in e]
        if isinstance(e, dict):
            return {k: ([sub(x) for x in v] if k == "join" and isinstance(v, list) else sub(v) if k in ("from", "to") else v) for k, v in e.items()}
        return e
    out = _copy.deepcopy(lines)
    for ln in out:
        for o in ln["ops"]:
            if o.get("op") == "add_node" and isinstance(o.get("node"), dict):
                o["node"]["id"] = sub(o["node"].get("id"))
                if "after" in o:
                    o["after"] = sub(o["after"])
            if "edge" in o:
                o["edge"] = edge(o["edge"])
            if "node" in o and isinstance(o["node"], str):
                o["node"] = sub(o["node"])
            if o.get("op") == "set" and o.get("param") == "cases" and isinstance(o.get("value"), dict):
                o["value"] = {k: sub(v) for k, v in o["value"].items()}
            if o.get("op") == "set" and o.get("param") == "default":
                o["value"] = sub(o.get("value"))
        ln["node"] = sub(ln.get("node"))
        ln["summary"] = _summary(ln["ops"], before)
        ln["label"] = " · ".join(dict.fromkeys(describe(o)[3] for o in ln["ops"]))
    return out


def dependents(lines, numbers):
    """Lines (not in `numbers`) that use a node or file created by one of the given lines — directly, through an add_node's
    `after`, or through a line that itself depends on them — they must be reverted together (manual p.34 #15)."""
    chosen, out = set(numbers), set()
    while True:
        made, groups = set(), set()
        for ln in lines:
            if ln["n"] in chosen | out:
                for o in ln["ops"]:
                    made |= creates(o)
                if any(o.get("op") == "add_node" for o in ln["ops"]):
                    groups.add(ln.get("group"))   # E-S-09: the lines made with a node (e.g. the connection an insert removed) go with it
        more = {ln["n"] for ln in lines if ln["n"] not in chosen | out
                and (any((touches(o) | ({o.get("after")} if o.get("op") == "add_node" else set())) & made for o in ln["ops"]) or ln.get("group") in groups)}
        if not more:
            return sorted(out)
        out |= more


def remove(lines, numbers):
    return [ln for ln in lines if ln["n"] not in set(numbers)]


PRE_FLOW_NOTE = "원래 Pack에 있던 흐름 경고 — "   # EV-056 CHG-235 (D-376): a moved flow problem keeps packrt's dict, this before its message
_F13 = _re_mod.compile(r"^(?:함수 )?node '([a-z][a-z0-9_]*)'(?:에 닿는 경로 중|는 이 자리에 둘 수 없습니다 —) reads '([^']+)'를 앞에서 쓰지 않는 경로가 있습니다")


def _graph_of(path):
    parts = str(path or "").split("/")
    return parts[1] if len(parts) > 1 and parts[0] == "graphs" else None


def preexisting_flow(base_dir):
    """EV-056 CHG-235: the (graph, message) pairs of packrt's F1 · F3 flow problems (a node reading a key some way in does not
    write) that the edit's starting copy already has — computed on every call (no cache, E3-A1-B4). F2 is not among them."""
    from packrt.ir import flow, load_pack
    lp = load_pack(Path(base_dir))
    if lp.doc is None or not lp.ok:
        return set()
    return {(p[1] if len(p) > 1 and p[0] == "graphs" else None, m) for lvl, p, m in flow.check(lp.doc, Path(base_dir)) if lvl == "error" and _F13.match(m)}


def drop_preexisting(res, base_dir, out):
    """EV-056 CHG-235 (D-376): an F1 · F3 flow problem of the edited copy `out` that the starting copy has too (same graph, same
    message) and whose key still has a node writing it above that node in the edited copy is no longer a problem — it moves to
    res.warnings (packrt's dict, PRE_FLOW_NOTE before the message) and res.moved_flow lists it. A key left with no writer above
    ('worse', E3-A1-B3), any other flow problem and every other stage stay. With no problem left: ok, and the revision packrt gives
    a copy without problems (load_pack of `out`)."""
    res.moved_flow = []
    flows = [e for e in res.errors if e.get("stage") == "flow" and _F13.match(e.get("message") or "")]
    if not flows or out is None or not Path(out).is_dir():
        return res
    pre = preexisting_flow(base_dir)
    if not pre:
        return res
    from packrt.ir import load_pack
    lp = load_pack(Path(out))
    if lp.doc is None:
        return res
    moved = []
    for e in flows:
        g, m = _graph_of(e.get("path")), _F13.match(e["message"])
        if (g, e["message"]) not in pre or g not in (lp.doc.get("graphs") or {}):
            continue
        io = node_io(lp.doc, g, m.group(1)) or {}
        r = next((x for x in io.get("reads", []) if x["key"] == m.group(2)), None)
        if r and r["from"]:
            moved.append(e)
    if moved:
        res.errors = [e for e in res.errors if not any(e is x for x in moved)]
        res.warnings = list(res.warnings) + [{**e, "message": PRE_FLOW_NOTE + e["message"]} for e in moved]
        res.moved_flow = [e["message"] for e in moved]
        if not res.errors and lp.ok:
            res.ok, res.revision = True, lp.revision
    return res


def check(base_dir, lines, out_dir=None):
    """apply_patch of the whole list on the starting copy. With no lines the result is the starting copy itself. EV-056: problems the
    starting copy already has do not count (drop_preexisting); without out_dir a temporary copy is used and removed (out_dir None)."""
    if out_dir is not None:
        return drop_preexisting(apply_patch(base_dir, all_ops(lines), out_dir=out_dir), base_dir, out_dir)
    tmp = Path(tempfile.mkdtemp(prefix="llmops-edit-"))
    try:
        res = drop_preexisting(apply_patch(base_dir, all_ops(lines), out_dir=tmp / "pack"), base_dir, tmp / "pack")
        res.out_dir = None
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def recheck_after(base_dir, folder, ops):
    """EV-056 CHG-236: the delete preview's 'after' again — `ops` on a temporary copy of `folder` (the edited Pack) through the same
    rule, problems the edit's starting copy `base_dir` already has not counted. (ok, errors)."""
    tmp = Path(tempfile.mkdtemp(prefix="llmops-edit-"))
    try:
        res = drop_preexisting(apply_patch(folder, ops, out_dir=tmp / "pack"), base_dir, tmp / "pack")
        return res.ok, res.errors
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_view(res):
    return {"ok": res.ok, "errors": res.errors, "warnings": res.warnings}


def materialize(base_dir, lines, dest):
    """Write the edited Pack to dest (a new empty folder in the LLMOps data folder) for confirming. Raises ValueError with the
    packrt problems when the edit does not pass. With no lines the starting copy is copied as it is."""
    dest = Path(dest)
    if blanks(lines):   # EV-073: a blank node has no kind yet — never confirmed (D-443)
        raise ValueError([{"stage": "blank", "message": BLANK_MSG.format(b["id"])} for b in blanks(lines)])
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not lines:
        shutil.copytree(base_dir, dest)
        return None
    res = check(base_dir, lines, out_dir=dest)
    if not res.ok:
        shutil.rmtree(dest, ignore_errors=True)
        raise ValueError(res.errors)
    return res


def temp_copy(base_dir, lines, pending_ok=False):
    """(temporary folder holding the edited Pack, cleanup function) — for the change view. With pending_ok, an edit whose only
    problems come from a node waiting for its next connection is copied too (that node's popover, manual p.14 · 32 #2)."""
    tmp = Path(tempfile.mkdtemp(prefix="llmops-edit-"))
    try:
        if pending_ok and lines:
            res = check(base_dir, lines, out_dir=tmp / "pack")
            waiting = [e for e in res.errors if any(r.match(e.get("message") or "") for r in PENDING)]
            if not res.ok and not (waiting and all(any(r.match(e.get("message") or "") for r in PENDING) or FOLLOWS_PENDING.match(e.get("message") or "") for e in res.errors)):
                raise ValueError(res.errors)
        else:
            materialize(base_dir, lines, tmp / "pack")
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return tmp / "pack", lambda: shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- input / output strip and context bundle (manual p.6 · 13 · 37)
def _graph_facts(doc, gname):
    from packrt.ir import flow, semantic
    g = doc["graphs"][gname]
    G = semantic.Graph(gname, g)
    state = g.get("state") or {}
    input_types = {ep.get("input") for ep in doc.get("entrypoints") or [] if ep.get("graph") == gname}
    initial = {k for k, t in state.items() if semantic.state_type(t) in input_types}
    return g, G, state, flow.written_before(G, g, initial)   # D-2: packrt's own calculation, unchanged


def _type(v):
    return v if isinstance(v, str) else (v or {}).get("type")


def node_io(doc, gname, node):
    """What the node reads (with the nodes upstream that write each key, and whether the key is certainly written by then — the
    packrt flow rule) and what it writes (with the nodes downstream that read it). None when the node is not in the graph."""
    g, G, state, before = _graph_facts(doc, gname)
    n = G.nodes.get(node)
    if n is None:
        return None
    preds = G.preds()
    up, todo = set(), [node]
    while todo:
        x = todo.pop()
        for y in preds.get(x, ()):
            if y not in up:
                up.add(y); todo.append(y)
    down = G.reach(node)
    reads = [{"key": k, "type": _type(state.get(k)), "from": [m for m in G.nodes if m in up and k in (G.nodes[m].get("writes") or [])],
              "certain": k in before.get(node, set()),
              # merge 3 M1 (manual p.13 ④): every node that writes this key, so the screen can ask about the ones not leading here
              "writers": [m for m in G.nodes if m != node and k in (G.nodes[m].get("writes") or [])]} for k in n.get("reads") or []]
    # manual p.13 ③ '읽을 값 · 받을 수 있는 값만': the keys certainly there by this node or written by a node before it
    up_writes = {k for m in up if m in G.nodes for k in (G.nodes[m].get("writes") or [])}
    readable = [k for k in state if k in before.get(node, set()) or k in up_writes]
    writes = [{"key": k, "type": _type(state.get(k)), "to": [m for m in G.nodes if m in down and k in (G.nodes[m].get("reads") or [])]}
              for k in n.get("writes") or []]
    return {"node": node, "kind": n.get("kind"), "reads": reads, "writes": writes, "readable": readable}


def context_bundle(doc, gname, node, addable, options=None, store_values=None):
    """The context bundle of one node (manual p.37 ①): neighbours, input/output, kinds that can be added, the option table
    entries and store values the screen may offer, and the node's role text (expect). The same bundle is the material a final-
    stage AI suggestion would get; today the screen only filters choices with it."""
    io = node_io(doc, gname, node)
    if io is None:
        return None
    from packrt.ir import semantic
    g = doc["graphs"][gname]
    G = semantic.Graph(gname, g)
    before = sorted(G.preds().get(node, ()))
    after = sorted({b for b, _ in G.succ.get(node, [])})
    n = G.nodes[node]
    p = n.get("params") or {}
    branches = {"by": p.get("by"), "cases": p.get("cases") or {}, "default": p.get("default")} if (n.get("kind") or "").startswith("router") else None
    return {"node": node, "neighbors": {"before": before, "after": after, "branches": branches}, "io": io, "addable_kinds": sorted(addable),
            "options": options or {}, "store_values": store_values or {}, "expect": n.get("expect")}


# ---------------------------------------------------------------- checking a whole edit (manual p.14 · 31 #5)
import re as _re

PENDING = (_re.compile(r"^node '([^']+)'에서 END로 가는 경로가 없습니다$"), _re.compile(r"^node '([^']+)'는 START에서 닿지 않습니다$"))
# while a node waits for its next connection, packrt also sees the open branch as running alongside the rest; these messages follow
# from the waiting node and go away with its connection (fork B lead item 6, manual p.14 · 31 #5) — confirming stays locked meanwhile
FOLLOWS_PENDING = _re.compile(r"^동시에 실행될 수 있는 '[^']+'와 '[^']+'가 merge 규칙 없는 state '[^']+'를 함께 씁니다$")


def waiting_message(message):
    """A packrt problem that comes from a node waiting for its next connection (manual p.14 · 31 #5)."""
    return any(r.match(message) for r in PENDING) or bool(FOLLOWS_PENDING.match(message))


def evaluate(base_dir, lines, entry):
    """Check the whole edit on a temporary copy. Returns (check view, revision, static graph of the edited Pack, PatchResult,
    savable). A node that is not connected yet ('END로 가는 경로가 없습니다' · 'START에서 닿지 않습니다') does not stop saving:
    it is listed in check.pending and only confirming is locked (manual p.14). Any other problem stops saving."""
    from packrt.ir import load_pack
    from . import packs
    tmp = Path(tempfile.mkdtemp(prefix="llmops-edit-"))
    try:
        out = tmp / "pack"
        res = drop_preexisting(apply_patch(base_dir, all_ops(lines), out_dir=out), base_dir, out)   # EV-056 CHG-235: before the 'made node' check
        pending, hard = [], []
        for e in res.errors:
            m = next((m for r in PENDING if (m := r.match(e.get("message") or ""))), None)
            (pending.append(m.group(1)) if m else hard.append(e))
        if pending:   # the branch messages of a waiting node do not stop saving (only confirming, res.ok stays False)
            hard = [e for e in hard if not FOLLOWS_PENDING.match(e.get("message") or "")]
        # manual p.31 · 32 #4 (새 화면 검사): a node made in this edit whose chosen value is not written before it on every way in
        # (a connection was changed) — packrt's own written_before on the patched copy, also when packrt stopped at stage 2 first
        made = {nid for ln in lines for o in ln["ops"] if o.get("op") == "add_node" for nid in creates(o)}
        if made and out.is_dir():
            lp = load_pack(out)
            if lp.doc is not None and entry in (lp.doc.get("graphs") or {}):
                _, G, state, before = _graph_facts(lp.doc, entry)
                have = {e.get("message") for e in res.errors}
                for nid in sorted(made & set(G.nodes)):
                    for key in G.nodes[nid].get("reads", []):
                        msg = f"node '{nid}'에 닿는 경로 중 reads '{key}'를 앞에서 쓰지 않는 경로가 있습니다"
                        if key in state and key not in before.get(nid, set()) and msg not in have and nid not in pending:
                            e = {"stage": "flow", "line": None, "message": msg}
                            res.errors.append(e); hard.append(e); res.ok, res.revision = False, None   # EV-056: also after drop_preexisting gave one
        # 013-66 (D-480): a retrieve node this edit touches may not return more hits than the Pack's own schema lets out (the wheel's
        # autohyp.pack_limits) — before, only the deploy side's intake stopped it (the rehearsal V6's top_k 13 over maxItems 10)
        touched = {t for ln in lines for o in ln["ops"] for t in touches(o)}
        if touched and out.is_dir():
            from . import autohyp
            lim = autohyp.pack_limits(out).get("top_k")
            lp = load_pack(out) if lim else None
            for n in (((lp.doc or {}).get("graphs") or {}).get(entry) or {}).get("nodes") or [] if lp else []:
                v = (n.get("params") or {}).get("top_k")
                if n.get("id") in touched and str(n.get("kind") or "").startswith("retrieve") and isinstance(v, int) and v > lim[0]:
                    e = {"stage": "limit", "line": None, "message": f"node '{n['id']}' 가져올 개수(top_k) {v} — 이 Pack이 내보낼 수 있는 최대 {lim[0]}({lim[1]})"}
                    res.errors.append(e); hard.append(e); res.ok, res.revision = False, None
        graph = None
        if out.is_dir() and not hard:
            lp = load_pack(out)
            if lp.doc is not None and entry in (lp.doc.get("graphs") or {}):
                try:
                    graph = packs.static_graph(lp.doc, lp.lines, entry, out)
                except Exception:  # noqa: BLE001 - a graph that cannot be drawn yet is shown from the last saved one
                    graph = None
        # while a node waits for its next connection, its own messages are the grey '다음 연결 필요' (manual p.14 · 31 #5), not problems
        shown = [e for e in res.errors if not (pending and (any(r.match(e.get("message") or "") for r in PENDING) or FOLLOWS_PENDING.match(e.get("message") or "")))]
        view = {"ok": res.ok, "errors": shown, "warnings": res.warnings, "pending": sorted(set(pending))}
        view, graph = with_blanks(view, graph, lines)   # EV-073 CHG-AI-2
        return view, res.revision, graph, res, not hard
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def edited_doc(base_dir, lines):
    """(pack.yaml doc, line map) of the edited Pack, read from a temporary copy; None when it cannot be read."""
    from packrt.ir import load_pack
    tmp = Path(tempfile.mkdtemp(prefix="llmops-edit-"))
    try:
        out = tmp / "pack"
        if lines:
            apply_patch(base_dir, all_ops(lines), out_dir=out)
        else:
            shutil.copytree(base_dir, out)
        if not out.is_dir():
            return None
        lp = load_pack(out)
        return (lp.doc, lp.lines) if lp.doc is not None else None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
