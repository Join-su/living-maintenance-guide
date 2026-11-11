"""D-549 덧 13 (packrt-1 167): the fast pre-screen of a large option space, in the Pack's own process. Started by Executor.screen with the
Pack's Python, packrt on PYTHONPATH and nothing of llmops (stdlib + packrt only):

  python screen_child.py <pack copy dir>     (stdin: {"nonce", "expect_revision", "combos": [[op, …], …], "cases": [{"input", "then"?, "review"?, "second_free"?,
                                                                                                                "records"?, "watch"?}]})

One packrt Runtime runs every case of every combination (a two-step case both steps) with the combination's change lines set on the graph's
node params in memory — the Pack's real nodes and code, nothing copied. What does not change between combinations is reused: a query's
embedding (one provider call per text), a vector search of one query, a second step with the same input.
D-587: a case's "records" names a node — the row says how many results its last visit brought, read from the node's own record (None: not reached) —
and its "watch" the nodes whose visits of the last step the row carries, as a Run keeps them (the field case's checks read them).
Lines, prefixed with the nonce: {"t":"val","n","v"} each distinct value once · {"t":"row","i","cases":[{"state","final","first","records","visits"}]} (values
by n) · {"t":"end","embed_calls","embed_tokens","embed_model"} · {"t":"error","code","where"} (never a provider's text)."""
import copy
import json
import os
import re
import sys

STEP = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def path_get(value, path):
    cur = value
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def path_set(value, path, new):
    parts = path.split(".")
    cur = value
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = cur[part] = {}
        cur = nxt
    cur[parts[-1]] = new


def path_drop(value, path):
    parts = path.split(".")
    cur = value
    for part in parts[:-1]:
        cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


def next_input(first_input, then, first_output):
    """llmops.runs.next_input's rule, here because this process has no llmops (tests hold the two the same)."""
    out = json.loads(json.dumps(first_input))
    for p in then.get("drop") or []:
        path_drop(out, p)
    for p, v in (then.get("set") or {}).items():
        path_set(out, p, json.loads(json.dumps(v)))
    for p, src in (then.get("take") or {}).items():
        path_set(out, p, path_get(first_output, src))
    return out


def final_value(f):
    try:
        return json.loads(f) if isinstance(f, str) and f[:1] in "{[" else f
    except ValueError:
        return f


def apply_op(params, op):
    """A change line ('set' · 'unset') on a node's params at its param ('rerank' · 'rules[0].evidence')."""
    steps = [int(i) if i else k for k, i in STEP.findall(op["param"])]
    cur = params
    for st in steps[:-1]:
        cur = cur[st]
    if op["op"] == "unset":
        cur.pop(steps[-1], None)
    else:
        cur[steps[-1]] = op["value"]


class Embeds:
    """The Runtime's provider with one call per text; the rest of it as it is (key slot · last response)."""

    def __init__(self, provider):
        self.provider, self.seen, self.calls, self.tokens, self.model = provider, {}, 0, 0, None

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def embed(self, provider, model, texts):
        key = (provider, model, tuple(texts))
        if key not in self.seen:
            self.seen[key] = self.provider.embed(provider, model, texts)
            self.calls, self.model = self.calls + 1, model
            last = getattr(self.provider, "last_response", None)
            raw = last() if callable(last) else None
            self.tokens += int(((raw or {}).get("usage") or {}).get("prompt_tokens") or 0) if isinstance(raw, dict) else 0
        return self.seen[key]


def reuse(backends):
    """A vector search of one query kept between runs: search2 does it anew for every call."""
    search, found = backends.FlatLocal.search, {}

    def kept_search(self, qvec, *a, **k):
        key = (id(self), tuple(qvec), json.dumps([a, k], sort_keys=True, default=str))
        if key not in found:
            found[key] = search(self, qvec, *a, **k)
        return found[key]
    backends.FlatLocal.search = kept_search


