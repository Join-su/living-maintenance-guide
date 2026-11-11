"""EV-073 (D-440 · D-443 · D-447; manual p.35 ~ 37): the node popover's ✦ AI tab — one node suggested by an LLM.

The context bundle (manual p.37 ①) is what the AI sees: the nodes before and after, the value names they hand over or read, the
values readable at that place (packrt's written_before), the kinds that can be added with one example node of each from the Pack,
the Pack's prompt file names, store names, the fixed flow items (D-107, read only) and the role text and the person's request. Run
values, cases, operational records, verdicts, keys and prompt file bodies are never in it.

The answer is one Pack-format node. It is turned into the same packrt change lines a person makes (manual p.37 ③ — the person
connects the ends, p.36): a blank node gets an add_node at its place plus the connections the person made; an existing node gets
value lines when kind · reads · writes stay, else a same-place replacement (remove_node + add_node of the same id, connections
kept — D-447 Q-4 (나)). Anything outside that is dropped with the reason. Nothing is saved here: the edit checks it with the same
`editing.evaluate`, and '편집에 반영' goes through the same edit/ops path with source 'ai'.

Calls (D-447 Q-1 · Q-2): the Project's LLM (keys.project_llm) through gateway.chat purpose 'ai_node'. In real mode only with the
launch setting LLMOPS_AI_TAB_BUDGET_USD (one cap for the AI tab while the server runs, apart from the wheels' cap), a model of the
price list, and an estimate before the call that stays under the cap; fake mode costs nothing."""
import hashlib
import json
import re

AI_NODE_SYSTEM = ("LLMOps AI 제안: You fill one node of an agent pack graph. Answer only JSON: {\"node\": {\"id\", \"kind\", \"reads\", \"writes\", "
                  "\"params\"}, \"out_choices\": [node ids], \"why\": one short Korean sentence}. Use one kind of addable_kinds and shape params "
                  "like that kind's template. reads only from readable, writes only from state_keys — no new state, no new schema. An llm@1 prompt "
                  "is one of prompt_files and its output is one of llm_outputs (it writes a state of that schema's type). A retriever's store is one of stores. "
                  "A retriever's params follow store_templates['<kind> <store>'] when there is one. Follow kind_hint when it fits. Never change what fixed_flow lists. For a blank node never router@1. For an existing node keep its id; keep kind, reads and writes unless "
                  "the request needs another. Writing a key a before node writes overwrites the value it hands over: when the role adds, write "
                  "another of state_keys, and write that key only when the role replaces it. out_choices only when out_open is true: 2 or 3 ids from after_choices.")
OVERWRITE = "덮어씀 — {key}: 앞 node {node}의 값을 이 node가 다시 씀"   # pack.md 미룬 것(EV-073 셋째): said, kept — the person decides
MAX_INPUT_CHARS = 24000
MAX_OUTPUT_TOKENS = 1500
_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def line_meta(ai):
    """The ai part kept on a change line (CHG-AI-9): model · prompt hash · mode, nothing else."""
    ai = ai if isinstance(ai, dict) else {}
    return {k: str(ai[k])[:100] for k in ("model", "prompt_sha", "mode") if ai.get(k)}


def _kind(n):
    return str((n or {}).get("kind") or "").split("@")[0]


def _types(state):
    return {k: (v if isinstance(v, str) else (v or {}).get("type")) for k, v in (state or {}).items()}


# D-450: the kind a role's verb points to (a hint the model sees; the person's words decide) — Korean verbs, first match wins
VERB_KIND = [(("찾", "검색", "고르"), "retrieve"), (("검사", "확인", "보는지", "맞는지"), "validate"), (("쓰", "다듬", "요약", "고쳐", "답"), "llm"),
             (("결과", "템플릿", "채워"), "render")]


def kind_hint(words, addable):
    """The addable kinds a role's verb points to (D-450), retrieve@2 before @1 when the Pack has it; [] when no verb matches."""
    for verbs, k in VERB_KIND:
        if any(v in (words or "") for v in verbs):
            return sorted((a for a in addable if a.split("@")[0] == k), reverse=True)
    return []


