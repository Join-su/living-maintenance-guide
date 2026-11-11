"""V-WRITE (EV-004 FB-2, CHG-F03 · F04): TraceWriter.record_feedback with a fake connection (no database, no socket) — the SQL and
its arguments, one transaction, the payload file of an edit, retries as write_event, duplicate and conflict, and the public names."""
import hashlib
import json
from datetime import datetime

import psycopg
import pytest

import tracewriter
from tracewriter import TraceWriteError, TraceWriter
from tracewriter.writer import canonical

FID, RID, AT = "fb_01JC0000000000000000000001", "run_01JC0000000000000000000000", "2026-10-21T09:12:03.120Z"
SECRET = "fake-secret-value-0123456789"
EDIT = dict(feedback_id=FID, run_id=RID, at=AT, action="edit", actor="owner-01", reason="  원인 1순위가 HDF가 맞음  ", retention_class="standard",
            label="LLM 출력 틀림", review_node="review", node="draft", edited_value={"answer.cause_candidates.0.type": "HDF", "answer.note": f"키 {SECRET}"})
APPROVE = dict(feedback_id=FID, run_id=RID, at=AT, action="approve", actor="owner-01", reason="맞음", retention_class="dev")
# deploy 052 3절 DDL draft columns, in order (recorded_at has its DEFAULT)
DDL_COLUMNS = ["feedback_id", "run_id", "contract", "source", "review_node", "node", "action", "label", "label_list_version", "actor", "actor_kind",
               "role", "reason", "edited_fields", "edited_value_ref", "redaction_policy_version", "retention_class", "at", "recorded_at", "body_sha256"]


class Cur:
    def __init__(self, db):
        self.db, self._one = db, None

    def execute(self, sql, params=()):
        self.db.log.append(("sql", " ".join(sql.split()), list(params)))
        if "INSERT INTO trace.feedback" in sql:
            if self.db.fail:
                raise self.db.fail
            self._one = None if self.db.stored else (params[0],)
        elif sql.startswith("SELECT body_sha256 FROM trace.feedback"):
            self._one = (self.db.stored,) if self.db.stored else None
        else:
            self._one = None

    def fetchone(self):
        return self._one


class Tx:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.log.append(("tx", "begin"))

    def __exit__(self, *exc):
        self.db.log.append(("tx", "commit" if exc[0] is None else "rollback"))
        return False


class Conn:
    def __init__(self, db):
        self.db, self.closed = db, False

    def transaction(self):
        return Tx(self.db)

    def cursor(self):
        return Cur(self.db)

    def close(self):
        self.closed = True


class DB:
    """stored: the body_sha256 already under this feedback_id (None = new). fail: an error the feedback INSERT raises."""

    def __init__(self, stored=None, fail=None, refuse_connect=0):
        self.log, self.stored, self.fail, self.refuse_connect, self.connects = [], stored, fail, refuse_connect, 0

    def connect(self):
        self.connects += 1
        if self.connects <= self.refuse_connect:
            raise OSError("no route")   # _conn turns any open failure into _ConnectFailed
        return Conn(self)

    def sql(self):
        return [x for x in self.log if x[0] == "sql"]


def writer(tmp_path, db):
    return TraceWriter(payload_root=tmp_path / "payloads", secrets=[SECRET], connect=db.connect)


def fields_of(insert_sql):
    return [c.strip() for c in insert_sql.split("(", 1)[1].split(")", 1)[0].split(",")]