def listen(case, seen):
    """The on_debug hook: a finished node's record into seen — the count of the case's "records" node, the visits of its "watch" nodes."""
    def hook(ev):
        p = ev.get("payload") or {}
        name, res = p.get("name"), p.get("result")
        if ev.get("type") != "task_result":
            return
        res = dict(res) if isinstance(res, (list, tuple)) and all(isinstance(x, (list, tuple)) and len(x) == 2 for x in res) else res
        ret = (((res or {}).get("_meta") or {}).get(name) or {}).get("retrievals") if isinstance(res, dict) else None
        if name == case.get("records") and isinstance(ret, dict):
            seen["records"] = len(ret.get("results") or [])
        if name in (case.get("watch") or ()):
            out = {k: v for k, v in res.items() if not k.startswith("_")} if isinstance(res, dict) else res
            seen["visits"] = [v for v in seen["visits"] if v["node"] != name] + [{"node": name, "status": "error" if p.get("error") else "done", "output": out, **({"retrievals": ret} if ret else {})}]
    return hook


def main(pack_dir):
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != os.path.dirname(os.path.abspath(__file__))]   # llmops/ is no Pack module path
    req = json.loads(sys.stdin.read())
    out = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)   # Pack code output goes to stderr, as in packrt.runner
    sys.stdout = os.fdopen(1, "w", encoding="utf-8", buffering=1, closefd=False)

    def emit(obj):
        out.write(req["nonce"] + " " + json.dumps(obj, ensure_ascii=False, default=str) + "\n")
        out.flush()

    from packrt.run import backends
    from packrt.run.engine import Runtime
    try:
        rt = Runtime(pack_dir)
    except BaseException as e:  # noqa: BLE001 - loading must end with a line
        emit({"t": "error", "code": type(e).__name__, "where": "load"})
        return 1
    if req.get("expect_revision") and req["expect_revision"] != rt.revision:
        emit({"t": "error", "code": "revision", "where": "load"})
        return 1
    embeds = rt.provider = Embeds(rt.provider)
    reuse(backends)
    nodes = {n["id"]: n for n in rt.graph_doc["nodes"]}
    values, second = {}, {}

    def val(v):
        key = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
        if key not in values:
            values[key] = len(values)
            emit({"t": "val", "n": values[key], "v": v})
        return values[key]

    def step(case, value):
        seen = {"records": None, "visits": []}
        r = rt.run(value, review=case.get("review"), on_debug=listen(case, seen))
        return (("완료", r.get("output")) if r.get("status") == "succeeded" else ("실패", None)) + (seen,)

    def told(case, row, seen, records):
        """The row's case with what was asked of it: the records (either step), the last step's visits."""
        return {**row, **({"records": records} if case.get("records") else {}), **({"visits": val(seen["visits"])} if case.get("watch") else {})}

    def one(k, i, case):
        state, first, seen = step(case, case["input"])
        if not case.get("then") or state != "완료":
            return told(case, {"state": state, "final": val(first)}, seen, seen["records"])
        nxt = next_input(case["input"], case["then"], final_value(first))
        key = (i, None if case.get("second_free") else k, json.dumps(nxt, ensure_ascii=False, sort_keys=True, default=str))   # second_free: its step passes no changed node
        if key not in second:
            second[key] = step(case, nxt)
        state, last, then = second[key]
        return told(case, {"state": state, "final": val(last), "first": val(first)}, then, seen["records"] if then["records"] is None else then["records"])

    start = {op["node"]: copy.deepcopy(nodes[op["node"]]["params"]) for ops in req["combos"] for op in ops}
    try:
        for k, ops in enumerate(req["combos"]):
            for n, p in start.items():
                nodes[n]["params"] = copy.deepcopy(p)
            for op in ops:
                apply_op(nodes[op["node"]]["params"], op)
            emit({"t": "row", "i": k, "cases": [one(k, i, c) for i, c in enumerate(req["cases"])]})
    except BaseException as e:  # noqa: BLE001 - a provider's text never leaves: the name of the error only
        emit({"t": "error", "code": type(e).__name__, "where": "run"})
        return 1
    emit({"t": "end", "embed_calls": embeds.calls, "embed_tokens": embeds.tokens, "embed_model": embeds.model})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
