"""LineRelay (trace contract 7, evaluation path): a Pack that runs in a separate sandboxed process sends its events and raw values
as JSON lines (packrt LineSink). The server reads the lines in order and hands them to a writer sink here: a raw value is stored
first through put_payload, which returns the real reference (hash after redaction); the event that follows carries the process's
provisional reference, which is swapped for the real one before emit. A reference the relay has not seen is a schema error."""
from datetime import datetime, timezone

from .errors import TraceWriteError


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class LineRelay:
    def __init__(self, sink):
        self.sink = sink
        self.refs = {}
        self.last = None          # last event emitted (for a closing run_ended)
        self.started = False
        self.ended = False
        self.run_span = None

    def payload(self, line):
        if not isinstance(line, dict) or not isinstance(line.get("ref"), str):
            raise TraceWriteError("trace_schema", "원문 줄 형식이 틀립니다")
        real = self.sink.put_payload(line.get("value"), role=str(line.get("role") or "value"), retention_class=str(line.get("retention_class") or "standard"))
        self.refs[line["ref"]] = real
        return real

    def _swap(self, v):
        if isinstance(v, dict):
            if set(v) == {"ref", "size", "media_type"} and isinstance(v.get("ref"), str):
                if v["ref"] not in self.refs:
                    raise TraceWriteError("trace_schema", f"보내지 않은 원문을 가리킵니다: {v['ref'][:20]}…")
                return dict(self.refs[v["ref"]])
            return {k: self._swap(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self._swap(x) for x in v]
        return v

    def event(self, line):
        ev = line.get("event") if isinstance(line, dict) else None
        if not isinstance(ev, dict) or not isinstance(ev.get("body"), dict):
            raise TraceWriteError("trace_schema", "이벤트 줄 형식이 틀립니다")
        ev = {**ev, "body": self._swap(ev["body"])}
        self.sink.emit(ev)
        self.last = ev
        if ev.get("type") == "run_started":
            self.run_span = ev["span_id"]
        self.started = self.started or ev.get("type") == "run_started"
        self.ended = self.ended or ev.get("type") == "run_ended"
        return ev

    def close(self, status, error):
        """After the process is gone: if the run started but never ended (timeout, cancel, crash, a refused line), add one
        run_ended so the record says how it finished. Returns the event or None."""
        if not self.started or self.ended or self.last is None:
            return None
        ev = {"contract": self.last["contract"], "run_id": self.last["run_id"], "seq": self.last["seq"] + 1, "trace_id": self.last["trace_id"],
              "span_id": self.run_span or _root_span(self), "parent_span_id": None, "type": "run_ended", "at": _now(), "node_id": None,
              "body": {"status": status, "outcome": None, "output_ref": None, "error": error, "ms": None, "tokens_in": None, "tokens_out": None}}
        self.sink.emit(ev)
        self.last, self.ended = ev, True
        return ev


def _root_span(relay):
    ev = relay.last
    return ev["parent_span_id"] or ev["span_id"]
