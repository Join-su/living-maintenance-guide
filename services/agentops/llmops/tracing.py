"""Evaluation runs into the shared trace (EV-019 CHG-93, trace contract 2 and 7). With LLMOPS_TRACE=postgres every evaluation case is
one trace run (U1) written through the shared trace writer (`tracewriter`), next to the SQLite case record that stays as it was.

- Agent Pack release: the Pack process runs packrt with a trace context; its `trace_payload` / `trace_event` lines go through a
  LineRelay (the writer redacts and stores the raw values, the provisional references are swapped for the stored ones).
- LangGraph code release (no packrt): the runner's own lines (node_start, node_end, case_end, case_error) become the same event
  shapes here (source_kind langgraph, pack_id = release name, pack_revision = code hash, no data release, no registration).
- A record failure is a case failure (CaseTraceError); a case that ends without run_ended (timeout, cancel, crash, a refused
  line) gets one closing run_ended."""
import hashlib
import time
from pathlib import Path

from packrt import trace

from tracewriter import LineRelay, TraceWriteError, new_run_id, new_trace_id

RUNNER = Path(__file__).with_name("runner.py")
OUTCOME_CLOSE = {"timeout": ("failed", "timeout", "case 실행 시간이 제한을 넘음"), "cancelled": ("failed", "cancelled", "Run 취소"),   # CHG-93
                 "error": ("failed", "agent_failed", "Agent 프로세스 오류"), "trace_error": ("failed", "trace_failed", "실행 기록 실패")}


class CaseTraceError(RuntimeError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


def _wrap(e):
    return CaseTraceError(getattr(e, "code", None) or "trace_unavailable", (getattr(e, "message", None) or str(e))[:300])


def runner_version():
    return "llmops-runner:sha256:" + hashlib.sha256(RUNNER.read_bytes()).hexdigest()[:16]


class CaseTrace:
    def __init__(self, writer, uid, release, pack):
        self.ctx = {"run_id": new_run_id(), "trace_id": new_trace_id(), "origin": "evaluation", "evaluation_run_id": uid,
                    "deployment_id": None, "retention_class": "standard"}
        self.release, self.pack, self.writer = release, pack, writer
        try:
            self.sink = writer.sink_for(self.ctx)
        except (TraceWriteError, trace.TraceFailure) as e:
            raise _wrap(e) from e
        self.relay = LineRelay(self.sink) if pack else None
        self.em = None if pack else trace.Emitter(self.ctx, self.sink)
        self.spans, self.iters, self.open_node, self.t0, self.ended, self.started = {}, {}, {}, time.monotonic(), False, False

    # ---------------------------------------------------------------- Agent Pack: relay the runtime's own events
    def pack_line(self, ev):
        try:
            if ev.get("t") == "trace_payload":
                self.relay.payload(ev)
            else:
                out = self.relay.event(ev)
                self.started = self.relay.started
                self.ended = self.relay.ended
                return out
        except (TraceWriteError, trace.TraceFailure) as e:
            raise _wrap(e) from e

    # ---------------------------------------------------------------- LangGraph code release: the runner's lines as events
    def start(self, case_input):
        """run_started before the Agent process starts: refused here means the case does not run at all."""
        if self.pack:   # the Pack process sends run_started itself: check now that the trace would take it, before anything runs
            try:
                self.writer.precheck({"origin": "evaluation", "source_kind": "agent_pack", "evaluation_run_id": self.ctx["evaluation_run_id"]})
            except TraceWriteError as e:
                raise _wrap(e) from e
            return
        g = self.release["graph"]
        try:
            self.em.event("run_started", {"origin": "evaluation", "source_kind": "langgraph", "evaluation_run_id": self.ctx["evaluation_run_id"],
                                          "deployment_id": None, "pack_id": self.release["name"], "pack_revision": self.release["code_hash"],
                                          "data_release_id": None, "release_bindings": {}, "runtime_version": runner_version(),
                                          "graph": g.get("id") or "graph", "manifest": {},
                                          "input_ref": self.em.payload(case_input, "input"), "retention_class": "standard"})
        except trace.TraceFailure as e:
            raise _wrap(e) from e
        self.started = True

    def legacy_line(self, ev):
        if self.pack or not self.started or self.ended:
            return
        kind, node = ev.get("t"), ev.get("node")
        try:
            if kind == "node_start":
                it = self.iters[node] = self.iters.get(node, 0) + 1
                span = trace.new_span_id()
                self.spans[ev.get("task")] = (span, it, time.monotonic())
                self.open_node[ev.get("task")] = node
                self.em.event("node_started", {"node_path": node, "iteration": it, "input_ref": self.em.payload(ev.get("input"), "input")},
                              node_id=node, span_id=span)
            elif kind == "node_end":
                span, it, t0 = self.spans.pop(ev.get("task"), (trace.new_span_id(), self.iters.get(node, 1), time.monotonic()))
                err = ev.get("error")
                self.em.event("node_ended", {"node_path": node, "iteration": it, "status": "error" if err else "done",
                                             "output_ref": None if err else self.em.payload(ev.get("output"), "output"),
                                             "ms": round((time.monotonic() - t0) * 1000),
                                             "error": {"node_path": node, "kind": "langgraph", "code": "node_error", "message": str(err)[:400],
                                                       "retryable": False, "attempt": 1} if err else None,
                                             "meta": {"kind": "langgraph"}}, node_id=node, span_id=span)
            elif kind == "case_end":
                self._end("succeeded", "completed", ev.get("final"), None)
            elif kind == "case_error":
                self._end("failed", None, None, {"code": "agent_" + str(ev.get("stage") or "run"), "message": str(ev.get("error") or "")[:400]})
        except trace.TraceFailure as e:
            raise _wrap(e) from e

    def _end(self, status, outcome, output, error):
        for task, (span, it, t0) in list(self.spans.items()):   # nodes still open when the case ended (timeout, cancel, crash)
            node = next((n for n, c in self.iters.items() if c == it), None)
            self.em.event("node_ended", {"node_path": self.open_node.get(task, node or "?"), "iteration": it, "status": "skipped", "output_ref": None,
                                         "ms": round((time.monotonic() - t0) * 1000), "error": None, "meta": {"kind": "langgraph"}},
                          node_id=self.open_node.get(task, node), span_id=span)
            self.spans.pop(task)
        self.em.event("run_ended", {"status": status, "outcome": outcome, "output_ref": self.em.payload(output, "final") if output is not None else None,
                                    "error": error, "ms": round((time.monotonic() - self.t0) * 1000), "tokens_in": None, "tokens_out": None})
        self.ended = True

    # ---------------------------------------------------------------- after the process is gone
    def close(self, outcome, summary=None, code=None):
        """One closing run_ended when the case ended without one. Never raises (the case result is already decided)."""
        if self.ended or not self.started:
            return None
        status, fixed, text = OUTCOME_CLOSE.get(outcome, ("failed", "agent_failed", "Agent 프로세스 오류"))
        code = code or fixed
        err = {"code": code, "message": (summary or text)[:400]}
        try:
            if self.pack:
                return self.relay.close(status, err)
            self._end(status, None, None, err)
        except Exception:  # noqa: BLE001 - best effort only (trace contract 8: log and keep the case result)
            return None
