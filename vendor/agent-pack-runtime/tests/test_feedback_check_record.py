"""EV-014 (D-412 · D-413 덧, 지시 206-p): TraceWriter.record_check_record — one person check record document (c1a15.check-record.v1)
as one trace.feedback row of kind check_record (contract 2: record_key · session_id · signals · the document reference in
edited_value_ref — deploy 208 O-1; body_sha256 without feedback_id and at — O-2) and its redacted document
as a payload file (role check_record), in one transaction. check_check_record checks the outside only (no database, no meaning of
the document). The feedback rows of contract 1 (record_feedback) stay as they were. Fake connection — no database, no socket."""
import hashlib
import json

import pytest

import tracewriter
from tracewriter import TraceWriteError, TraceWriter
from tracewriter.feedback import CHECK_RECORD_CONTRACT, CHECK_RECORD_KIND, check_check_record
from tracewriter.writer import canonical

FID, RID, AT = "fb_01JC0000000000000000000002", "run_01JC0000000000000000000000", "2026-10-27T09:12:03.120Z"
SECRET = "fake-secret-value-0123456789"
KEPT_ID = "fb_01JC0000000000000000000009"   # the feedback id and time a stored row already has (deploy 208 O-2)
KEPT_AT = __import__("datetime").datetime(2026, 10, 27, 8, 0, 0, 500000, tzinfo=__import__("datetime").timezone.utc)
DOC = {"schema": "c1a15.check-record.v1", "record_key": "CR-RPT-0001", "session": {"pack_session_id": "S-A0001"},
       "signals": [{"id": "S1", "sections": ["4.3"]}], "outcome": {"restart": "within"}, "note": f"키 {SECRET}"}
CR = dict(feedback_id=FID, run_id=RID, at=AT, record_key="CR-RPT-0001", session_id="S-A0001", signals=["S1"], record=DOC,
          actor="lead-park", retention_class="dev")


class Cur:
    def __init__(self, db):
        self.db, self._rows = db, []

    def execute(self, sql, params=()):
        self.db.log.append(("sql", " ".join(sql.split()), list(params)))
        if "INSERT INTO trace.feedback" in sql:
            if self.db.fail:
                raise self.db.fail
            self._rows = [] if self.db.stored or self.db.held else [(params[0],)]
        elif sql.startswith("SELECT body_sha256, feedback_id, at, record_key FROM trace.feedback"):
            self._rows = [s if isinstance(s, tuple) else (s, KEPT_ID, KEPT_AT, CR["record_key"]) for s in self.db.stored]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


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
        self.db = db

    def transaction(self):
        return Tx(self.db)

    def cursor(self):
        return Cur(self.db)

    def close(self):
        pass


class DB:
    """stored: rows already there under this record_key or feedback_id ([] = new) — a body_sha256 string (a row of this
    record_key, KEPT_ID · KEPT_AT) or a full (body_sha256, feedback_id, at, record_key) tuple."""

    def __init__(self, stored=(), fail=None, refuse_connect=0, held=False):
        """held: the INSERT is held back by another unique key while the SELECT finds no row (E3-A1 B2 C-M1)."""
        self.log, self.stored, self.fail, self.refuse_connect, self.connects = [], list(stored), fail, refuse_connect, 0
        self.held = held

    def connect(self):
        self.connects += 1
        if self.connects <= self.refuse_connect:
            raise OSError("no route")
        return Conn(self)

    def sql(self):
        return [x for x in self.log if x[0] == "sql"]


def writer(tmp_path, db):
    return TraceWriter(payload_root=tmp_path / "payloads", secrets=[SECRET], connect=db.connect)


def cols(insert_sql):
    return [c.strip() for c in insert_sql.split("(", 1)[1].split(")", 1)[0].split(",")]


def feedback_insert(db):
    return next(s for s in db.sql() if s[1].startswith("INSERT INTO trace.feedback"))