def test_an_edit_is_one_transaction_with_its_payload(tmp_path):
    db = DB()
    out = writer(tmp_path, db).record_feedback(**EDIT)
    assert [x[1] for x in db.log if x[0] == "tx"] == ["begin", "commit"]
    (_, pr_sql, pr), (_, fb_sql, fb) = db.sql()
    # the payload file: redact-1, canonical JSON, named by its sha256 — and its trace.payload_ref row
    files = [p for p in (tmp_path / "payloads").rglob("*") if p.is_file()]
    assert len(files) == 1 and json.loads(files[0].read_bytes()) == {"answer.cause_candidates.0.type": "HDF", "answer.note": "키 [가림]"}
    digest = hashlib.sha256(files[0].read_bytes()).hexdigest()
    assert pr_sql == ("INSERT INTO trace.payload_ref (sha256, run_id, role, size, media_type, location, retention_class, redaction_policy_version)"
                      " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING")
    assert pr == [digest, RID, "feedback_edited_value", files[0].stat().st_size, "application/json", f"{digest[:2]}/{digest}", "standard", "redact-1"]
    # the feedback row: the DDL draft's columns in its order, then the conflict rule
    assert fields_of(fb_sql) == [c for c in DDL_COLUMNS if c != "recorded_at"]
    assert fb_sql.endswith("VALUES (" + ",".join(["%s"] * 19) + ") ON CONFLICT (feedback_id) DO NOTHING RETURNING feedback_id")
    row = dict(zip(fields_of(fb_sql), fb))
    assert {k: row[k] for k in ("feedback_id", "run_id", "contract", "source", "review_node", "node", "action", "label", "label_list_version",
                                 "actor", "actor_kind", "role", "reason", "edited_value_ref", "redaction_policy_version", "retention_class")} == {
        "feedback_id": FID, "run_id": RID, "contract": 1, "source": "deploy_api", "review_node": "review", "node": "draft", "action": "edit",
        "label": "LLM 출력 틀림", "label_list_version": "labels-v1", "actor": "owner-01", "actor_kind": "human", "role": None,
        "reason": "원인 1순위가 HDF가 맞음", "edited_value_ref": "sha256:" + digest, "redaction_policy_version": "redact-1", "retention_class": "standard"}
    assert type(row["edited_fields"]).__name__ == "Jsonb" and row["edited_fields"].obj == ["answer.cause_candidates.0.type", "answer.note"]
    assert isinstance(row["at"], datetime) and row["at"].isoformat() == "2026-10-21T09:12:03.120000+00:00"
    # body_sha256: the canonical JSON of the stored columns (at as sent, no recorded_at)
    stored = {k: (row[k].obj if k == "edited_fields" else AT if k == "at" else row[k]) for k in fields_of(fb_sql) if k != "body_sha256"}
    assert row["body_sha256"] == hashlib.sha256(canonical(stored)).hexdigest()
    assert out == {"result": "inserted", "edited_value_ref": "sha256:" + digest, "body_sha256": row["body_sha256"]}
    assert SECRET not in json.dumps([x[2] for x in db.sql()], default=str)


def test_an_approve_has_no_payload(tmp_path):
    db = DB()
    out = writer(tmp_path, db).record_feedback(**APPROVE)
    (_, sql, args), = db.sql()
    row = dict(zip(fields_of(sql), args))
    assert row["edited_fields"] is None and row["edited_value_ref"] is None and row["label"] is None and row["retention_class"] == "dev"
    assert row["review_node"] is None and row["node"] is None   # a run without a review node (034-02 (1))
    assert not (tmp_path / "payloads").exists() and out["result"] == "inserted" and out["edited_value_ref"] is None


def test_the_same_content_again_is_duplicate(tmp_path):
    first = DB()
    sha = writer(tmp_path, first).record_feedback(**EDIT)["body_sha256"]
    again = DB(stored=sha)
    out = writer(tmp_path, again).record_feedback(**EDIT)
    assert out == {"result": "duplicate", "edited_value_ref": out["edited_value_ref"], "body_sha256": sha}
    assert again.sql()[-1][1] == "SELECT body_sha256 FROM trace.feedback WHERE feedback_id = %s" and again.sql()[-1][2] == [FID]
    assert [x[1] for x in again.log if x[0] == "tx"] == ["begin", "commit"]


def test_other_content_under_the_same_id_is_a_conflict(tmp_path):
    db = DB(stored="0" * 64)
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, db).record_feedback(**{**EDIT, "reason": "다른 이유"})
    assert e.value.code == "trace_conflict" and FID in str(e.value)
    assert [x[1] for x in db.log if x[0] == "tx"] == ["begin", "rollback"]   # the payload_ref row goes back with it


