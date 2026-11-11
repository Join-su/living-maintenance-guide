"""D-549 덧 3 A (llmops-2): ④'s diagnosis from the Run record, cost 0 — the first node, in the Run's node order, whose output deviates
by the Pack's own declaration is the responsible node.

The declaration is the Pack's label file (ui-labels/state-labels.json, read only) key 'diagnose': {node: rule}, one of
  cause_share  {"items": <path to a list in the node's output>, "cause": <path to a cause in one item>, "min": <share 0 ~ 1>}
               — a retriever: the right cause's share among what it brought is at least min
  cause_rank   {"ranks": <path to a {cause: score} object in the node's output>, "max": <rank 1 ~>}
               — a judge: the right cause is within the first max by score
The right cause of a case = its confirmed check (the case's final 'equals' on the label file's chosen_check, the D-498 truth) through
the label file's check_cause — or an allowed-section list ('in') whose sections all go to one cause (A-3, the real c1a15_order suite:
15 of 16 checks); sections of more causes, or one with no cause, name none. A two-step case (runs.py EV-079) is read in the Run's
order: its first step's visits (steps[0].visits — where the real suite's retrievers and first judge are), then its own (the second
step's). Without the key, or without chosen_check · check_cause, there is no spec and ④ goes on as before. The measure values are a
person's (the Pack declares them); a node whose output has no declared place reads 'unknown', never wrong."""
import json

from .runs import _path_get

MEASURES = ("cause_share", "cause_rank")
SHARE = "가져온 {label} {n}건 중 정답 원인 {k}건(기준 {min:.0%} 이상)"
RANK = "정답 원인 순위 {rank}위(기준 {max}위 이내)"
RANK_NONE = "정답 원인 순위 없음 — 원인 점수 칸에 정답 원인이 없음(기준 {max}위 이내)"


def _rule(r):
    if not isinstance(r, dict) or r.get("measure") not in MEASURES:
        return None
    if r["measure"] == "cause_share":
        ok = all(isinstance(r.get(k), str) and r[k] for k in ("items", "cause")) and isinstance(r.get("min"), (int, float)) and 0 <= r["min"] <= 1
    else:
        ok = isinstance(r.get("ranks"), str) and r["ranks"] and isinstance(r.get("max"), int) and r["max"] >= 1
    return r if ok else None


def spec_of(label):
    """{"chosen", "cause", "nodes"} from the label file, or None (no 'diagnose' · no chosen_check · no check_cause · no rule left)."""
    if not isinstance(label, dict) or not isinstance(label.get("diagnose"), dict):
        return None
    chosen, cause = label.get("chosen_check"), label.get("check_cause")
    if not (isinstance(chosen, str) and "." in chosen and isinstance(cause, dict) and cause):
        return None
    nodes = {n: r for n, r in label["diagnose"].items() if _rule(r)}
    return {"chosen": chosen, "cause": dict(cause), "nodes": nodes} if nodes else None


def gold_cause(final_checks, spec, output_field=None):
    """The case's right cause: its final check 'equals' on chosen_check (that state's name taken off when it is the output state) →
    check_cause, or its 'in' list when every section in it goes to one and the same cause (A-3); None when the case has no such check,
    a section has no cause or the list's sections go to more causes than one."""
    st_name, rest = spec["chosen"].split(".", 1)
    path = rest if st_name == output_field else spec["chosen"]
    for c in final_checks or []:
        if isinstance(c, dict) and c.get("path") in (path, rest) and "equals" in c:
            return spec["cause"].get(str(c["equals"]))
        if isinstance(c, dict) and c.get("path") in (path, rest) and isinstance(c.get("in"), list):
            causes = {spec["cause"].get(str(x)) for x in c["in"]}
            return next(iter(causes)) if len(causes) == 1 and None not in causes else None
    return None


def _out(visit):
    o = (visit or {}).get("output")
    if isinstance(o, str):
        try:
            o = json.loads(o)
        except ValueError:
            return None
    return o if isinstance(o, dict) else None


def measure(rule, output, gold):
    """(ok, text): ok True (as declared) · False (deviates) · None (the declared place is not in this output)."""
    if rule["measure"] == "cause_share":
        items = _path_get(output, rule["items"]) if output else None
        if not isinstance(items, list) or not items:
            return None, None
        k = sum(1 for it in items if isinstance(it, dict) and _path_get(it, rule["cause"]) == gold)
        label = rule.get("label") or "결과"
        return k / len(items) >= rule["min"], SHARE.format(label=label, n=len(items), k=k, min=rule["min"])
    ranks = _path_get(output, rule["ranks"]) if output else None
    if not isinstance(ranks, dict) or not ranks:
        return None, None
    order = sorted(ranks, key=lambda c: -(ranks[c] if isinstance(ranks[c], (int, float)) else float("-inf")))
    if gold not in order:
        return False, RANK_NONE.format(max=rule["max"])
    rank = order.index(gold) + 1
    return rank <= rule["max"], RANK.format(rank=rank, max=rule["max"])


def case_visits(case):
    """A-3: a case's visits in the Run's order — a two-step case's first step (steps[0].visits; the second step's entry holds its input
    only), then the case's own (the last step's)."""
    return [v for s in (case or {}).get("steps") or [] for v in (s or {}).get("visits") or []] + list((case or {}).get("visits") or [])


def first_deviation(case, spec, gold):
    """{"node": the first declared node (Run order, its first visit) that deviates — or None, "why": its measure text, "rows": [{node,
    ok, why}] up to it, "order": the Run's node order}."""
    visits = case_visits(case)
    seen, rows, order = set(), [], list(dict.fromkeys(v.get("node") for v in visits))
    for v in visits:
        n = v.get("node")
        if n in seen:
            continue
        seen.add(n)
        rule = spec["nodes"].get(n)
        if not rule:
            continue
        ok, why = measure(rule, _out(v), gold)
        rows.append({"node": n, "ok": ok, "input_ok": None, "why": why})
        if ok is False:
            return {"node": n, "why": why, "rows": rows, "order": order}
    return {"node": None, "why": None, "rows": rows, "order": order}