# ---------------------------------------------------------------- CHG-W01 · W03: one row + one payload, one transaction
def test_a_check_record_is_one_row_and_its_payload_in_one_transaction(tmp_path):
    db = DB()
    out = writer(tmp_path, db).record_check_record(**CR)
    assert out["result"] == "inserted" and out["record_ref"].startswith("sha256:")
    assert [x for x in db.log if x[0] == "tx"] == [("tx", "begin"), ("tx", "commit")]
    pref = next(s for s in db.sql() if s[1].startswith("INSERT INTO trace.payload_ref"))
    assert pref[2][1:3] == [RID, "check_record"] and pref[2][0] == out["record_ref"][7:]
    sql, params = feedback_insert(db)[1:]
    row = dict(zip(cols(sql), params))
    assert row["contract"] == 2 and row["kind"] == "check_record" and row["source"] == "deploy_api"
    assert row["record_key"] == "CR-RPT-0001" and row["session_id"] == "S-A0001" and row["edited_value_ref"] == out["record_ref"]
    assert "record_ref" not in row and out["feedback_id"] == FID and out["at"] == AT
    assert row["actor"] == "lead-park" and row["actor_kind"] == "human" and row["retention_class"] == "dev"
    assert json.loads(json.dumps(getattr(row["signals"], "obj", row["signals"]))) == ["S1"]
    for v1_only in ("action", "label", "label_list_version", "reason", "review_node", "node", "edited_fields", "role"):
        assert v1_only not in row   # NULL by the table's default (0012 CHECK per kind)
    assert "ON CONFLICT DO NOTHING RETURNING feedback_id" in sql


def test_the_payload_is_the_redacted_document_and_nothing_keeps_the_key(tmp_path):
    db = DB()
    out = writer(tmp_path, db).record_check_record(**CR)
    files = [p for p in (tmp_path / "payloads").rglob("*") if p.is_file()]
    assert len(files) == 1 and files[0].name == out["record_ref"][7:]
    data = files[0].read_bytes()
    assert hashlib.sha256(data).hexdigest() == files[0].name and SECRET.encode() not in data
    assert json.loads(data)["record_key"] == "CR-RPT-0001"
    assert SECRET not in json.dumps([x for x in db.log], ensure_ascii=False, default=str)


def test_the_body_sha_is_the_canonical_row_without_id_and_time(tmp_path):
    db = DB()
    out = writer(tmp_path, db).record_check_record(**CR)
    sql, params = feedback_insert(db)[1:]
    row = dict(zip(cols(sql), params))
    assert row["body_sha256"] == out["body_sha256"]
    other = writer(tmp_path, DB()).record_check_record(**dict(CR, feedback_id=KEPT_ID, at="2026-10-27T10:00:00Z"))
    assert other["body_sha256"] == out["body_sha256"]   # deploy 208 O-2: a new feedback_id or time is the same body


# ---------------------------------------------------------------- CHG-W03: same key rules
def test_the_same_content_again_is_duplicate(tmp_path):
    first = DB()
    sha = writer(tmp_path, first).record_check_record(**CR)["body_sha256"]
    db = DB(stored=[sha])
    out = writer(tmp_path, db).record_check_record(**dict(CR, feedback_id="fb_01JC0000000000000000000003", at="2026-10-27T11:00:00Z"))
    assert out["result"] == "duplicate" and out["feedback_id"] == KEPT_ID and out["at"] == "2026-10-27T08:00:00.500Z"
    sel = next(s for s in db.sql() if s[1].startswith("SELECT body_sha256"))
    assert "WHERE record_key = %s OR feedback_id = %s" in sel[1] and "kind" not in sel[1]
    assert sel[2] == ["CR-RPT-0001", "fb_01JC0000000000000000000003"]


