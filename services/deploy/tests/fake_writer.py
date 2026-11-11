"""DB 없는 시험용 기록기. 공유 기록기 TraceWriter와 같은 모양(sink_for(ctx) → put_payload·emit)이고, 검사·가림·등록 대조는
tracewriter의 공개 부품(EVENT_SCHEMA, redact, sqlite_registration_check 같은 registration_check)을 그대로 쓴다. 저장만 메모리·파일.
제품 코드는 이것을 쓰지 않는다(서버는 TraceWriter). PostgreSQL에 실제로 쌓이는지는 tests/test_logdb.py가 본다."""
import hashlib
import json
import threading
from pathlib import Path

from jsonschema import Draft202012Validator
from tracewriter import EVENT_SCHEMA, REDACTION_POLICY_VERSION, TraceWriteError, redact

_VALIDATOR = Draft202012Validator(EVENT_SCHEMA)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


class FakeWriter:
    def __init__(self, root, *, secrets=(), registration_check=None, fail=None):
        """fail(event) -> 오류 코드 또는 None: 그 이벤트를 기록할 때 TraceWriteError를 낸다(기록 실패 시험)."""
        self.root = Path(root)
        self._secrets, self.registration_check, self.fail = secrets, registration_check, fail
        self.events, self.payload_refs = {}, []
        self.closed = []   # close()가 불릴 때마다 그때까지 기록된 이벤트 수(종료 시험)
        self._lock = threading.Lock()

    @property
    def secrets(self):
        s = self._secrets() if callable(self._secrets) else self._secrets
        return [x for x in s or [] if x]

    def close(self):
        with self._lock:
            self.closed.append(sum(len(v) for v in self.events.values()))
        return 0

    def sink_for(self, ctx):
        return _Sink(self, ctx)

    def events_of(self, run_id):
        return [e for _, e in sorted(self.events.get(run_id, {}).items())]

    def payload_path(self, sha):
        return self.root / "payloads" / sha[:2] / sha

    def store_payload(self, value):
        data = canonical(redact(value, secrets=self.secrets)[0])
        sha = hashlib.sha256(data).hexdigest()
        p = self.payload_path(sha)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return {"ref": "sha256:" + sha, "size": len(data), "media_type": "application/json"}

    def write_event(self, event):
        errs = list(_VALIDATOR.iter_errors(event))
        if errs:
            raise TraceWriteError("trace_schema", f"이벤트 형식 오류: {errs[0].message[:200]}")
        code = self.fail(event) if self.fail else None
        if code:
            raise TraceWriteError(code, "가짜 기록 실패")
        body = redact(event["body"], secrets=self.secrets)[0]
        if event["type"] == "run_started":
            body = {**body, "redaction_policy_version": REDACTION_POLICY_VERSION}
            if self.registration_check:
                self.registration_check({**body, "run_id": event["run_id"]})
        ev = {**event, "body": body}
        with self._lock:
            run = self.events.setdefault(event["run_id"], {})
            old = run.get(event["seq"])
            if old is not None:
                if canonical(old["body"]) != canonical(body):
                    raise TraceWriteError("trace_conflict", "같은 키에 다른 내용")
                return
            run[event["seq"]] = ev


class _Sink:
    def __init__(self, writer, ctx):
        self.writer, self.ctx = writer, ctx

    def put_payload(self, value, *, role, retention_class):
        ref = self.writer.store_payload(value)
        self.writer.payload_refs.append({**ref, "run_id": self.ctx["run_id"], "role": role, "retention_class": retention_class})
        return ref

    def emit(self, event):
        if event.get("run_id") != self.ctx["run_id"] or event.get("trace_id") != self.ctx["trace_id"]:
            raise TraceWriteError("trace_schema", "다른 실행의 이벤트")
        self.writer.write_event(event)