def llm_outputs(doc, pack_dir, state):
    """D-450: the outputs an llm@1 node can have — 'text' and the Pack schemas OpenAI strict structured output takes, each with the
    state keys of that type (a schema with oneOf · loose additionalProperties is left out: packrt would refuse it)."""
    import json as _json
    from pathlib import Path
    from packrt.ir import strict_schema_problems
    out = [{"schema": "text", "writes_state": [k for k, t in state.items() if t == "text"]}]
    for name, ref in (doc.get("schemas") or {}).items():
        try:
            sc = ref if isinstance(ref, dict) else _json.loads((Path(pack_dir) / ref).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(sc, dict) and not strict_schema_problems(sc)[0]:
            out.append({"schema": name, "writes_state": [k for k, t in state.items() if t == name]})
    return out


def declared_options(pack_dir, gname, node):
    """{name: {min, max, step}} of the node's declared options, read through packrt's public node_options; {} when none or unreadable."""
    try:
        import packrt.ir
        rows = packrt.ir.node_options(pack_dir)
    except Exception:   # noqa: BLE001 - an old packrt or a folder it cannot read: no declared step, the small step by name
        return {}
    return {r["name"]: {k: r[k] for k in ("min", "max", "step") if r.get(k) is not None}
            for r in rows or [] if r.get("node") == node and r.get("graph") in (gname, None) and r.get("name")}


def bundle(doc, gname, node, blank, ask, addable, prompt_files, fixed, pack_dir=None):
    """The context bundle for `node` (an existing node id) or `blank` (the blank part of a blank line). None when the node is not
    in the graph. `fixed` = fixedflow.view(row) or None."""
    from . import editing
    g = doc["graphs"][gname]
    nodes = {n["id"]: n for n in g.get("nodes") or []}
    state = _types(g.get("state"))
    # one example node per kind — and for a retriever per (kind, store) (D-450 go 3): a store without a sensor rerank never shows one
    templates, store_templates = {}, {}
    for n in g.get("nodes") or []:
        if n.get("kind") not in addable:
            continue
        t = {"id": n["id"], "reads": n.get("reads", []), "writes": n.get("writes", []), "params": n.get("params") or {}}
        templates.setdefault(n["kind"], t)
        st = (n.get("params") or {}).get("store")
        if str(n["kind"]).startswith("retrieve") and st:
            store_templates.setdefault(f"{n['kind']} {st}", t)
    if blank is not None:
        before = [b for b in blank["from"] if b in nodes]
        after = [t for t in blank["to"] if t in nodes]
        readable = []
        for b in before:
            io = editing.node_io(doc, gname, b) or {}
            for k in (io.get("readable") or []) + (nodes[b].get("writes") or []):
                if k not in readable:
                    readable.append(k)
        current, role, task = None, blank["expect"]["text"], "blank"
    else:
        if node not in nodes:
            return None
        io = editing.node_io(doc, gname, node) or {}
        n = nodes[node]
        before = sorted({f for r in io.get("reads") or [] for f in r.get("from") or []})
        after = sorted({t for w in io.get("writes") or [] for t in w.get("to") or []})
        readable = list(io.get("readable") or [])
        current = {"kind": n.get("kind"), "reads": n.get("reads", []), "writes": n.get("writes", []), "params": n.get("params") or {}}
        opts = declared_options(pack_dir, gname, node) if pack_dir else {}
        if opts:   # D-551 덧 1 ①: the Pack's declared options of this node (range · step), so '한 칸' is its step
            current["options"] = opts
        role, task = (n.get("expect") or {}).get("text") or "", "existing"
    out_open = blank is not None and not blank["to"]
    after_choices = []
    if out_open:   # the output end stays the person's (manual p.36): nodes after this place — not the ones before it
        from packrt.ir import semantic
        preds, up, todo = semantic.Graph(gname, g).preds(), set(before), list(before)
        while todo:
            for y in preds.get(todo.pop(), ()):
                if y not in up:
                    up.add(y); todo.append(y)
        after_choices = [m for m in nodes if m not in up][:12]
    keep = [{"what": i.get("what"), "value": i.get("value")} for i in ((fixed or {}).get("items") or [])]
    return {"task": task, "node": blank["id"] if blank else node, "ask": ask or role, "role": role, "graph": gname,
            "before": [{"id": b, "kind": nodes[b].get("kind"), "writes": nodes[b].get("writes", [])} for b in before],
            "after": [{"id": t, "kind": nodes[t].get("kind"), "reads": nodes[t].get("reads", [])} for t in after],
            "readable": readable, "state_keys": state, "addable_kinds": sorted(addable), "templates": templates, "store_templates": store_templates,
            "prompt_files": sorted(prompt_files),
            # D-450: what a retriever can read (store · its type · how it is split) and what an llm node can write
            "stores": {k: {"type": (v or {}).get("type"), "split": ((v or {}).get("index") or {}).get("split"), "rerank": bool(((v or {}).get("index") or {}).get("rerank_norm"))}
                       for k, v in (doc.get("stores") or {}).items()},
            "llm_outputs": llm_outputs(doc, pack_dir, state) if pack_dir else [],
            "kind_hint": kind_hint(f"{ask or ''} {role}", addable),
            "fixed_flow": {"state": (fixed or {}).get("state"), "keep": keep} if fixed else None,
            "current": current, "out_open": out_open, "after_choices": after_choices}


def messages(b):
    user = json.dumps(b, ensure_ascii=False, default=str)[:MAX_INPUT_CHARS]
    return [{"role": "system", "content": AI_NODE_SYSTEM}, {"role": "user", "content": user}]


def prompt_sha(msgs):
    return "sha256:" + hashlib.sha256(json.dumps(msgs, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def estimate_usd(msgs, price):
    """Before the call (A2 N3): ASCII characters ÷ 4 + one token per other character, plus the full output cap."""
    text = json.dumps(msgs, ensure_ascii=False)
    ascii_n = sum(1 for ch in text if ord(ch) < 128)
    tokens = ascii_n / 4 + (len(text) - ascii_n)
    return (tokens * price[0] + MAX_OUTPUT_TOKENS * price[1]) / 1_000_000


def usage_usd(usage, price):
    u = usage or {}
    return ((u.get("prompt_tokens") or 0) * price[0] + (u.get("completion_tokens") or 0) * price[1]) / 1_000_000


def parse(text):
    from .gateway import strip_fence
    try:
        d = json.loads(strip_fence(text or ""))
    except (ValueError, TypeError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("node"), dict) else None


def to_ops(b, answer, doc, gname, blank, addable, editable):
    """(ops, node, dropped, out_choices): the answer turned into packrt change lines inside the AI's scope (manual p.36)."""
    g = doc["graphs"][gname]
    nodes = {n["id"]: n for n in g.get("nodes") or []}
    order = [n["id"] for n in g.get("nodes") or []]
    a = answer["node"]
    dropped = []
    kind = a.get("kind")
    params = a.get("params") if isinstance(a.get("params"), dict) else {}
    reads = [k for k in (a.get("reads") or []) if isinstance(k, str)]
    writes = [k for k in (a.get("writes") or []) if isinstance(k, str)]
    if [k for k in reads if k not in b["readable"]]:
        dropped.append(f"버림 — 읽을 수 없는 값: {', '.join(k for k in reads if k not in b['readable'])}")
        reads = [k for k in reads if k in b["readable"]]
    if [k for k in writes if k not in b["state_keys"]]:
        dropped.append(f"버림 — 새 state: {', '.join(k for k in writes if k not in b['state_keys'])}")
        writes = [k for k in writes if k in b["state_keys"]]
    st = params.get("store")
    if str(kind).startswith("retrieve") and "rerank" in params and st in b["stores"] and not b["stores"][st].get("rerank"):   # D-450 go 3
        params = {k: v for k, v in params.items() if k != "rerank"}
        dropped.append(f"버림 — 재정렬(rerank): 저장소 {st}에 센서 재정렬 없음")
    if kind == "llm@1" and params.get("prompt") not in b["prompt_files"]:
        t = (b["templates"].get("llm@1") or {}).get("params") or {}
        dropped.append(f"버림 — Pack에 없는 프롬프트: {params.get('prompt')!r}")
        params = {**params, "prompt": t.get("prompt")}
    if blank is not None:
        if kind not in addable:
            return [], None, dropped + [f"버림 — 추가할 수 없는 종류: {kind!r}"], []
        if kind == "router@1":   # a router's ways out are connections — the person's (manual p.36); 고치기 still offers it
            return [], None, dropped + ["버림 — 갈래는 고치기에서"], []
        nid = a.get("id") if isinstance(a.get("id"), str) and _ID.match(a["id"]) and a["id"] not in nodes else blank["id"]
        node = {"id": nid, "kind": kind, "reads": reads, "writes": writes, "params": params, "expect": blank["expect"] if blank["expect"]["text"] else {"text": "AI 제안"}}
        dropped += [OVERWRITE.format(key=k, node=x["id"]) for k in writes for x in b.get("before") or [] if k in (x.get("writes") or [])]
        ops = [{"op": "add_node", "graph": gname, "node": node, **({"after": blank["from"][0]} if blank["from"] and blank["from"][0] in nodes else {})}]
        route = blank.get("route")
        if route and route.get("router") in nodes:
            r = nodes[route["router"]]
            p = r.get("params") or {}
            old = (p.get("cases") or {}).get(route.get("key")) if route.get("key") != "default" else p.get("default")
            if route.get("key") == "default":
                ops.append({"op": "set", "graph": gname, "node": route["router"], "param": "default", "value": nid})
            else:
                ops.append({"op": "set", "graph": gname, "node": route["router"], "param": "cases", "value": {**(p.get("cases") or {}), route["key"]: nid}})
                if p.get("default") and p.get("default") == old:
                    ops.append({"op": "set", "graph": gname, "node": route["router"], "param": "default", "value": nid})
        else:
            for f in blank["from"]:
                if blank["split"]:
                    for t in blank["to"]:
                        ops.append({"op": "remove_edge", "graph": gname, "edge": [f, t]})
                ops.append({"op": "add_edge", "graph": gname, "edge": [f, nid]})
        for t in blank["to"]:
            ops.append({"op": "add_edge", "graph": gname, "edge": [nid, t]})
        outs = []
        if b["out_open"]:   # the AI's picks among the nodes after this place, then the ones reading what the new node writes
            readers = [m for m in b["after_choices"] if set(writes) & set(nodes[m].get("reads") or [])]
            outs = [x for x in (answer.get("out_choices") or []) if x in b["after_choices"]][:3]
            outs += [m for m in readers + b["after_choices"] if m not in outs][: max(0, 3 - len(outs))]
        return ops, node, dropped, outs
    nid, cur = b["node"], nodes[b["node"]]
    if cur.get("kind") not in addable and kind != cur.get("kind"):
        dropped.append("버림 — 이 종류는 바꿔 끼울 수 없음(값만)")
        kind, reads, writes = cur.get("kind"), cur.get("reads", []), cur.get("writes", [])
    if (cur.get("kind") == "router@1") != (kind == "router@1"):
        dropped.append("버림 — 갈래(router)는 갈래끼리만 바꿈")
        kind, reads, writes = cur.get("kind"), cur.get("reads", []), cur.get("writes", [])
    if kind == cur.get("kind") and reads == cur.get("reads", []) and writes == cur.get("writes", []):
        before = cur.get("params") or {}
        ops = []
        for k, v in params.items():
            if v != before.get(k):
                if k == "rules" and isinstance(v, list) and isinstance(before.get("rules"), list) and len(v) == len(before["rules"]):
                    for i, (new, old) in enumerate(zip(v, before["rules"])):   # a basic rule's value at its place, as a person edits it
                        if new == old:
                            continue
                        if isinstance(new, dict) and isinstance(old, dict) and len(new) == 1 and list(new) == list(old) and "custom" not in new:
                            name = next(iter(new))
                            ops.append({"op": "set", "graph": gname, "node": nid, "param": f"rules[{i}].{name}", "value": new[name]})
                        else:
                            dropped.append(f"버림 — 규칙 {i + 1}: 같은 자리 기본 규칙 값만")
                elif k in editable:
                    ops.append({"op": "set", "graph": gname, "node": nid, "param": k, "value": v})
                else:
                    dropped.append(f"버림 — 고칠 수 없는 칸: {k}")
        node = {**cur, "params": {**before, **{o["param"]: o["value"] for o in ops if not o["param"].startswith("rules[")}}}
        if any(o["param"].startswith("rules[") for o in ops):
            node["params"]["rules"] = [r for r in params.get("rules") or []]
        return ops, node, dropped, []
    if kind not in addable:
        return [], None, dropped + [f"버림 — 추가할 수 없는 종류: {kind!r}"], []
    node = {"id": nid, "kind": kind, "reads": reads, "writes": writes, "params": params, "expect": cur.get("expect") or {"text": "AI 제안"}}
    i = order.index(nid)
    ops = [{"op": "remove_node", "graph": gname, "node": nid},
           {"op": "add_node", "graph": gname, "node": node, **({"after": order[i - 1]} if i > 0 else {})}]
    return ops, node, dropped, []


# ---------------------------------------------------------------- fake provider's answer (CHG-AI-7)
STEP = {"temperature": 0.1, "top_k": 1, "max_tokens": 100, "score_threshold": 0.05, "limit": 1}   # D-551 덧 1 ①: a small step by name


def one_step(name, value, spec=None):
    """D-551 덧 1 ① (4차 점검): '한 칸' = the option's declared step; no step → a tenth of its declared range; no range → STEP by
    name (else 1) — never past a declared max (task10's draft temperature 0 went to 1, then 2, the top)."""
    spec = spec or {}
    lo, hi, st = spec.get("min"), spec.get("max"), spec.get("step")
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)   # noqa: E731
    if not num(st) or st <= 0:
        st = (hi - lo) / 10 if num(lo) and num(hi) and hi > lo else STEP.get(name, 1)
    v = value + st if isinstance(value, int) and isinstance(st, int) else round(value + st, 6)
    return min(v, hi) if num(hi) else v


def fake_answer(user):
    """A fixed suggestion from the bundle alone: a blank node takes a kind from its role words ('찾' · '검색' → retrieve, '검사' →
    validate, else llm) shaped like that kind's template; an existing node gets its first number knob one step up."""
    try:
        b = json.loads(user)
    except ValueError:
        return json.dumps({"node": {}, "why": "가짜: 맥락을 읽지 못함"}, ensure_ascii=False)
    t = b.get("templates") or {}
    if b.get("task") == "existing":
        cur = b.get("current") or {}
        params = json.loads(json.dumps(cur.get("params") or {}))
        ask, kind, why = b.get("ask") or "", cur.get("kind"), "가짜 제안: 첫 수 칸을 한 칸 올림"
        if "바꿔 끼" in ask and kind in ("validate@1", "validate@2"):   # a same-place replacement (D-447 Q-4 (나)): the other validate kind, basic rules only
            kind = "validate@1" if kind == "validate@2" else "validate@2"
            params["rules"] = [r for r in params.get("rules") or [] if not (isinstance(r, dict) and "custom" in r)]
            why = f"가짜 제안: 기본 규칙만 남겨 {kind}로 바꿔 끼움"
        elif "규칙" in ask and str(kind).startswith("validate"):   # a stricter evidence rule (touches the fixed flow's check rules)
            for r in params.get("rules") or []:
                if isinstance(r, dict) and isinstance(r.get("evidence"), dict):
                    e = r["evidence"]
                    e["min_score"] = 0.4
                    e["require_source_types"] = sorted(set(e.get("require_source_types") or []) | {"history"})
                    break
            why = "가짜 제안: 근거 규칙을 더 엄하게"
        else:
            specs = cur.get("options") or {}
            for k, v in params.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    params[k] = one_step(k, v, specs.get(k))
                    break
        node = {"id": b.get("node"), "kind": kind, "reads": cur.get("reads"), "writes": cur.get("writes"), "params": params}
        return json.dumps({"node": node, "why": why}, ensure_ascii=False)
    words = f"{b.get('ask') or ''} {b.get('role') or ''}"
    wants = ["retrieve@1", "retrieve@2"] if ("찾" in words or "검색" in words) else ["validate@2", "validate@1"] if "검사" in words else ["llm@1"]
    kind = next((k for k in wants if k in t), None) or next((k for k in t if k != "router@1"), None)
    tp = t.get(kind) or {}
    readable = b.get("readable") or []
    reads = [k for k in tp.get("reads") or [] if k in readable] or readable[:1]
    node = {"id": b.get("node"), "kind": kind, "reads": reads, "writes": list(tp.get("writes") or []), "params": tp.get("params") or {}}
    return json.dumps({"node": node, "out_choices": [], "why": f"가짜 제안: 역할 글의 낱말로 {kind}"}, ensure_ascii=False)   # outputs: to_ops picks the readers
