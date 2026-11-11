"""Trace event emission (trace contract 2, docs/contracts/trace-event-contract.md 7). packrt does not know the database: it numbers
the events of one run (seq, in memory, under a lock), makes span ids, hands raw values to a TraceSink first (put_payload → a
reference) and then emits the event that carries the reference. Storing, redaction and hashing belong to the shared trace writer
(the separate `tracewriter` package) inside the server process.

Sinks here:
- LineSink: the evaluation path. The Pack runs in a sandboxed process; events and raw values go out as JSON lines on the runner's
  private channel and the server relays them to the writer. A value's reference is provisional (the sha256 of its canonical JSON
  before redaction); the server's relay swaps it for the writer's real reference.
- NullSink: no trace context (a run started without one keeps working as before)."""
import hashlib
import json
import secrets
import threading
from datetime import datetime, timezone
from typing import Protocol, TypedDict

CONTRACT = 2
EVENT_TYPES = ("run_started", "node_started", "node_ended", "review_requested", "review_recorded", "artifact_written", "run_ended")


class TraceFailure(Exception):
    """A sink call failed: the run must end as an error (trace contract 8). code comes from the sink's exception when it has one."""

    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


def _failure(e):
    if isinstance(e, TraceFailure):
        return e
    return TraceFailure(getattr(e, "code", None) or "trace_unavailable", (getattr(e, "message", None) or f"{type(e).__name__}: {e}")[:300])


class TraceSink(Protocol):
    def put_payload(self, value: object, *, role: str, retention_class: str) -> dict: ...
    def emit(self, event: dict) -> None: ...


class RunContext(TypedDict):
    run_id: str
    trace_id: str
    origin: str                      # evaluation | deployment
    evaluation_run_id: str | None
    deployment_id: str | None
    retention_class: str             # standard | dev


def canonical(value):
    """Canonical JSON bytes: sorted keys, no spaces, UTF-8 (the hash of a value is the sha256 of these bytes)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def sha256_ref(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def data_release_id(stores):
    """`drs:` + the first 16 hex of sha256 over {store: release_id} as sorted JSON (trace contract 3)."""
    return "drs:" + hashlib.sha256(canonical({k: stores[k] for k in sorted(stores)})).hexdigest()[:16]


def model_release_id(models):
    """`mrs:` + the first 16 hex of sha256 over {model: release_id} as sorted JSON, or None without models (EV-006 CHG-R12, contract
    E). data_release_id keeps covering stores only, so the values already in run records and the deploy ledger stay as they are."""
    if not models:
        return None
    return "mrs:" + hashlib.sha256(canonical({k: models[k] for k in sorted(models)})).hexdigest()[:16]


def release_line(runtime):
    """One deployment line (contract E): Pack id · revision, packrt version, the vector DB / table / model releases a Runtime is bound
    to, data_release_id (the same value as run_started) and model_release_id. The Pack tag is the caller's (deploy knows it)."""
    from . import __version__
    b = runtime.bindings
    stores = {k: v.get("release_id") for k, v in b["stores"].items()}
    models = {k: v.get("release_id") for k, v in (b.get("models") or {}).items()}
    return {"pack": {"id": runtime.doc["pack"]["id"], "revision": runtime.revision}, "packrt": __version__,
            "vector_db": {k: v.get("release_id") for k, v in b["stores"].items() if v.get("type") == "vector"},
            "tables": {k: v.get("release_id") for k, v in b["stores"].items() if v.get("type") == "table"},
            "models": models, "data_release_id": data_release_id(stores), "model_release_id": model_release_id(models)}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_span_id():
    while True:
        s = secrets.token_hex(8)
        if s != "0" * 16:
            return s


class NullSink:
    """No trace context: nothing is recorded, and nothing is serialized (a value that cannot be stored must not fail a run
    that keeps no record)."""
    REF = {"ref": "sha256:" + "0" * 64, "size": 0, "media_type": "application/json"}

    def put_payload(self, value, *, role, retention_class):
        return dict(self.REF)

    def emit(self, event):
        return None


class LineSink:
    """Events and raw values as JSON lines through `emit_line(obj)` (the runner's private stdout channel)."""

    def __init__(self, emit_line):
        self._emit_line = emit_line
        self._lock = threading.Lock()

    def put_payload(self, value, *, role, retention_class):
        data = canonical(value)
        ref = {"ref": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data), "media_type": "application/json"}
        with self._lock:
            self._emit_line({"t": "trace_payload", **ref, "role": role, "retention_class": retention_class, "value": value})
        return ref

    def emit(self, event):
        with self._lock:
            self._emit_line({"t": "trace_event", "event": event})


class Emitter:
    """seq and span bookkeeping for one run. Every sink call goes through here, under one lock, so seq never repeats or skips
    even when parallel nodes finish in other threads."""

    def __init__(self, ctx, sink):
        self.ctx, self.sink = ctx, sink
        self.seq = 0
        self.run_span = new_span_id()
        self._lock = threading.Lock()

    def payload(self, value, role, retention_class=None):
        try:
            return self.sink.put_payload(value, role=role, retention_class=retention_class or self.ctx["retention_class"])
        except Exception as e:  # noqa: BLE001 - any sink failure ends the run
            raise _failure(e) from e

    def event(self, type_, body, node_id=None, span_id=None, parent=True):
        with self._lock:
            ev = {"contract": CONTRACT, "run_id": self.ctx["run_id"], "seq": self.seq, "trace_id": self.ctx["trace_id"],
                  "span_id": span_id or self.run_span, "parent_span_id": self.run_span if (span_id and parent) else None,
                  "type": type_, "at": now_iso(), "node_id": node_id, "body": body}
            try:
                self.sink.emit(ev)
            except Exception as e:  # noqa: BLE001 - the seq is not used up: a closing run_ended may take it
                raise _failure(e) from e
            self.seq += 1
        return ev