def test_the_same_feedback_id_under_another_record_is_a_conflict(tmp_path):
    """deploy 208 O-2: the same body under this record_key, but the feedback_id is taken by another record — conflict."""
    sha = writer(tmp_path, DB()).record_check_record(**CR)["body_sha256"]
    db = DB(stored=[sha, ("1" * 64, FID, KEPT_AT, "CR-RPT-0999")])
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, db).record_check_record(**CR)
    assert e.value.code == "trace_conflict"
    db = DB(stored=[("1" * 64, FID, KEPT_AT, "CR-RPT-0999")])   # only the other record holds the id: nothing of mine — conflict
    with pytest.raises(TraceWriteError):
        writer(tmp_path, db).record_check_record(**CR)


@pytest.mark.parametrize("stored", [["0" * 64], ["SAME", "0" * 64]], ids=["other", "one-of-two-other"])
def test_other_content_under_the_same_key_is_a_conflict(tmp_path, stored):
    sha = writer(tmp_path, DB()).record_check_record(**CR)["body_sha256"]
    db = DB(stored=[sha if s == "SAME" else s for s in stored])
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, db).record_check_record(**CR)
    assert e.value.code == "trace_conflict" and [x for x in db.log if x[0] == "tx"][-1] == ("tx", "rollback")


def test_connection_failures_are_tried_three_times(tmp_path, monkeypatch):
    monkeypatch.setattr(tracewriter.writer.time, "sleep", lambda s: None)
    db = DB(refuse_connect=2)
    assert writer(tmp_path, db).record_check_record(**CR)["result"] == "inserted" and db.connects == 3
    db = DB(refuse_connect=5)
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, db).record_check_record(**CR)
    assert e.value.code == "trace_unavailable" and db.connects == 3


def test_a_database_refusal_is_not_retried(tmp_path):
    import psycopg
    db = DB(fail=psycopg.errors.CheckViolation("no"))
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, db).record_check_record(**CR)
    assert e.value.code != "trace_unavailable" and db.connects == 1


# ---------------------------------------------------------------- CHG-W02: the outside check, before any file or database
@pytest.mark.parametrize("field,value,where", [
    ("feedback_id", "fb_x", "$.feedback_id"), ("run_id", "run_x", "$.run_id"), ("at", "2026-10-27 09:00", "$.at"),
    ("record_key", "", "$.record_key"), ("record_key", "CR RPT", "$.record_key"), ("record_key", "x" * 129, "$.record_key"),
    ("record_key", "sk-" + "a" * 40, "$.record_key"), ("session_id", None, "$.session_id"), ("session_id", "S/A", "$.session_id"),
    ("signals", "S1", "$.signals"), ("signals", ["S1", "S1"], "$.signals"), ("signals", ["S 1"], "$.signals[0]"),
    ("signals", [f"S{i}" for i in range(21)], "$.signals"), ("record", [], "$.record"), ("record", None, "$.record"),
    ("actor", "Lead Park", "$.actor"), ("retention_class", "forever", "$.retention_class"), ("source", "app", "$.source"),
], ids=lambda v: str(v)[:24])
def test_a_bad_record_stops_before_any_file_or_database(tmp_path, field, value, where):
    db = DB()
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, db).record_check_record(**dict(CR, **{field: value}))
    assert e.value.code == "trace_schema" and where in str(e.value)
    assert db.connects == 0 and not (tmp_path / "payloads").exists()


def test_the_document_key_must_match(tmp_path):
    problems = check_check_record(dict(CR, record=dict(DOC, record_key="CR-RPT-0002")))
    assert problems and problems[0][0] == "$.record.record_key"
    assert check_check_record(dict(CR, record={k: v for k, v in DOC.items() if k != "record_key"})) == []


def test_the_document_size_counts_after_redaction():
    big = {"text": "a" * (1024 * 1024)}
    assert check_check_record(dict(CR, record=big))[0][0] == "$.record"
    assert check_check_record(dict(CR, record={"text": "a" * 1000})) == []


def test_no_signal_is_a_resolved_session(tmp_path):
    db = DB()
    assert writer(tmp_path, db).record_check_record(**dict(CR, signals=[]))["result"] == "inserted"