def test_a_bad_record_stops_before_any_file_or_database(tmp_path):
    db = DB()
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, db).record_feedback(**{**EDIT, "actor": "Kim Minsu"})
    assert e.value.code == "trace_schema" and str(e.value).startswith("trace_schema: 피드백 형식 오류 $.actor: 별명 ID")
    assert db.connects == 0 and not (tmp_path / "payloads").exists()
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, db).record_feedback(**{**EDIT, "edited_value": {"ghp_abcdefghijklmnopqrstuvwxyz": 1}})   # D-1
    assert str(e.value) == 'trace_schema: 피드백 형식 오류 $.edited_value["[가림]"]: 키 모양 글은 칸 경로로 쓸 수 없습니다'   # no key in the message
    with pytest.raises(TraceWriteError) as e:   # the writer's own secret, as an edited path (FB-2 review B03)
        TraceWriter(payload_root=tmp_path / "payloads", secrets=["Topsecretvalue123"], connect=db.connect).record_feedback(
            **{**EDIT, "edited_value": {"answer.Topsecretvalue123": 1}})
    assert e.value.code == "trace_schema"
    assert db.connects == 0 and not (tmp_path / "payloads").exists()


def test_connection_failures_are_tried_three_times(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr("tracewriter.writer.time.sleep", sleeps.append)
    db = DB(refuse_connect=3)
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, db).record_feedback(**APPROVE)
    assert e.value.code == "trace_unavailable" and db.connects == 3 and sleeps == [0.2, 0.5]
    ok = DB(refuse_connect=2)
    assert writer(tmp_path, ok).record_feedback(**APPROVE)["result"] == "inserted" and ok.connects == 3


@pytest.mark.parametrize("error, code", [
    (psycopg.errors.InsufficientPrivilege("permission denied for table feedback"), "trace_unavailable"),
    (psycopg.errors.UndefinedTable('relation "trace.feedback" does not exist'), "trace_schema"),      # before the DDL is applied
    (psycopg.errors.ForeignKeyViolation("violates foreign key constraint"), "trace_schema"),           # no such run in trace
    (psycopg.errors.CheckViolation("violates check constraint"), "trace_schema"),
])
def test_database_refusals_keep_the_write_event_codes(tmp_path, error, code):
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, DB(fail=error)).record_feedback(**APPROVE)
    assert e.value.code == code
    if code == "trace_schema":
        assert str(e.value).startswith(f"trace_schema: 기록 거부: {type(error).__name__}")


def test_public_names_and_version():
    old = ["TraceWriter", "RunSink", "LineRelay", "TraceWriteError", "redact", "redact_text", "REDACTION_POLICY_VERSION",
           "sqlite_registration_check", "new_run_id", "new_trace_id", "EVENT_SCHEMA", "__version__"]
    assert tracewriter.__all__[:len(old)] == old
    assert tracewriter.__all__[len(old):] == ["check_feedback", "new_feedback_id", "FEEDBACK_CONTRACT", "LABEL_LIST_VERSION", "LABELS", "OK_LABELS"]
    assert all(hasattr(tracewriter, n) for n in tracewriter.__all__) and callable(TraceWriter.record_feedback)
    assert tracewriter.__version__ == "0.1.4" and tracewriter.REDACTION_POLICY_VERSION == "redact-1"


def test_the_reason_is_trimmed_and_redacted_and_the_paths_sorted(tmp_path):
    db = DB()
    edit = {**EDIT, "reason": f"  {SECRET} 그리고 sk-proj-abcdefghijklmnop 때문  ", "edited_value": {"answer.z": 1, "answer.a": 2, "answer.m": 3}}
    writer(tmp_path, db).record_feedback(**edit)
    row = dict(zip(fields_of(db.sql()[1][1]), db.sql()[1][2]))
    assert row["reason"] == "[가림] 그리고 [가림] 때문"   # the exact secret and a common key format, both masked (redact-1)
    assert row["edited_fields"].obj == ["answer.a", "answer.m", "answer.z"]


def test_a_dropped_connection_while_writing_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr("tracewriter.writer.time.sleep", lambda s: None)

    class Once(DB):
        def __init__(self):
            super().__init__()
            self.dropped = False

    db = Once()
    real = Cur.execute

    def execute(self, sql, params=()):
        if "INSERT INTO trace.feedback" in sql and not self.db.dropped:
            self.db.dropped = True
            raise psycopg.OperationalError("server closed the connection")
        return real(self, sql, params)
    monkeypatch.setattr(Cur, "execute", execute)
    assert writer(tmp_path, db).record_feedback(**APPROVE)["result"] == "inserted" and db.connects == 2
