"""Case runner (B-05). Runs INSIDE the Agent sandbox, cwd = release folder (or upload copy).

  python runner.py <entry-file> <input-field>      (stdin: {"nonce": per-case token, "input": case input})
Calls build_graph(), compiles if needed, runs ONE case with LangGraph's debug stream and writes JSON lines, each prefixed
with the nonce, on a private copy of the original stdout; the user code's own stdout (fd 1, also for its child processes)
goes to stderr, so lines the Agent prints can never be taken for runner events:
  {"t":"node_start","node","task","step","input","ts"}  {"t":"node_end","node","task","step","output","error","tools","ts"}
  {"t":"case_end","final"}  or  {"t":"case_error","stage","error"}
The parent redacts every line before storing or publishing it."""
import importlib.util
import json
import os
import sys
import time
import traceback


def channel(nonce):
    """Move the event pipe to a private, non-inheritable descriptor; fd 1 (print, os.write(1, ...), child processes) → stderr."""
    out = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    sys.stdout = os.fdopen(1, "w", encoding="utf-8", buffering=1, closefd=False)

    def emit(obj):
        out.write(nonce + " " + json.dumps(obj, ensure_ascii=False, default=str) + "\n")
        out.flush()
    return emit


class ToolCalls:
    """Tool calls seen in node outputs during one case: LangChain AIMessage.tool_calls answered by ToolMessage (by
    tool_call_id), and the state convention `tool_calls: [{"tool"|"name", "args", ...result fields}]`."""

    def __init__(self):
        self.items, self.by_id = [], {}

    def scan(self, x, depth=0):
        if depth > 8:
            return
        calls = getattr(x, "tool_calls", None)
        if isinstance(calls, list) and hasattr(x, "content"):
            for c in calls:
                if isinstance(c, dict) and c.get("id") not in self.by_id:
                    item = {"name": c.get("name"), "args": c.get("args"), "result": None}
                    self.items.append(item); self.by_id[c.get("id")] = item
            return
        if getattr(x, "type", None) == "tool" and hasattr(x, "tool_call_id"):
            item = self.by_id.get(x.tool_call_id)
            if item is None:
                item = {"name": getattr(x, "name", None), "args": None, "result": None}
                self.items.append(item); self.by_id[x.tool_call_id] = item
            item["result"] = x.content
            return
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "tool_calls" and isinstance(v, list):
                    for c in v:
                        if isinstance(c, dict) and (c.get("tool") or c.get("name")) and "role" not in c and "function" not in c:
                            rest = {kk: vv for kk, vv in c.items() if kk not in ("tool", "name", "args", "result")}
                            self.items.append({"name": c.get("tool") or c.get("name"), "args": c.get("args"), "result": c["result"] if "result" in c else (rest or None)})
                        else:
                            self.scan(c, depth + 1)
                else:
                    self.scan(v, depth + 1)
        elif isinstance(x, (list, tuple)):
            for v in x:
                self.scan(v, depth + 1)


def summary(exc):
    return f"{type(exc).__name__}: {exc}"[:500]


def main(entry, input_field):
    req = json.loads(sys.stdin.read())
    emit, case_input = channel(req["nonce"]), req["input"]
    del req
    sys.path.insert(0, os.path.dirname(os.path.abspath(entry)))
    try:
        spec = importlib.util.spec_from_file_location("llmops_release_entry", entry)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        graph = mod.build_graph()
        if hasattr(graph, "compile") and not hasattr(graph, "get_graph"):
            graph = graph.compile()
    except BaseException as e:  # noqa: BLE001 - user code
        emit({"t": "case_error", "stage": "load", "error": summary(e)})
        return 1
    state = case_input if isinstance(case_input, dict) else {input_field: case_input}
    final = dict(state)
    tools = ToolCalls()
    try:
        for mode, ev in graph.stream(state, stream_mode=["debug", "values"]):
            if mode == "values":
                final = ev
                continue
            p = ev.get("payload", {})
            if ev.get("type") == "task":
                emit({"t": "node_start", "node": p.get("name"), "task": p.get("id"), "step": ev.get("step"), "input": p.get("input"), "ts": time.time()})
            elif ev.get("type") == "task_result":
                err = p.get("error")
                tools.scan(p.get("result"))
                emit({"t": "node_end", "node": p.get("name"), "task": p.get("id"), "step": ev.get("step"),
                      "output": p.get("result"), "error": err if err else None, "tools": tools.items, "ts": time.time()})
    except BaseException as e:  # noqa: BLE001 - node exceptions surface here
        emit({"t": "case_error", "stage": "run", "error": summary(e), "trace": traceback.format_exc()[-1500:]})
        return 1
    emit({"t": "case_end", "final": final})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
