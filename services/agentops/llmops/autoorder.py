"""D-565 · D-560 2단계 #7 (dev-2 · llmops-2): the checks a wheel's 헛걸음 fix cases point to — for each fix case, its symptom group (the
case input's group), the section a person found the result at (its truth on the label file's chosen_check: 'equals', or an 'in' list of
one) and the section the Run chose first (its final at that path); where they differ, the person's section is to be seen first in that
group (D-565 덧 3: the Pack option check_first_<group>), most cases first. The kind '점검 순서' (autohyp HYP_KINDS['order']) steps it."""
import json


def _path_get(obj, path):
    for k in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def _truth(final_checks, path, rest):
    for c in final_checks or []:
        if isinstance(c, dict) and c.get("path") in (path, rest):
            if "equals" in c:
                return str(c["equals"])
            if isinstance(c.get("in"), list) and len(c["in"]) == 1:
                return str(c["in"][0])
    return None


def _chosen(case, path, rest):
    f = case.get("final")
    try:
        f = json.loads(f) if isinstance(f, str) and f[:1] in "{[" else f
    except ValueError:
        return None
    v = _path_get(f, rest) if isinstance(f, dict) else None
    v = v if v is not None else (_path_get(f, path) if isinstance(f, dict) else None)
    return str(v) if v is not None else None


def _group(case):
    g = (case.get("input") or {}).get("group") if isinstance(case.get("input"), dict) else None
    return g if isinstance(g, str) and g else None


def pairs(case_ids, run, fix, expected, chosen, output_field=None):
    """[{group, before, after, cases}] — per symptom group, the person's section (before) where the Run chose another (after: the one
    most of those cases chose), most cases first (a tie in the order first met). chosen = the label file's chosen_check '<state>.<path>'
    (the state's name off when it is the output state)."""
    if not chosen or "." not in chosen:
        return []
    st, rest = chosen.split(".", 1)
    path = rest if st == output_field else chosen
    by = {}
    for cid, c in zip(case_ids, (run or {}).get("cases") or []):
        if cid not in fix:
            continue
        a, b, g = _truth(((expected or {}).get(cid) or {}).get("final"), path, rest), _chosen(c, path, rest), _group(c)
        if a and b and g and a != b:
            by.setdefault((g, a), []).append((cid, b))
    out = []
    for (g, a), xs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        afters = [b for _, b in xs]
        out.append({"group": g, "before": a, "after": max(dict.fromkeys(afters), key=afters.count), "cases": [c for c, _ in xs]})
    return out


def twins(nodes):
    """{option: [{node, graph}]} — every node declaring a check_first_* option, in node order (C1-a15: one judging node for the first Run and
    another for the next; the order holds only when both see the same value)."""
    out = {}
    for name, n in nodes.items():
        for knob, spec in (n.get("_options") or {}).items():
            if knob.startswith("check_first_") and isinstance(spec, dict):
                out.setdefault(knob, []).append({"node": n.get("name") or name, "graph": spec.get("option")})
    return out


def with_twins(nodes):
    """The node map with the declaring nodes under '_order_twins' on every node (the order kind sets its option on all of them) — ④'s map
    and ⑤'s, which makes the rows' change lines from a map of its own."""
    tw = twins(nodes)
    return {k: {**v, "_order_twins": tw} for k, v in nodes.items()} if tw else nodes


def attach(nodes, found):
    """The node map with the pairs under '_order_pairs' on every node (the order kind steps only a node declaring the option), the
    twins too."""
    return {k: {**v, "_order_pairs": list(found or [])} for k, v in with_twins(nodes).items()} if found else nodes