def test_unknown_fields_and_a_non_dict_are_named():
    assert check_check_record("x") == [("$", "체크 기록은 객체입니다")]
    assert ("$.memo", "모르는 칸입니다") in check_check_record(dict(CR, memo="x"))
    assert check_check_record(dict(CR, source="deploy_api")) == []


def test_a_key_shaped_value_is_never_quoted_in_the_message(tmp_path):
    key = "sk-" + "b" * 40
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, DB()).record_check_record(**dict(CR, session_id=key))
    assert key not in str(e.value)


# ---------------------------------------------------------------- PRES-W01 · W02 · CHG-W04: version one as it was, names
def test_contract_one_feedback_is_as_before(tmp_path):
    import test_feedback_write as v1
    db = v1.DB()
    out = TraceWriter(payload_root=tmp_path / "p", secrets=[v1.SECRET], connect=db.connect).record_feedback(**v1.APPROVE)
    sql, params = next(s for s in db.sql() if s[1].startswith("INSERT INTO trace.feedback"))[1:]
    assert v1.fields_of(sql) == [c for c in v1.DDL_COLUMNS if c != "recorded_at"] and dict(zip(v1.fields_of(sql), params))["contract"] == 1
    assert out["result"] == "inserted" and "kind" not in v1.fields_of(sql) and "ON CONFLICT (feedback_id) DO NOTHING" in sql


def test_public_names_and_version():
    assert tracewriter.__version__ == "0.1.4" and tracewriter.FEEDBACK_CONTRACT == 1
    assert CHECK_RECORD_CONTRACT == 2 and CHECK_RECORD_KIND == "check_record"
    for n in ("check_check_record", "CHECK_RECORD_CONTRACT", "CHECK_RECORD_KIND"):   # the public list is as in 0.1.3 (PUBLIC-API.md)
        assert n not in tracewriter.__all__ and not hasattr(tracewriter, n)
    assert callable(TraceWriter.record_check_record)


# ---------------------------------------------------------------- E3-A1 (critic revise) B1 · B2 · N1 · N7 · N8
@pytest.mark.parametrize("field,value,where", [
    ("signals", ["sk-" + "c" * 16], "$.signals[0]"), ("signals", ["AKIA" + "D" * 16], "$.signals[0]"), ("signals", [SECRET], "$.signals[0]"), ("signals", [SECRET[:12]], None),
    ("record_key", SECRET, "$.record_key"), ("actor", SECRET, "$.actor"),
    ("record_key", "CR-RPT-0001\n", "$.record_key"), ("session_id", "S-A0001\n", "$.session_id"), ("signals", ["S1\n"], "$.signals[0]"),
    ("feedback_id", FID + "\n", "$.feedback_id"), ("at", AT + "\n", "$.at"), ("actor", "lead-park\n", "$.actor"),
    ("run_id", RID + "\n", "$.run_id"), ("session_id", SECRET, "$.session_id"),
], ids=lambda v: repr(v)[:20])
def test_key_shaped_or_line_broken_values_never_reach_a_column(tmp_path, field, value, where):
    """B1: a key-shaped signal (common formats or the writer's own secret) is refused like record_key · session_id · actor; N1: every
    pattern matches the whole value — a trailing line break is refused (one record could become two rows)."""
    db = DB()
    if where is None:   # a part of the secret is not the secret — only the exact value counts as key-shaped
        assert writer(tmp_path, db).record_check_record(**dict(CR, **{field: value}))["result"] == "inserted"
        return
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, db).record_check_record(**dict(CR, **{field: value}))
    assert e.value.code == "trace_schema" and where in str(e.value) and db.connects == 0
    assert SECRET not in str(e.value)


def test_held_back_by_another_key_with_no_row_is_a_conflict(tmp_path):
    """B2 C-M1: nothing inserted and nothing found (another unique key held it back) is never "duplicate"."""
    db = DB(held=True)
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, db).record_check_record(**CR)
    assert e.value.code == "trace_conflict"


