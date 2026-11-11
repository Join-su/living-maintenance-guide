"""Pack case runner (EV-002 CHG-07). Started by LLMOps inside its Agent sandbox with the Pack's own Python:

  python -m packrt.runner <pack copy dir>      (stdin: {"nonce", "input", "review": {...}})
Prints JSON lines prefixed with the nonce on a private copy of stdout, in the same shapes as llmops/runner.py:
  {"t":"manifest", ...}  {"t":"node_start",...}  {"t":"node_end",..., "kind", "meta"}  {"t":"case_end","final","stopped"}
  {"t":"case_error","stage","error"}
With a trace context in the request ({"trace": RunContext}, EV-019 CHG-90) the run also reports trace contract 2 events through
packrt.trace.LineSink: {"t":"trace_payload", "ref", "size", "media_type", "role", "retention_class", "value"} and
{"t":"trace_event", "event"}, before case_end. The server relays them to the shared trace writer.
Pack code output on fd 1 goes to stderr. Keys come only from the environment or the file named by PACKRT_ENV_FILE."""
import json
import os
import sys
import time
import traceback

PRIVATE = ("_meta", "_route", "_iter")


def channel(nonce):
    out = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    sys.stdout = os.fdopen(1, "w", encoding="utf-8", buffering=1, closefd=False)

    def emit(obj):
        out.write(nonce + " " + json.dumps(obj, ensure_ascii=False, default=str) + "\n")
        out.flush()
    return emit


def public(d):
    return {k: v for k, v in d.items() if k not in PRIVATE} if isinstance(d, dict) else d


def summary(e):
    return f"{type(e).__name__}: {e}"[:500]


def main(pack_dir):
    req = json.loads(sys.stdin.read())
    emit = channel(req["nonce"])
    from packrt import trace
    from packrt.run.engine import Runtime
    try:
        rt = Runtime(pack_dir)
        emit({"t": "manifest", **rt.manifest()})
        if req.get("expect_revision") and req["expect_revision"] != rt.revision:   # never run a Pack other than the registered one
            emit({"t": "case_error", "stage": "revision", "error": f"revision 불일치 — 등록 {req['expect_revision'][:15]} / 사본 {rt.revision[:15]} — 실행하지 않음"})
            return 1
    except ValueError as e:
        emit({"t": "case_error", "stage": "input" if "형식에 맞지 않음" in str(e) else "load", "error": str(e)[:500]})
        return 1
    except BaseException as e:  # noqa: BLE001 - loading must never crash silently
        emit({"t": "case_error", "stage": "load", "error": summary(e)})
        return 1

    def legacy(ev):   # the line protocol LLMOps' run records are built from (unchanged)
        p = ev.get("payload", {})
        name = p.get("name")
        if ev.get("type") == "task":
            emit({"t": "node_start", "node": name, "task": p.get("id"), "step": ev.get("step"), "input": public(p.get("input")), "ts": time.time()})
        elif ev.get("type") == "task_result":
            res = p.get("result")
            res = dict(res) if isinstance(res, (list, tuple)) and all(isinstance(x, (list, tuple)) and len(x) == 2 for x in res) else res
            meta = ((res or {}).get("_meta") or {}).get(name) if isinstance(res, dict) else None
            err = p.get("error")
            emit({"t": "node_end", "node": name, "task": p.get("id"), "step": ev.get("step"), "output": public(res), "meta": meta,
                  "error": err if err else None, "tools": [], "ts": time.time()})

    ctx = req.get("trace")
    sink = trace.LineSink(emit) if ctx else trace.NullSink()
    try:
        out = rt.run(req["input"], context=ctx, sink=sink, review=req.get("review"), on_debug=legacy)
    except BaseException as e:  # noqa: BLE001 - whatever escapes still ends with a case_error line
        emit({"t": "case_error", "stage": "run", "error": summary(e), "trace": traceback.format_exc()[-1500:]})
        return 1
    d = out.get("detail") or {}
    if out["status"] == "succeeded":
        if out["outcome"] == "stopped_before_review":
            emit({"t": "case_end", "final": out["output"], "stopped": d.get("stopped"), "state": d.get("state")})
        else:
            emit({"t": "case_end", "final": out["output"], "state": d.get("state")})
        return 0
    err = out.get("error") or {}
    line = {"t": "case_error", "stage": d.get("stage", "run"), "error": (d.get("text") or err.get("message") or "")[:500]}
    if d.get("stage") == "trace":
        line["error"] = f"실행 기록 실패({err.get('code')}) — {err.get('message') or ''}"[:500]
        line["stage"] = "run"
    if d.get("node_error"):
        line["node_error"] = d["node_error"]
    if d.get("trace"):
        line["trace"] = d["trace"]
    emit(line)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
