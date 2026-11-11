"""TraceWriter (trace contract 2, 6-8): the shared trace writer that lives inside a server process (LLMOps Server for evaluations,
the Deployment Server for deployments). One event = one transaction: schema check → redaction → body_sha256 → (run_started:
registration check, trace.run) → trace.event (same key: same hash is success, another hash is trace_conflict) → payload_ref rows
held since the last event → screen tables (node_event, retrieval, review, artifact). Raw values are written once, redacted, to
a content-addressed file before the event that refers to them. Every failure raises TraceWriteError; the caller ends the run."""
import hashlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

from .errors import TraceWriteError
from .feedback import CHECK_RECORD_CONTRACT, CHECK_RECORD_KIND, FEEDBACK_CONTRACT, LABEL_LIST_VERSION, check_check_record, check_feedback
from .redact import REDACTION_POLICY_VERSION, redact, redact_text
from .schema import EVENT

MAX_PAYLOAD = 5 * 1024 * 1024
RETRY_SLEEPS = (0.2, 0.5)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def read_env_file(path):
    out = {}
    if not path:
        return out
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return out


def _ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


def _iso(value):
    """A stored timestamp back to the trace ISO form (UTC, milliseconds, Z); a string stays as it is."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return value


def _ref(value):
    return value.get("ref") if isinstance(value, dict) else None


class TraceWriter:
    def __init__(self, dsn_env="LLMOPSAGENT_TRACE_DSN", payload_root=None, secrets=(), contracts=None, registration_check=None,
                 env_file=None, connect=None):
        self.dsn_env, self.env_file = dsn_env, env_file
        self.payload_root = Path(payload_root) if payload_root else None
        self._secrets = secrets
        self.validators = {v: Draft202012Validator(s) for v, s in (contracts or {2: EVENT}).items()}
        self.registration_check = registration_check
        self._connect = connect
        self._local = threading.local()
        self._opened = []                     # every connection any thread opened, so close() can reach them all
        self._opened_lock = threading.Lock()

    # ---------------------------------------------------------------- plumbing
    def secrets(self):
        s = self._secrets() if callable(self._secrets) else self._secrets
        return [x for x in (s or []) if isinstance(x, str) and x]

    def _dsn(self):
        dsn = os.environ.get(self.dsn_env) or read_env_file(self.env_file).get(self.dsn_env)
        if not dsn:
            raise TraceWriteError("trace_unavailable", f"DB 접속 정보가 없습니다(환경 변수 {self.dsn_env})")
        return dsn

    def _conn(self):
        c = getattr(self._local, "conn", None)
        if c is None or c.closed:
            dsn = None if self._connect else self._dsn()
            try:
                if self._connect:
                    c = self._connect()
                else:
                    import psycopg
                    c = psycopg.connect(dsn, connect_timeout=5)
            except Exception as e:  # noqa: BLE001 - any failure to open: the message may quote the DSN, so only the type is kept
                raise _ConnectFailed(type(e).__name__) from None
            self._local.conn = c
            with self._opened_lock:
                self._opened = [x for x in self._opened if not x.closed] + [c]
        return c

    def close(self):
        """Close every connection this writer opened, in any thread (EV-020 CHG-100: a long-running server ends cleanly). The
        writer stays usable: the next write in a thread opens a new connection. Returns how many were closed."""
        with self._opened_lock:
            conns, self._opened = self._opened, []
        n = 0
        for c in conns:
            try:
                if not c.closed:
                    c.close()
                    n += 1
            except Exception:  # noqa: BLE001
                pass
        return n

    def _drop_conn(self):
        c = getattr(self._local, "conn", None)
        self._local.conn = None
        if c is not None:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass

    def sink_for(self, ctx):
        return RunSink(self, ctx)

    def precheck(self, run):
        """Before a separate process starts a run (the evaluation path): the database answers and the registration check accepts
        the run, so a run the trace would refuse does not start at all (trace contract 8). Raises TraceWriteError."""
        if self.registration_check:
            self.registration_check(run)
        try:
            conn = self._conn()
            conn.execute("SELECT 1")
            conn.rollback()
        except _ConnectFailed as e:
            self._drop_conn()
            raise TraceWriteError("trace_unavailable", f"DB에 기록할 수 없습니다: {e.kind}") from None
        except Exception as e:  # noqa: BLE001 - a broken connection: no detail, it may quote the server
            self._drop_conn()
            raise TraceWriteError("trace_unavailable", f"DB에 기록할 수 없습니다: {type(e).__name__}") from None

    # ---------------------------------------------------------------- payload files
    def store_payload(self, value, retention_class, ctx):
        red, _ = redact(value, secrets=self.secrets())
        data = canonical(red)
        if len(data) > MAX_PAYLOAD:
            raise TraceWriteError("payload_too_large", f"원문 하나가 {len(data)} 바이트로 한도 {MAX_PAYLOAD}를 넘습니다")
        digest = hashlib.sha256(data).hexdigest()
        if self.payload_root is None:
            raise TraceWriteError("payload_failed", "원문 저장 위치(payload_root)가 없습니다")
        rel = f"{digest[:2]}/{digest}"
        dest = self.payload_root / rel
        try:
            if not dest.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-")
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                    os.replace(tmp, dest)
                except OSError:
                    Path(tmp).unlink(missing_ok=True)
                    raise
        except OSError as e:
            raise TraceWriteError("payload_failed", f"원문 파일을 쓸 수 없습니다: {type(e).__name__}")
        return {"ref": "sha256:" + digest, "size": len(data), "media_type": "application/json"}, rel

    # ---------------------------------------------------------------- one event
    def write_event(self, event, pending_refs):
        validator = self.validators.get(event.get("contract")) if isinstance(event, dict) else None
        if validator is None:
            raise TraceWriteError("trace_schema", f"모르는 contract 판입니다: {event.get('contract') if isinstance(event, dict) else type(event).__name__}")
        errs = sorted(validator.iter_errors(event), key=lambda e: list(e.absolute_path))
        if errs:
            where = "/".join(str(p) for p in errs[0].absolute_path) or "(event)"
            raise TraceWriteError("trace_schema", f"이벤트 형식 오류 {where}: {errs[0].message[:200]}")
        body, _ = redact(event["body"], secrets=self.secrets())
        if event["type"] == "run_started":
            body = {**body, "redaction_policy_version": REDACTION_POLICY_VERSION}
            if self.registration_check:
                self.registration_check({**body, "run_id": event["run_id"]})
        body_sha = hashlib.sha256(canonical(body)).hexdigest()
        last = None
        for attempt, pause in enumerate((0, *RETRY_SLEEPS)):
            if pause:
                time.sleep(pause)
            try:
                return self._insert(event, body, body_sha, pending_refs)
            except TraceWriteError:
                raise
            except _ConnectFailed as e:
                last = e
                self._drop_conn()
                continue
            except Exception as e:  # noqa: BLE001 - classified below
                last = e
                if not _is_connection_error(e):
                    raise TraceWriteError(_db_code(e), f"기록 거부: {type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}")
                self._drop_conn()
        raise TraceWriteError("trace_unavailable", f"DB에 기록할 수 없습니다: {last.kind if isinstance(last, _ConnectFailed) else type(last).__name__}")

    def _insert(self, event, body, body_sha, pending_refs):
        from psycopg.types.json import Jsonb
        conn = self._conn()
        run_id, seq = event["run_id"], event["seq"]
        with conn.transaction():
            cur = conn.cursor()
            if event["type"] == "run_started":
                man = body.get("manifest") or {}
                # no ON CONFLICT here: that needs SELECT on trace.run, which trace_writer does not have (appendix B). A repeated
                # run_started fails this INSERT inside a savepoint; the event row below then tells a retry from a conflict.
                import psycopg
                try:
                    with conn.transaction():
                        cur.execute(
                        "INSERT INTO trace.run (run_id, trace_id, origin, source_kind, evaluation_run_id, deployment_id, pack_id, pack_revision,"
                        " data_release_id, release_bindings, runtime_version, graph, manifest_sha256, redaction_policy_version, retention_class, started_at)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (run_id, event["trace_id"], body["origin"], body["source_kind"], body.get("evaluation_run_id"), body.get("deployment_id"),
                         body["pack_id"], body["pack_revision"], body.get("data_release_id"), Jsonb(body.get("release_bindings") or {}),
                         body["runtime_version"], body["graph"], hashlib.sha256(canonical(man)).hexdigest(), body["redaction_policy_version"],
                         body["retention_class"], _ts(event["at"])))
                except psycopg.errors.UniqueViolation:
                    pass
            cur.execute(
                "INSERT INTO trace.event (run_id, seq, span_id, parent_span_id, type, node_id, at, contract, body, body_sha256)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id, seq) DO NOTHING RETURNING seq",
                (run_id, seq, event["span_id"], event["parent_span_id"], event["type"], event["node_id"], _ts(event["at"]),
                 event["contract"], Jsonb(body), body_sha))
            if cur.fetchone() is None:
                cur.execute("SELECT body_sha256 FROM trace.event WHERE run_id = %s AND seq = %s", (run_id, seq))
                row = cur.fetchone()
                if row and row[0] == body_sha:
                    return "duplicate"
                raise TraceWriteError("trace_conflict", f"같은 키({run_id}, seq {seq})에 다른 내용이 왔습니다")
            for ref in pending_refs:
                cur.execute("INSERT INTO trace.payload_ref (sha256, run_id, role, size, media_type, location, retention_class, redaction_policy_version)"
                            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            (ref["sha256"], run_id, ref["role"], ref["size"], ref["media_type"], ref["location"], ref["retention_class"],
                             REDACTION_POLICY_VERSION))
            self._screen_rows(cur, event, body)
        return "inserted"

    def _screen_rows(self, cur, event, body):
        from psycopg.types.json import Jsonb
        j = lambda v: None if v is None else Jsonb(v)
        run_id, seq, node, t = event["run_id"], event["seq"], event["node_id"], event["type"]
        if t == "node_started":
            cur.execute("INSERT INTO trace.node_event (run_id, seq, node_id, iteration, status, started_at, input_ref) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (run_id, seq, node, body["iteration"], "started", _ts(event["at"]), _ref(body["input_ref"])))
        elif t == "node_ended":
            m = body.get("meta") or {}
            cur.execute("INSERT INTO trace.node_event (run_id, seq, node_id, iteration, kind, status, ended_at, output_ref, error, provider, model,"
                        " request_config, tokens_in, tokens_out, prompt_ref, provider_raw_ref, custom_source_hash, expect_checks, route, checks)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (run_id, seq, node, body["iteration"], m.get("kind"), body["status"], _ts(event["at"]), _ref(body.get("output_ref")),
                         j(body.get("error")), m.get("provider"), m.get("model"), j(m.get("request_config")), m.get("tokens_in"), m.get("tokens_out"),
                         _ref(m.get("prompt_ref")), _ref(m.get("provider_raw_ref")), m.get("custom_source_hash"), j(m.get("expect_checks")),
                         j(m.get("route")), j(m.get("checks"))))
            r = m if str(m.get("kind", "")).startswith("retrieve") and "store" in m else None
            if r is not None:
                cur.execute("INSERT INTO trace.retrieval (run_id, seq, node_id, store, release_id, query_ref, query_sha256, filter_applied, results,"
                            " fallback_used, fallback_reason, embedding_mode, embedding_model, embed_tokens) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (run_id, seq, node, r.get("store"), r.get("release_id"), _ref(r.get("query_ref")), r.get("query_sha256"),
                             j(r.get("filter_applied")), j(r.get("results")), r.get("fallback_used"), r.get("fallback_reason"),
                             r.get("embedding_mode"), r.get("embedding_model"), r.get("embed_tokens")))
        elif t in ("review_requested", "review_recorded"):
            cur.execute("INSERT INTO trace.review (run_id, seq, node_id, review_id, event, action, actor, actor_kind, role, reason, edited_fields,"
                        " edited_value_ref, revalidation, at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (run_id, seq, node, body["review_id"], t, body.get("action"), body.get("actor"), body.get("actor_kind"), body.get("role"),
                         body.get("reason"), j(body.get("edited_fields")), _ref(body.get("edited_value_ref")), j(body.get("revalidation")), _ts(event["at"])))
        elif t == "artifact_written":
            cur.execute("INSERT INTO trace.artifact (run_id, seq, node_id, format, sha256, location, size) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (run_id, seq, node, body["format"], body["sha256"], body["location"], body["size"]))

    # ---------------------------------------------------------------- human feedback (EV-004, D-182)
    def record_feedback(self, *, feedback_id, run_id, at, action, actor, reason, retention_class, label=None, review_node=None,
                        node=None, edited_value=None, source="deploy_api"):
        """One person's feedback on a finished run, from the Deployment Server (D-180 (A) · D-182): one row of trace.feedback, added
        only. The record is checked first (check_feedback — the run's own state is the caller's check); the reason is redacted
        (redact-1) and an edit's new values go to a payload file with a trace.payload_ref row (role feedback_edited_value), the
        table keeping the reference and the edited paths only. One call = one transaction. The same feedback_id with the same
        content is "duplicate"; with other content trace_conflict. Returns {"result", "edited_value_ref", "body_sha256"}."""
        record = {"feedback_id": feedback_id, "run_id": run_id, "source": source, "review_node": review_node, "node": node,
                  "action": action, "label": label, "actor": actor, "reason": reason, "edited_value": edited_value,
                  "retention_class": retention_class, "at": at}
        problems = check_feedback(record, self.secrets())   # this writer's secrets count as key-shaped too (FB-2 review B03)
        if problems:
            where, message = problems[0]
            raise TraceWriteError("trace_schema", f"피드백 형식 오류 {where}: {message}")
        pending, ref = [], None
        if action == "edit":
            ref, rel = self.store_payload(edited_value, retention_class, None)
            pending.append({"sha256": ref["ref"][7:], "role": "feedback_edited_value", "size": ref["size"], "media_type": ref["media_type"],
                            "location": rel, "retention_class": retention_class})
        row = {"feedback_id": feedback_id, "run_id": run_id, "contract": FEEDBACK_CONTRACT, "source": source, "review_node": review_node,
               "node": node, "action": action, "label": label, "label_list_version": LABEL_LIST_VERSION, "actor": actor, "actor_kind": "human",
               "role": None, "reason": redact_text(reason.strip(), self.secrets()), "edited_fields": sorted(edited_value) if ref else None,
               "edited_value_ref": ref["ref"] if ref else None, "redaction_policy_version": REDACTION_POLICY_VERSION,
               "retention_class": retention_class, "at": at}
        body_sha = hashlib.sha256(canonical(row)).hexdigest()
        last = None
        for pause in (0, *RETRY_SLEEPS):
            if pause:
                time.sleep(pause)
            try:
                result = self._insert_feedback(row, body_sha, pending)
                return {"result": result, "edited_value_ref": row["edited_value_ref"], "body_sha256": body_sha}
            except TraceWriteError:
                raise
            except _ConnectFailed as e:
                last = e
                self._drop_conn()
                continue
            except Exception as e:  # noqa: BLE001 - classified below, as write_event does
                last = e
                if not _is_connection_error(e):
                    raise TraceWriteError(_db_code(e), f"기록 거부: {type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}")
                self._drop_conn()
        raise TraceWriteError("trace_unavailable", f"DB에 기록할 수 없습니다: {last.kind if isinstance(last, _ConnectFailed) else type(last).__name__}")

    # ---------------------------------------------------------------- person check records (EV-014, D-412 · D-413)
    def record_check_record(self, *, feedback_id, run_id, at, record_key, session_id, signals, record, actor, retention_class,
                            source="deploy_api"):
        """One person check record document (contract 2) from the Deployment Server: one trace.feedback row of kind check_record
        (record_key · session_id · signals, and the document's reference in edited_value_ref — deploy 208 O-1, 0012 keeps four new
        columns) and the document, redacted (redact-1), as a payload file with a trace.payload_ref row (role check_record) — one
        transaction, added only. The outside is checked first (check_check_record); the document's meaning is not. body_sha256 leaves
        feedback_id and at out (deploy 208 O-2): the same record_key with the same body is "duplicate" whatever feedback_id and at
        come, and the stored feedback_id and at are returned; another body under that record_key, or that feedback_id under another
        record, is trace_conflict. Signals are stored sorted. Retries and error codes as record_feedback.
        Returns {"result", "record_ref", "body_sha256", "feedback_id", "at"} — feedback_id · at are the stored ones."""
        given = {"feedback_id": feedback_id, "run_id": run_id, "source": source, "record_key": record_key, "session_id": session_id,
                 "signals": signals, "record": record, "actor": actor, "retention_class": retention_class, "at": at}
        problems = check_check_record(given, self.secrets())
        if problems:
            where, message = problems[0]
            raise TraceWriteError("trace_schema", f"체크 기록 형식 오류 {where}: {message}")
        ref, rel = self.store_payload(record, retention_class, None)
        pending = [{"sha256": ref["ref"][7:], "role": CHECK_RECORD_KIND, "size": ref["size"], "media_type": ref["media_type"],
                    "location": rel, "retention_class": retention_class}]
        row = {"feedback_id": feedback_id, "run_id": run_id, "contract": CHECK_RECORD_CONTRACT, "kind": CHECK_RECORD_KIND, "source": source,
               "record_key": record_key, "session_id": session_id, "signals": sorted(signals), "edited_value_ref": ref["ref"], "actor": actor,
               "actor_kind": "human", "redaction_policy_version": REDACTION_POLICY_VERSION, "retention_class": retention_class, "at": at}
        body_sha = hashlib.sha256(canonical({k: v for k, v in row.items() if k not in ("feedback_id", "at")})).hexdigest()
        last = None
        for pause in (0, *RETRY_SLEEPS):
            if pause:
                time.sleep(pause)
            try:
                result, kept_id, kept_at = self._insert_check_record(row, body_sha, pending)
                return {"result": result, "record_ref": ref["ref"], "body_sha256": body_sha, "feedback_id": kept_id, "at": kept_at}
            except TraceWriteError:
                raise
            except _ConnectFailed as e:
                last = e
                self._drop_conn()
                continue
            except Exception as e:  # noqa: BLE001 - classified below, as record_feedback does
                last = e
                if not _is_connection_error(e):
                    raise TraceWriteError(_db_code(e), f"기록 거부: {type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}")
                self._drop_conn()
        raise TraceWriteError("trace_unavailable", f"DB에 기록할 수 없습니다: {last.kind if isinstance(last, _ConnectFailed) else type(last).__name__}")

    def _insert_check_record(self, row, body_sha, pending_refs):
        from psycopg.types.json import Jsonb
        conn = self._conn()
        with conn.transaction():
            cur = conn.cursor()
            for ref in pending_refs:
                cur.execute("INSERT INTO trace.payload_ref (sha256, run_id, role, size, media_type, location, retention_class, redaction_policy_version)"
                            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            (ref["sha256"], row["run_id"], ref["role"], ref["size"], ref["media_type"], ref["location"], ref["retention_class"],
                             REDACTION_POLICY_VERSION))
            cols = list(row) + ["body_sha256"]
            vals = [Jsonb(v) if k == "signals" else _ts(v) if k == "at" else v for k, v in row.items()] + [body_sha]
            cur.execute(f"INSERT INTO trace.feedback ({', '.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})"
                        " ON CONFLICT DO NOTHING RETURNING feedback_id", vals)   # the feedback_id key or the check_record record_key key
            if cur.fetchone() is None:   # contract-1 rows have no record_key, so record_key alone finds a check record (no kind read)
                cur.execute("SELECT body_sha256, feedback_id, at, record_key FROM trace.feedback WHERE record_key = %s OR feedback_id = %s",
                            (row["record_key"], row["feedback_id"]))
                got = cur.fetchall()
                mine = [g for g in got if g[3] == row["record_key"]]
                if mine and all(g[0] == body_sha for g in got):
                    return "duplicate", mine[0][1], _iso(mine[0][2])
                raise TraceWriteError("trace_conflict", f"같은 체크 기록 키({row['record_key']}) 또는 피드백 id에 다른 내용이 왔습니다")
        return "inserted", row["feedback_id"], row["at"]

    def _insert_feedback(self, row, body_sha, pending_refs):
        from psycopg.types.json import Jsonb
        conn = self._conn()
        with conn.transaction():
            cur = conn.cursor()
            for ref in pending_refs:
                cur.execute("INSERT INTO trace.payload_ref (sha256, run_id, role, size, media_type, location, retention_class, redaction_policy_version)"
                            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            (ref["sha256"], row["run_id"], ref["role"], ref["size"], ref["media_type"], ref["location"], ref["retention_class"],
                             REDACTION_POLICY_VERSION))
            cols = list(row) + ["body_sha256"]
            vals = [Jsonb(v) if k == "edited_fields" and v is not None else _ts(v) if k == "at" else v for k, v in row.items()] + [body_sha]
            cur.execute(f"INSERT INTO trace.feedback ({', '.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})"
                        " ON CONFLICT (feedback_id) DO NOTHING RETURNING feedback_id", vals)
            if cur.fetchone() is None:   # this id is there already: the same content is a retry, other content a conflict
                cur.execute("SELECT body_sha256 FROM trace.feedback WHERE feedback_id = %s", (row["feedback_id"],))
                got = cur.fetchone()
                if got and got[0] == body_sha:
                    return "duplicate"
                raise TraceWriteError("trace_conflict", f"같은 피드백 id({row['feedback_id']})에 다른 내용이 왔습니다")
        return "inserted"


class _ConnectFailed(Exception):
    def __init__(self, kind):
        self.kind = kind
        super().__init__(kind)


def _is_connection_error(e):
    try:
        import psycopg
    except ImportError:
        return False
    return isinstance(e, (psycopg.OperationalError, psycopg.InterfaceError)) and not isinstance(e, psycopg.errors.InsufficientPrivilege)


def _db_code(e):
    """A refused write: missing privilege means the writer runs with the wrong account (unavailable); a constraint or
    data error means the event does not fit the trace tables (schema)."""
    try:
        import psycopg
        if isinstance(e, psycopg.errors.InsufficientPrivilege):
            return "trace_unavailable"
    except ImportError:
        pass
    return "trace_schema"


class RunSink:
    """TraceSink of one run. put_payload stores the file now and keeps its payload_ref row for the next event's transaction
    (trace.payload_ref points at trace.run, which exists only from run_started on)."""

    def __init__(self, writer, ctx):
        self.writer, self.ctx = writer, ctx
        self._lock = threading.Lock()
        self._pending = []

    def put_payload(self, value, *, role, retention_class):
        with self._lock:
            ref, rel = self.writer.store_payload(value, retention_class, self.ctx)
            self._pending.append({"sha256": ref["ref"][7:], "role": role, "size": ref["size"], "media_type": ref["media_type"],
                                  "location": rel, "retention_class": retention_class})
            return ref

    def emit(self, event):
        with self._lock:
            if not isinstance(event, dict) or event.get("run_id") != self.ctx["run_id"] or event.get("trace_id") != self.ctx["trace_id"]:
                raise TraceWriteError("trace_schema", "다른 실행의 이벤트입니다(run_id·trace_id가 실행 문맥과 다름)")
            if event.get("type") == "run_started":   # who started the run is the server's call (RunContext), not the Pack process's
                body = event.get("body") if isinstance(event.get("body"), dict) else {}
                wrong = [k for k in ("origin", "evaluation_run_id", "deployment_id", "retention_class") if body.get(k) != self.ctx.get(k)]
                if wrong:
                    raise TraceWriteError("trace_schema", f"run_started가 실행 문맥과 다릅니다: {wrong}")
            pending, self._pending = self._pending, []
            try:
                self.writer.write_event(event, pending)
            except Exception:
                self._pending = pending + self._pending
                raise