def test_the_body_holds_the_document_and_the_sorted_signals(tmp_path):
    """B2 C-M5: another document under the same arguments is another body; signals are stored sorted (N2)."""
    a = writer(tmp_path, DB()).record_check_record(**CR)
    b = writer(tmp_path, DB()).record_check_record(**dict(CR, record=dict(DOC, outcome={"restart": "outside"})))
    assert a["body_sha256"] != b["body_sha256"] and a["record_ref"] != b["record_ref"]
    db1, db2 = DB(), DB()
    one = writer(tmp_path, db1).record_check_record(**dict(CR, signals=["S3", "S1"]))
    two = writer(tmp_path, db2).record_check_record(**dict(CR, signals=["S1", "S3"]))
    assert one["body_sha256"] == two["body_sha256"]
    row = dict(zip(cols(feedback_insert(db1)[1]), feedback_insert(db1)[2]))
    assert row["signals"].obj == ["S1", "S3"] and row["edited_value_ref"] == one["record_ref"]


def test_the_columns_go_as_the_database_wants_them(tmp_path):
    """N8 C-M12 · M13 · M14: payload_ref keeps the record's retention class; signals go as jsonb; at as a timestamp."""
    from datetime import datetime
    from psycopg.types.json import Jsonb
    db = DB()
    writer(tmp_path, db).record_check_record(**CR)
    pref = next(s for s in db.sql() if s[1].startswith("INSERT INTO trace.payload_ref"))
    assert pref[2][6] == "dev"
    row = dict(zip(cols(feedback_insert(db)[1]), feedback_insert(db)[2]))
    assert isinstance(row["signals"], Jsonb) and isinstance(row["at"], datetime)


def test_the_size_limit_is_counted_after_redaction_and_is_inclusive():
    """N8 C-M2 · M3: a document over the limit only before redaction passes; one exactly at the limit passes; one byte more does not."""
    from tracewriter.feedback import MAX_RECORD_BYTES
    from tracewriter.redact import redact

    def size(doc):
        return len(json.dumps(redact(doc, secrets=[SECRET])[0], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    base = size({"t": ""})
    exact = {"t": "a" * (MAX_RECORD_BYTES - base)}
    assert size(exact) == MAX_RECORD_BYTES and check_check_record(dict(CR, record=exact), [SECRET]) == []
    assert check_check_record(dict(CR, record={"t": "a" * (MAX_RECORD_BYTES - base + 1)}), [SECRET])[0][0] == "$.record"
    keys = {"t": "", "k": [SECRET] * 400}
    raw_empty = len(json.dumps(keys, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    assert size(keys) < raw_empty   # redaction makes it shorter
    shrinks = dict(keys, t="a" * (MAX_RECORD_BYTES - size(keys)))
    raw = len(json.dumps(shrinks, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    assert raw > MAX_RECORD_BYTES and size(shrinks) <= MAX_RECORD_BYTES and check_check_record(dict(CR, record=shrinks), [SECRET]) == []


def test_every_signal_is_checked_not_the_first_only():
    assert check_check_record(dict(CR, signals=["S1", "S 2"]))[0][0] == "$.signals[1]"


def test_a_document_that_is_not_json_is_a_schema_error(tmp_path):
    """N7: keys that are not strings next to string keys — TraceWriteError, not TypeError."""
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, DB()).record_check_record(**dict(CR, record={1: "a", "b": 2}))
    assert e.value.code == "trace_schema" and "$.record" in str(e.value)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")], ids=["nan", "inf"])
def test_a_document_with_nan_is_not_standard_json(tmp_path, bad):
    """E3-A2 N2: NaN · Infinity would make a payload file that is not standard JSON."""
    db = DB()
    with pytest.raises(TraceWriteError) as e:
        writer(tmp_path, db).record_check_record(**dict(CR, record=dict(DOC, score=bad)))
    assert e.value.code == "trace_schema" and "$.record" in str(e.value) and db.connects == 0 and not (tmp_path / "payloads").exists()
