"""Store (B-03): the only owner of product records. No key values are ever stored. SQLite (llmops.db in the data folder) by
default; EV-029 CHG-130: with LLMOPS_STORE=pg the same methods read and write the shared PostgreSQL `llmops` schema (migration
0005) as llmops_app. The methods' SQL is written once; the PostgreSQL adapter (PgDB) only changes placeholders, table names
(`llmops.` prefix — llmops_app's search_path stays as it is), rowid → ord and identity returns."""
import contextlib
import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  scoring TEXT NOT NULL, rule TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS validation_data (
  project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL, cases TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS releases (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL, how TEXT NOT NULL, path TEXT NOT NULL, color TEXT NOT NULL,
  code_hash TEXT NOT NULL, graph TEXT NOT NULL, env_excluded TEXT NOT NULL DEFAULT '[]',
  last_run_code TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, seq INTEGER NOT NULL,
  release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE CASCADE, meta TEXT NOT NULL, status TEXT NOT NULL,
  created_at REAL NOT NULL, finished_at REAL);
CREATE TABLE IF NOT EXISTS run_cases (
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, idx INTEGER NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (run_id, idx));
CREATE TABLE IF NOT EXISTS input_sets (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, name TEXT NOT NULL, source TEXT NOT NULL,
  release_name TEXT, provider TEXT, cases TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS bundles (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, input TEXT NOT NULL, runs TEXT NOT NULL,
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS briefs (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, title TEXT NOT NULL, fields TEXT NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS verdicts (
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, seq INTEGER NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (run_id, seq));
CREATE TABLE IF NOT EXISTS pack_releases (
  release_id TEXT PRIMARY KEY REFERENCES releases(id) ON DELETE CASCADE, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS eval_suites (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, data TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS run_events (
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, seq INTEGER NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (run_id, seq));
-- EV-019 CHG-94 (decision contract 3 and 6, week 1 in SQLite): decisions and deployment registrations, append only.
-- The shared trace writer reads `deployment` (read only) before it records a deployment run (registration_check).
CREATE TABLE IF NOT EXISTS decision (
  decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject_kind TEXT NOT NULL CHECK (subject_kind IN ('candidate', 'deployment')),
  subject_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('change_approved', 'hold', 'rejected', 'deploy_approved', 'deploy_bootstrap',
                                             'deploy_rolled_back', 'deploy_retired', 'data_release_auto')),
  reason TEXT NOT NULL CHECK (length(trim(reason)) > 0), decided_by TEXT NOT NULL CHECK (length(trim(decided_by)) > 0),
  evidence TEXT NOT NULL DEFAULT '[]', decided_at REAL NOT NULL);
-- EV-044 CHG-219 (migration 0010 in PostgreSQL): the model release of a deployment row (null = no model, or a row from before
-- EV-044) and its bindings; one row is (deployment_id, pack_revision, data_release_id, model_release_id) — the unique index
-- deployment_key is made after the columns (Store._ledger_columns), so an older file with the three-column key still opens.
CREATE TABLE IF NOT EXISTS deployment (
  deployment_id TEXT NOT NULL, pack_id TEXT NOT NULL, pack_revision TEXT NOT NULL, data_release_id TEXT NOT NULL,
  release_bindings TEXT NOT NULL, runtime_version TEXT NOT NULL, pack_commit TEXT, pack_tag TEXT, config_sha256 TEXT NOT NULL,
  decision_id INTEGER NOT NULL REFERENCES decision(decision_id), created_at REAL NOT NULL,
  model_release_id TEXT CHECK (model_release_id IS NULL OR (length(model_release_id) = 20 AND model_release_id GLOB 'mrs:[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]')),
  model_bindings TEXT);
CREATE TRIGGER IF NOT EXISTS decision_append_only_u BEFORE UPDATE ON decision BEGIN SELECT RAISE(ABORT, 'decision is append only'); END;
CREATE TRIGGER IF NOT EXISTS decision_append_only_d BEFORE DELETE ON decision BEGIN SELECT RAISE(ABORT, 'decision is append only'); END;
CREATE TRIGGER IF NOT EXISTS deployment_append_only_u BEFORE UPDATE ON deployment BEGIN SELECT RAISE(ABORT, 'deployment is append only'); END;
CREATE TRIGGER IF NOT EXISTS deployment_append_only_d BEFORE DELETE ON deployment BEGIN SELECT RAISE(ABORT, 'deployment is append only'); END;
-- EV-020 CHG-98 (decision contract 1 and 4, week 1 in SQLite): evaluation case ↔ trace run, append only. EV-022 CHG-106
-- removed the candidate tables from the schema; a data folder that already has them keeps them untouched and no code reads them (S-15).
CREATE TABLE IF NOT EXISTS evaluation_case (
  evaluation_run_id TEXT NOT NULL, case_index INTEGER NOT NULL, case_id TEXT, run_id TEXT NOT NULL,
  PRIMARY KEY (evaluation_run_id, case_index));
CREATE TRIGGER IF NOT EXISTS evaluation_case_append_only_u BEFORE UPDATE ON evaluation_case BEGIN SELECT RAISE(ABORT, 'evaluation_case is append only'); END;
CREATE TRIGGER IF NOT EXISTS evaluation_case_append_only_d BEFORE DELETE ON evaluation_case BEGIN SELECT RAISE(ABORT, 'evaluation_case is append only'); END;
-- EV-022 CHG-108 · 115: the Project's open edit (one at a time, S-1) — the starting release, the folder the edit starts from
-- (the release copy, or an earlier revision kept by S-14) and its change lines. LLMOps keeps it; the Pack is never written. An
-- ended edit stays as a record (confirmed / discarded). node_layout: node positions per release or per edit (manual p.19 · 20).
CREATE TABLE IF NOT EXISTS release_edit (
  edit_id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE CASCADE, base_dir TEXT NOT NULL, base_revision TEXT NOT NULL,
  lines TEXT NOT NULL DEFAULT '[]', revision TEXT, check_result TEXT NOT NULL DEFAULT '{}', graph TEXT,
  started_at REAL NOT NULL, updated_at REAL NOT NULL, ended TEXT, ended_at REAL, result_release_id TEXT, memo TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS release_edit_one_open ON release_edit(project_id) WHERE ended IS NULL;
CREATE TABLE IF NOT EXISTS node_layout (owner TEXT PRIMARY KEY, positions TEXT NOT NULL, updated_at REAL NOT NULL);
-- EV-025 OPS-3 CHG-125 (manual p.30 ③): an operational Run put up as an evaluation case ('올린 case'), append only. The input is
-- the deploy side's stored (redacted) payload, copied as it is and checked against its sha; no expected value (Q-2 A). Not tied to a
-- release by a foreign key, so deleting a Project or a release (AC-21) is not blocked; the name follows the shared DB design ⑨.
CREATE TABLE IF NOT EXISTS case_promotion (
  promotion_id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, release_id TEXT NOT NULL, source_run_id TEXT NOT NULL,
  deployment_id TEXT, source_pack_revision TEXT NOT NULL, input_ref TEXT NOT NULL, input TEXT NOT NULL, observed TEXT NOT NULL,
  promoted_by TEXT NOT NULL CHECK (length(trim(promoted_by)) > 0), reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  promoted_at REAL NOT NULL, UNIQUE (project_id, source_run_id));
CREATE TRIGGER IF NOT EXISTS case_promotion_append_only_u BEFORE UPDATE ON case_promotion BEGIN SELECT RAISE(ABORT, 'case_promotion is append only'); END;
CREATE TRIGGER IF NOT EXISTS case_promotion_append_only_d BEFORE DELETE ON case_promotion BEGIN SELECT RAISE(ABORT, 'case_promotion is append only'); END;
-- EV-030 CHG-150 (supplement A1 ~ A5 · B · C): the auto cycle's own records — the operational Runs' diagnosis (results only, never
-- the raw values — Q-11), people's review labels, wheels with their progress log and hypothesis rows, the Project's settings and a
-- release's fixed flow. Append only (review_label · auto_wheel_log · auto_hypothesis) is kept by the code and, on PostgreSQL, by
-- grants alone — no trigger — so deleting a Project or a release removes them by CASCADE (AC-21). Same tables as migration 0007.
CREATE TABLE IF NOT EXISTS ops_diagnosis (
  diagnosis_id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE CASCADE, run_id TEXT NOT NULL, deployment_id TEXT NOT NULL,
  pack_revision TEXT NOT NULL, batch TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('waiting', 'running', 'done', 'error')),
  rule_result TEXT, ratio_result TEXT, judge_result TEXT, judge_kind TEXT CHECK (judge_kind IN ('fake', 'harness', 'real', 'skipped')),
  rubric_version TEXT NOT NULL DEFAULT 'none', judge_model TEXT, signal TEXT CHECK (signal IN ('fail', 'pass', 'normal', 'none')),
  suspect_node TEXT, error_kind TEXT, created_at REAL NOT NULL, finished_at REAL, UNIQUE (project_id, run_id, rubric_version));
CREATE TABLE IF NOT EXISTS review_label (
  label_id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE CASCADE, run_id TEXT NOT NULL, node TEXT, label TEXT NOT NULL,
  label_list_version TEXT NOT NULL, rubric_scores TEXT, by_alias TEXT NOT NULL, reason TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS auto_wheel (
  wheel_id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, seq INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('performance', 'efficiency')), started_by TEXT NOT NULL,
  base_release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE CASCADE, base_revision TEXT NOT NULL, scope TEXT NOT NULL,
  goal TEXT NOT NULL, cell INTEGER NOT NULL CHECK (cell BETWEEN 1 AND 8),
  cell_state TEXT NOT NULL CHECK (cell_state IN ('running', 'waiting', 'human', 'stopped', 'done')), iteration INTEGER NOT NULL DEFAULT 1,
  settings TEXT NOT NULL, outcome TEXT, started_at REAL NOT NULL, ended_at REAL, UNIQUE (project_id, seq));
CREATE UNIQUE INDEX IF NOT EXISTS auto_wheel_one_open ON auto_wheel(project_id) WHERE ended_at IS NULL;
CREATE TABLE IF NOT EXISTS auto_wheel_log (
  log_id INTEGER PRIMARY KEY AUTOINCREMENT, wheel_id INTEGER NOT NULL REFERENCES auto_wheel(wheel_id) ON DELETE CASCADE,
  cell INTEGER NOT NULL, text TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS auto_hypothesis (
  hypothesis_id INTEGER PRIMARY KEY AUTOINCREMENT, wheel_id INTEGER NOT NULL REFERENCES auto_wheel(wheel_id) ON DELETE CASCADE,
  iteration INTEGER NOT NULL, batch TEXT NOT NULL CHECK (batch IN ('first', 'waiting')), batch_no INTEGER NOT NULL, node TEXT NOT NULL,
  knob TEXT NOT NULL, value TEXT, cheap_test TEXT, result TEXT, discarded_reason TEXT, source TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS auto_settings (
  project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE, data TEXT NOT NULL, updated_by TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fixed_flow (
  release_id TEXT PRIMARY KEY REFERENCES releases(id) ON DELETE CASCADE, pack_revision TEXT NOT NULL, items TEXT NOT NULL,
  knob_ranges TEXT NOT NULL, confirmed_by TEXT, confirmed_at REAL, updated_at REAL NOT NULL);
-- EV-035 CHG-188 (answer 045-01): the improvement wheel's rows (a row = one tried change, not a version — D-121) and the spend of
-- its paid steps (append only). Same tables as migration 0008. A row keeps its record when the version it became is deleted.
CREATE TABLE IF NOT EXISTS auto_candidate (
  candidate_id INTEGER PRIMARY KEY AUTOINCREMENT, wheel_id INTEGER NOT NULL REFERENCES auto_wheel(wheel_id) ON DELETE CASCADE,
  iteration INTEGER NOT NULL, hypothesis_ids TEXT NOT NULL, node TEXT NOT NULL, lines TEXT NOT NULL, revision TEXT,
  check_result TEXT NOT NULL DEFAULT '{}', cheap_test TEXT, table_result TEXT, executions INTEGER NOT NULL DEFAULT 0, rank INTEGER,
  outcome TEXT, recommendation TEXT, release_id TEXT REFERENCES releases(id) ON DELETE SET NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS auto_spend (
  spend_id INTEGER PRIMARY KEY AUTOINCREMENT, wheel_id INTEGER NOT NULL REFERENCES auto_wheel(wheel_id) ON DELETE CASCADE,
  step TEXT NOT NULL, calls INTEGER NOT NULL, tokens_in INTEGER NOT NULL, tokens_out INTEGER NOT NULL, usd REAL NOT NULL,
  model TEXT NOT NULL, created_at REAL NOT NULL);
-- D-486 덧 9 (migration 0013): every use of a sealed set outside a wheel — append only, no foreign key (a set's count never goes down)
CREATE TABLE IF NOT EXISTS sealed_use (
  use_id INTEGER PRIMARY KEY AUTOINCREMENT, set_sha12 TEXT NOT NULL, project_id TEXT NOT NULL, release_id TEXT NOT NULL, revision TEXT NOT NULL DEFAULT '',
  base_release_id TEXT NOT NULL, result TEXT NOT NULL CHECK (result IN ('통과', '떨어짐', '상한 멈춤', '설정 실패')),
  called_by TEXT NOT NULL CHECK (length(trim(called_by)) > 0), reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  counts TEXT NOT NULL DEFAULT '{}', usd REAL NOT NULL DEFAULT 0, usd_measured REAL NOT NULL DEFAULT 0, usd_estimated REAL NOT NULL DEFAULT 0,
  line TEXT NOT NULL, created_at REAL NOT NULL);
-- The contract's evaluation_run columns over runs (a table in the week-2 PostgreSQL move). data_release_id is what the Run
-- recorded from its execution manifest (runs.meta), NULL for a LangGraph Run or a Run without a manifest.
CREATE VIEW IF NOT EXISTS evaluation_run AS
  SELECT id AS evaluation_run_id, project_id, release_id, json_extract(meta, '$.label') AS label,
         json_extract(meta, '$.input.kind') AS input_kind, json_extract(meta, '$.input.id') AS input_id,
         json_extract(meta, '$.input.suite_revision') AS suite_revision, json_extract(meta, '$.input.dataset_hash') AS dataset_hash,
         json_extract(meta, '$.data_release_id') AS data_release_id, status, created_at
  FROM runs;
"""
COLORS = ["#3056d3", "#0e9384", "#b54708", "#7a5af8", "#c11574", "#475467"]
TABLES = ("projects", "validation_data", "releases", "runs", "run_cases", "input_sets", "bundles", "briefs", "verdicts", "pack_releases",
          "eval_suites", "run_events", "decision", "deployment", "evaluation_case", "release_edit", "node_layout", "case_promotion")
IDENTITY = {"decision": "decision_id", "release_edit": "edit_id", "case_promotion": "promotion_id"}   # SQLite AUTOINCREMENT ↔ PG identity
# EV-030 CHG-150: the auto cycle's tables (migration 0007). Not part of the start check — a PostgreSQL database without them still
# starts the server, with the auto cycle off (Store.auto_ready) — and not moved by move-to-pg (they did not exist then).
AUTO_TABLES = ("ops_diagnosis", "review_label", "auto_wheel", "auto_wheel_log", "auto_hypothesis", "auto_settings", "fixed_flow")
# EV-035 CHG-188: the wheel's rows and spend (migration 0008) — like AUTO_TABLES, outside the start check (Store.auto_main_ready)
AUTO_MAIN_TABLES = ("auto_candidate", "auto_spend")
IDENTITY.update({"auto_candidate": "candidate_id", "auto_spend": "spend_id"})
# D-486 덧 9: the sealed set's uses outside a wheel (migration 0013) — outside the start check too (Store.sealed_ready)
SEALED_TABLES = ("sealed_use",)
IDENTITY.update({"sealed_use": "use_id"})
IDENTITY.update({"ops_diagnosis": "diagnosis_id", "review_label": "label_id", "auto_wheel": "wheel_id", "auto_wheel_log": "log_id",
                 "auto_hypothesis": "hypothesis_id"})
MOVED_MARK = "llmops.db.moved-to-pg"   # written next to llmops.db by move-to-pg (CHG-133); a SQLite store does not start beside it


class StoreStartError(RuntimeError):
    """The store cannot start. `kind`: unknown_backend · moved · no_dsn · unreachable · denied · no_schema. The message never holds a
    DSN or a password."""
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


class PgError(sqlite3.DatabaseError):
    """A PostgreSQL store error (the database's own message, without the DSN). Under sqlite3.DatabaseError so a caller written for
    the SQLite store treats it the same way."""


class PgIntegrityError(PgError, sqlite3.IntegrityError):
    """Same key, the one open edit, a check, or an append-only refusal — what sqlite3.IntegrityError means for the SQLite store."""


INTEGRITY_ERRORS = (sqlite3.IntegrityError, PgIntegrityError)   # backend-neutral names the app catches (409 and the like)
DB_ERRORS = (sqlite3.Error, PgError)
START_REASON = {"unknown_backend": "모르는 저장소 값", "moved": "기록이 PostgreSQL로 옮겨졌음(llmops.db.moved-to-pg)", "no_dsn": "연결 설정 없음",
                "unreachable": "DB에 연결할 수 없음", "denied": "권한 없음", "no_schema": "llmops 표 없음(migration 0005 전)"}
APPEND_ONLY = ("verdicts", "decision", "deployment", "evaluation_case", "case_promotion")   # llmops_app: SELECT · INSERT only
_APPEND_ONLY_CHANGE = re.compile(r"\s*(UPDATE|DELETE FROM)\s+llmops\.(" + "|".join(APPEND_ONLY) + r")\b")
_PG_TABLE = re.compile(r"\b(FROM|INTO|UPDATE|JOIN)\s+(" + "|".join(TABLES + AUTO_TABLES + AUTO_MAIN_TABLES + SEALED_TABLES + ("evaluation_run",)) + r")\b")


def _pg_sql(sql):
    if "%" in sql:
        raise PgError("SQL에 % 글자는 쓰지 않습니다")
    return re.sub(r"\browid\b", "ord", _PG_TABLE.sub(lambda m: f"{m.group(1)} llmops.{m.group(2)}", sql)).replace("?", "%s")


class PgRow:
    """A row read like sqlite3.Row: by column name, by position, and dict(row)."""
    __slots__ = ("_k", "_v")

    def __init__(self, names, values):
        self._k, self._v = names, values

    def keys(self):
        return list(self._k)

    def __getitem__(self, i):
        return self._v[i] if isinstance(i, int) else self._v[self._k.index(i)]

    def __iter__(self):
        return iter(self._v)

    def __len__(self):
        return len(self._v)


def _row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: PgRow(names, values)


class _PgCursor:
    def __init__(self, cur, lastrowid=None):
        self._c, self.lastrowid = cur, lastrowid

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    def __iter__(self):
        return iter(self._c.fetchall())


UNAVAILABLE = {"NOT_SAVED": "공유 DB 연결이 끊겨 저장하지 않았습니다 — 다시 하세요",
               "UNKNOWN": "공유 DB 연결이 끊겨 저장됐는지 모릅니다 — 화면에서 확인한 뒤 필요하면 다시 하세요",
               "DOWN": "공유 DB에 닿지 않습니다({why}) — 잠시 뒤 다시 하세요"}
LOST_STATES = ("57P01", "57P02", "57P03")   # admin shutdown · crash shutdown (a restart, as at 18:02) · not accepting connections yet
RECONNECT_GAP_S = 2.0                       # after a failed reconnect, statements answer DOWN at once for this long (A1-N2)
_READ = re.compile(r"\s*(SELECT|WITH)\b", re.I)
_WRITE_WORD = re.compile(r"\b(INSERT|UPDATE|DELETE)\b", re.I)


class StoreUnavailable(PgError):
    """EV-057 CHG-238 · 239: the shared PostgreSQL connection was lost. kind NOT_SAVED (cut inside a transaction — nothing kept) ·
    UNKNOWN (cut after a write or COMMIT was sent — the product cannot know) · DOWN (cannot reconnect). The app answers 503 with the
    text; it never holds a DSN, a password or a server message."""
    def __init__(self, kind, why=""):
        super().__init__(UNAVAILABLE[kind].format(why=why))
        self.kind = kind


class PgDB:
    """The part of a sqlite3 connection the Store uses, over one psycopg connection in autocommit: execute(sql, args) and
    `with db:` = one transaction (commit, or roll back on an error). Used only under the Store's lock.
    EV-057 CHG-238: a connection the shared PostgreSQL cut (a restart) is replaced on the next statement or outermost BEGIN; only what
    sent nothing yet (a BEGIN) or changes nothing (a SELECT outside a transaction) is tried once more — any other cut statement is never
    sent again and raises StoreUnavailable. The DSN stays inside this object."""
    def __init__(self, conn, dsn=None):
        self.conn, self._tx, self._dsn, self._down = conn, [], dsn, (0.0, "")

    def _lost(self, e):
        """A1-N1: the connection is gone — sqlstate 08xxx · 57P01 ~ 57P03, or an OperationalError · InterfaceError that left it broken."""
        import psycopg
        state = getattr(e, "sqlstate", None) or ""
        if state.startswith("08") or state in LOST_STATES:
            return True
        return isinstance(e, (psycopg.OperationalError, psycopg.InterfaceError)) and bool(self.conn.closed or self.conn.broken)

    def _reconnect(self):
        until, why = self._down
        if time.monotonic() < until:
            raise StoreUnavailable("DOWN", why)
        try:
            self.conn.close()   # the old one quietly (A2-N3)
        except Exception:   # noqa: BLE001 - already gone
            pass
        try:
            if not self._dsn:
                raise StoreStartError("no_dsn", START_REASON["no_dsn"])
            self.conn = pg_connect(self._dsn)
        except StoreStartError as e:
            self._down = (time.monotonic() + RECONNECT_GAP_S, START_REASON.get(e.kind, e.kind))   # counted from the failure's end (A2-N4)
            raise StoreUnavailable("DOWN", self._down[1]) from None
        self._down = (0.0, "")

    def _ensure(self):
        if self.conn.closed or self.conn.broken:
            self._reconnect()

    def _run(self, q, args, ident):
        import psycopg
        try:
            cur = self.conn.execute(q, tuple(args))
            return _PgCursor(cur, cur.fetchone()[0] if ident else None)
        except (psycopg.errors.IntegrityError, psycopg.errors.RaiseException) as e:   # RaiseException: the append-only trigger
            raise PgIntegrityError(_pg_message(e)) from None
        except psycopg.errors.InsufficientPrivilege as e:
            # llmops_app's grants refuse UPDATE · DELETE on the append-only tables before the trigger (change contract permission
            # table): the SQLite trigger's refusal. Any other refused statement stays a plain store error.
            if _APPEND_ONLY_CHANGE.match(q):
                raise PgIntegrityError(f"append only — {_pg_message(e)}") from None
            raise PgError(_pg_message(e)) from None
        except psycopg.Error as e:
            if self._lost(e):
                raise _Lost() from None
            raise PgError(_pg_message(e)) from None

    def execute(self, sql, args=()):
        q = _pg_sql(sql)
        m = re.match(r"\s*INSERT INTO llmops\.(\w+)", q)
        ident = IDENTITY.get(m.group(1)) if m else None
        if ident:
            q += f" RETURNING {ident}"
        if self._tx:   # inside a transaction: a cut throws the whole transaction away on the server
            try:
                return self._run(q, args, ident)
            except _Lost:
                raise StoreUnavailable("NOT_SAVED") from None
        self._ensure()
        try:
            return self._run(q, args, ident)
        except _Lost:
            if not (_READ.match(q) and not _WRITE_WORD.search(q)):   # a write was sent: never again (PRES-260)
                raise StoreUnavailable("UNKNOWN") from None
        self._reconnect()
        try:
            return self._run(q, args, ident)
        except _Lost:
            raise StoreUnavailable("DOWN", "다시 이은 뒤에도 끊김") from None

    def commit(self):   # autocommit: a statement outside `with db:` is already committed (sqlite3 callers may still call it)
        pass

    def __enter__(self):
        import psycopg
        outer = not self._tx
        if outer:
            self._ensure()
        tx = self.conn.transaction()
        try:
            tx.__enter__()
        except psycopg.Error as e:
            if not (outer and self._lost(e)):
                raise
            self._reconnect()   # the BEGIN sent nothing yet: once more on a new connection
            tx = self.conn.transaction()
            try:
                tx.__enter__()
            except psycopg.Error as e2:
                if self._lost(e2):
                    raise StoreUnavailable("NOT_SAVED") from None
                raise
        self._tx.append(tx)
        return self

    def __exit__(self, et, ev, tb):
        import psycopg
        tx = self._tx.pop()
        outer = not self._tx
        if et is None and outer and (self.conn.closed or self.conn.broken):   # A2-N1: never a quiet end without a COMMIT
            try:
                tx.__exit__(None, None, None)
            except Exception:   # noqa: BLE001
                pass
            raise StoreUnavailable("NOT_SAVED")
        try:
            return tx.__exit__(et, ev, tb)
        except psycopg.Error as e:
            if et is not None:   # the rollback of a failing block on a cut connection: keep the block's own error
                return False
            if self._lost(e):
                raise StoreUnavailable("UNKNOWN" if outer else "NOT_SAVED") from None
            raise


class _Lost(Exception):
    """PgDB._run's signal that the connection was cut (never leaves PgDB)."""


def _pg_message(e):
    diag = getattr(e, "diag", None)
    return (getattr(diag, "message_primary", None) or type(e).__name__)[:300]


def pg_connect(dsn):
    """llmops_app's connection to the llmops schema. Refused with a reason kind only (no DSN, no password, no server message)."""
    import psycopg
    if not dsn:
        raise StoreStartError("no_dsn", START_REASON["no_dsn"])
    try:
        conn = psycopg.connect(dsn, autocommit=True, connect_timeout=5, row_factory=_row_factory)
    except psycopg.OperationalError as e:
        kind = "denied" if re.search(r"authentication failed|permission denied|does not exist|not permitted", str(e)) else "unreachable"
        raise StoreStartError(kind, START_REASON[kind]) from None
    except (psycopg.Error, ValueError):   # a DSN that is not one
        raise StoreStartError("unreachable", START_REASON["unreachable"]) from None
    try:
        n = conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace "
                         "WHERE s.nspname = 'llmops' AND c.relkind = 'r' AND c.relname = ANY(%s)", (list(TABLES),)).fetchone()[0]
        if n != len(TABLES):
            raise StoreStartError("no_schema", START_REASON["no_schema"])
        if not conn.execute("SELECT has_schema_privilege('llmops', 'USAGE') AND has_table_privilege('llmops.projects', 'INSERT')").fetchone()[0]:
            raise StoreStartError("denied", START_REASON["denied"])
    except StoreStartError:
        conn.close()
        raise
    except psycopg.Error:
        conn.close()
        raise StoreStartError("denied", START_REASON["denied"]) from None
    return conn


def open_store(settings):
    """The Store the settings name (LLMOPS_STORE): SQLite by default, PostgreSQL with `pg` (DSN = the DB env file's LLMOPS_APP_DSN line)."""
    if getattr(settings, "store", "sqlite") == "pg":
        from .opsruns import app_dsn
        return Store(settings.data_dir, pg_dsn=app_dsn(settings.db_env_file) or "")
    return Store(settings.data_dir)



def suite_key(s):
    """What makes two evaluation suites the same content (EV-012 CHG-69/71)."""
    return (s.get("pack_id"), s.get("graph"), s.get("suite_revision"), s.get("dataset_hash"))


class Store:
    def __init__(self, data_dir: Path, *, pg_dsn=None):
        data_dir.mkdir(parents=True, exist_ok=True)
        if pg_dsn is not None:   # EV-029 CHG-130: the shared PostgreSQL llmops schema (made by migration 0005, never here)
            self.backend = "pg"
            self.db = PgDB(pg_connect(pg_dsn), pg_dsn)   # EV-057: the DSN stays inside PgDB, for a reconnect after a restart
        else:
            if (data_dir / MOVED_MARK).exists():   # the records went to PostgreSQL: never write the old file quietly
                raise StoreStartError("moved", START_REASON["moved"])
            self.backend = "sqlite"
            self.db = sqlite3.connect(data_dir / "llmops.db", check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys = ON")
            self.db.executescript(SCHEMA)
            self._ledger_columns()
        self.ledger_v2 = self._ledger_v2()   # EV-044 CHG-226: model columns · data_release_auto only when migration 0010 is here
        self.auto_ready = self._auto_tables_present()   # EV-030: the auto cycle runs only when its tables are here (V-DIAG-OFF)
        self.auto_main_ready = self._auto_tables_present(AUTO_MAIN_TABLES)   # EV-035: the wheel goes past ③ only with these
        self.sealed_ready = self._auto_tables_present(SEALED_TABLES)   # D-486 덧 9: the sealed test outside a wheel only with it
        self.lock = threading.RLock()  # one connection shared by API threads and run executor threads
        self.redact = lambda s: s     # set by the app: masks the LLMOps key values and common key formats (B-08)

    def _ledger_columns(self):
        """EV-044 (SQLite): an older llmops.db gets the two model columns (added at the end, as in PostgreSQL 0010) and the
        four-column unique index. Its three-column primary key and its old decision CHECK stay, so it counts as 'before 0010'."""
        have = {r[1] for r in self.db.execute("PRAGMA table_info(deployment)")}
        for col in ("model_release_id", "model_bindings"):
            if col not in have:
                self.db.execute(f"ALTER TABLE deployment ADD COLUMN {col} TEXT")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS deployment_key ON deployment "
                        "(deployment_id, pack_revision, data_release_id, COALESCE(model_release_id, ''))")
        self.db.commit()

    def _ledger_v2(self):
        """EV-044 CHG-226: True when the ledger is past migration 0010 — the deployment model columns are there and decision accepts
        data_release_auto. Before it (the shared database at 0009, an older SQLite file) the store writes the ledger as it did."""
        if self.backend == "sqlite":
            sql = (self.db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'decision'").fetchone() or [""])[0]
            pk = any(r[5] for r in self.db.execute("PRAGMA table_info(deployment)"))
            return "data_release_auto" in (sql or "") and not pk
        cols = self.db.conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'llmops' AND table_name = 'deployment' "
                                    "AND column_name IN ('model_release_id', 'model_bindings')").fetchone()[0]
        chk = self.db.conn.execute("SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                                   "JOIN pg_namespace s ON s.oid = t.relnamespace WHERE s.nspname = 'llmops' AND t.relname = 'decision' "
                                   "AND c.conname = 'decision_decision_check'").fetchone()
        return cols == 2 and bool(chk) and "data_release_auto" in chk[0]

    def _auto_tables_present(self, names=AUTO_TABLES):
        if self.backend == "sqlite":
            return True   # SCHEMA made them
        n = self.db.conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace "
                                 "WHERE s.nspname = 'llmops' AND c.relkind = 'r' AND c.relname = ANY(%s)", (list(names),)).fetchone()[0]
        return n == len(names)

    def _r(self, obj):
        """Every text the store writes passes the mask first (AC-26: before recording), wherever it came from — Agent output,
        LLM output, or text a person typed (verdict reasons, briefs, names, generation descriptions)."""
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {k: self._r(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._r(v) for v in obj]
        return obj

    def _j(self, obj):
        return json.dumps(self._r(obj), ensure_ascii=False)

    def create_project(self, name, description, scoring, rule, data=None):
        pid = "p-" + uuid.uuid4().hex[:8]
        now = time.time()
        with self.lock, self.db:
            self.db.execute("INSERT INTO projects VALUES (?,?,?,?,?,?)", (pid, self._r(name), self._r(description), self._j(scoring), self._j(rule), now))
            if data:
                self.db.execute("INSERT INTO validation_data VALUES (?,?,?,?)", (pid, self._r(data["name"]), self._j(data["cases"]), now))
        return self.project(pid)

    def add_validation_data(self, pid, name, cases):
        with self.lock, self.db:
            self.db.execute("INSERT INTO validation_data VALUES (?,?,?,?)", (pid, self._r(name), self._j(cases), time.time()))
        return self.project(pid)

    def add_release(self, pid, name, how, path, code_hash, graph, env_excluded=()):
        rid = "rel-" + uuid.uuid4().hex[:8]
        now = time.time()
        with self.lock, self.db:
            n = self.db.execute("SELECT COUNT(*) FROM releases WHERE project_id=?", (pid,)).fetchone()[0]
            self.db.execute("INSERT INTO releases (id, project_id, name, how, path, color, code_hash, graph, env_excluded, last_run_code, created_at, updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,NULL,?,?)",
                            (rid, pid, self._r(name), how, str(path), COLORS[n % len(COLORS)], code_hash, self._j(graph), self._j(list(env_excluded)), now, now))
        return self.release(rid)

    def add_suite(self, pid, suite):
        """An evaluation suite of the Project (EV-002 CHG-11): its cases, revisions and the data-dir copy."""
        sid = "suite-" + uuid.uuid4().hex[:8]
        with self.lock, self.db:
            self.db.execute("INSERT INTO eval_suites VALUES (?,?,?,?)", (sid, pid, self._j(suite), time.time()))
        return self.suite(sid)

    def add_suite_unique(self, pid, suite):
        """EV-012 CHG-69: add the suite unless the Project already has one with the same content (same Pack id, entrypoint,
        suite_revision and dataset_hash). Checked and written under one lock, so concurrent requests add it once.
        Returns (new record, None) or (None, the earliest same-content record)."""
        with self.lock:
            same = next((s for s in self.suites(pid) if suite_key(s) == suite_key(suite)), None)
            return (None, same) if same else (self.add_suite(pid, suite), None)

    def set_suite_copy(self, sid, copy):
        with self.lock, self.db:
            d = json.loads(self.db.execute("SELECT data FROM eval_suites WHERE id=?", (sid,)).fetchone()["data"])
            d["copy"] = str(copy)
            self.db.execute("UPDATE eval_suites SET data=? WHERE id=?", (self._j(d), sid))
        return self.suite(sid)

    def delete_suite(self, sid):
        with self.lock, self.db:
            self.db.execute("DELETE FROM eval_suites WHERE id=?", (sid,))

    def suite(self, sid):
        with self.lock:
            r = self.db.execute("SELECT * FROM eval_suites WHERE id=?", (sid,)).fetchone()
            return self._suite_record(r) if r else None

    @staticmethod
    def _suite_record(r):
        return {"id": r["id"], "project_id": r["project_id"], **json.loads(r["data"]), "created_at": r["created_at"]}

    def suites(self, pid):
        with self.lock:
            return [self.suite(r["id"]) for r in self.db.execute("SELECT id FROM eval_suites WHERE project_id=? ORDER BY created_at, rowid", (pid,))]

    def add_pack_release(self, rid, pack):
        """Agent Pack facts of a release (EV-002 CHG-02/CHG-04): pack id·version, revision, raw file hashes, work folder,
        execution config and release bindings. A release without this row is a legacy Python release."""
        with self.lock, self.db:
            self.db.execute("INSERT INTO pack_releases VALUES (?,?)", (rid, self._j(pack)))
        return self.release(rid)

    def update_release_code(self, rid, code_hash, graph):
        with self.lock, self.db:
            self.db.execute("UPDATE releases SET code_hash=?, graph=?, updated_at=? WHERE id=?", (code_hash, self._j(graph), time.time(), rid))
        return self.release(rid)

    def release(self, rid):
        with self.lock:
            r = self.db.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone()
            if not r:
                return None
            pk = self.db.execute("SELECT data FROM pack_releases WHERE release_id=?", (rid,)).fetchone()
            return self._release_record(r, pk["data"] if pk else None)

    @staticmethod
    def _release_record(r, pack_data):
        return {"id": r["id"], "project_id": r["project_id"], "name": r["name"], "how": r["how"], "path": r["path"], "color": r["color"],
                "code_hash": r["code_hash"], "code": r["code_hash"][:4], "graph": json.loads(r["graph"]), "envExcluded": json.loads(r["env_excluded"]),
                "last_run_code": r["last_run_code"], "changed_since_run": bool(r["last_run_code"]) and r["last_run_code"] != r["code_hash"],
                "sourceKind": "agent_pack" if pack_data is not None else "legacy_python", "pack": json.loads(pack_data) if pack_data is not None else None}

    def releases(self, pid):
        with self.lock:
            return [self.release(r["id"]) for r in self.db.execute("SELECT id FROM releases WHERE project_id=? ORDER BY created_at", (pid,))]

    def set_last_run_code(self, rid, code_hash):
        with self.lock, self.db:
            self.db.execute("UPDATE releases SET last_run_code=? WHERE id=?", (code_hash, rid))

    # runs (B-03 owns records; the executor (B-05) writes through these methods only)
    # ------------------------------------------------------------------ deployment registrations (EV-019 CHG-94)
    def _ledger_row_exists(self, deployment_id, pack_revision, data_release_id, model_release_id):
        """The ledger key (decision contract 6): three columns before migration 0010, four after it (EV-044 CHG-219) — the model
        release compared null-safe, as the unique index does (COALESCE(model_release_id, ''))."""
        if not self.ledger_v2:
            return self.db.execute("SELECT 1 FROM deployment WHERE deployment_id=? AND pack_revision=? AND data_release_id=?",
                                   (deployment_id, pack_revision, data_release_id)).fetchone() is not None
        return self.db.execute("SELECT 1 FROM deployment WHERE deployment_id=? AND pack_revision=? AND data_release_id=? "
                               "AND COALESCE(model_release_id, '') = COALESCE(?, '')",
                               (deployment_id, pack_revision, data_release_id, model_release_id)).fetchone() is not None

    def _insert_ledger_row(self, *, deployment_id, pack_id, pack_revision, data_release_id, release_bindings, runtime_version, pack_commit,
                           pack_tag, config_sha256, decision_id, created_at, model_release_id=None, model_bindings=None):
        """One deployment row; the model columns only past migration 0010 (EV-044 CHG-226 — before it they do not exist)."""
        cols = ["deployment_id", "pack_id", "pack_revision", "data_release_id", "release_bindings", "runtime_version", "pack_commit",
                "pack_tag", "config_sha256", "decision_id", "created_at"]
        vals = [deployment_id, pack_id, pack_revision, data_release_id, json.dumps(release_bindings, ensure_ascii=False, sort_keys=True),
                runtime_version, pack_commit, pack_tag, config_sha256, decision_id, created_at]
        if self.ledger_v2:
            cols += ["model_release_id", "model_bindings"]
            vals += [model_release_id, None if model_bindings is None else json.dumps(model_bindings, ensure_ascii=False, sort_keys=True)]
        self.db.execute(f"INSERT INTO deployment ({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)

    def approved_packs(self):
        """EV-049 CHG-224 (D-280 (2), D-326 Q-2): ({pack id: first deploy_approved decision_id}, [decision ids whose Pack cannot be
        told]). A deploy approval names its Pack by any of four ways — ① its evidence release's Pack id ② the pack_id of a ledger row
        written by it ③ its deployment id before '@' (exact) ④ the pack_id of the ledger rows its evidence target_base names (same
        deployment id · data bundle · config hash; every approval the app makes carries it, and ledger rows are never removed). A
        value of the wrong type only closes that way (A2-N2). Known limit (A2-N3): an approval made outside the app without target_base,
        whose release was deleted, with no row yet and a deployment id naming another Pack, closes that other Pack, not its own."""
        with self.lock:
            rows = [dict(r) for r in self.db.execute("SELECT decision_id, subject_id, evidence FROM decision WHERE decision = 'deploy_approved' ORDER BY decision_id")]
            ledger = [dict(r) for r in self.db.execute("SELECT deployment_id, pack_id, data_release_id, config_sha256, decision_id FROM deployment")]
            packs, unknown = {}, []
            for d in rows:
                found = set()
                try:
                    ev = json.loads(d["evidence"] or "null")
                except ValueError:
                    ev = None
                ev = ev if isinstance(ev, dict) else {}
                rid = ev.get("release")
                if isinstance(rid, str):
                    try:
                        found.add(((self.release(rid) or {}).get("pack") or {}).get("id"))
                    except Exception:  # noqa: BLE001 - a release that cannot be read only closes this way
                        pass
                found |= {x["pack_id"] for x in ledger if x["decision_id"] == d["decision_id"]}
                sub = d["subject_id"]
                if isinstance(sub, str) and "@" in sub:
                    found.add(sub.split("@", 1)[0])
                tb = ev.get("target_base")
                if isinstance(tb, dict) and isinstance(tb.get("deployment_id"), str):
                    found |= {x["pack_id"] for x in ledger if x["deployment_id"] == tb["deployment_id"]
                              and x["data_release_id"] == tb.get("data_release_id") and x["config_sha256"] == tb.get("config_sha256")}
                found = {f for f in found if isinstance(f, str) and f}
                if not found:
                    unknown.append(d["decision_id"])
                for f in found:
                    packs.setdefault(f, d["decision_id"])
            return packs, unknown

    def add_deployment(self, *, deployment_id, pack_id, pack_revision, data_release_id, release_bindings, runtime_version, config_sha256,
                       decided_by, reason, pack_commit=None, pack_tag=None, dev=False, model_release_id=None, model_bindings=None):
        """decision(deploy_bootstrap) + deployment row in one transaction (decision contract 6). The same key twice is refused (three
        columns, four past migration 0010 — EV-044); a production id is refused once its Pack has a deploy_approved decision (the
        bootstrap closes itself for that Pack — EV-049; a deploy approval whose Pack cannot be told closes every Pack); dev ids stay open."""
        now = time.time()
        with self.lock, self.db:
            if self._ledger_row_exists(deployment_id, pack_revision, data_release_id, model_release_id):
                raise ValueError(f"이미 등록됨: {deployment_id}, {pack_revision[:23]}, {data_release_id}"
                                 + (f", {model_release_id}" if self.ledger_v2 and model_release_id else ""))
            if not dev:
                packs, unknown = self.approved_packs()
                if unknown:
                    raise ValueError(f"Pack을 정할 수 없는 배포 승인(decision {unknown[0]})이 있어 모든 Pack의 운영 bootstrap 등록을 닫았습니다"
                                     "(deploy_approved로만 등록)")
                if pack_id in packs:
                    raise ValueError(f"이 Pack({pack_id})은 배포 승인 흐름이 생겨 운영 배포의 bootstrap 등록은 닫혔습니다"
                                     f"(decision {packs[pack_id]}, deploy_approved로만 등록)")
            cur = self.db.execute("INSERT INTO decision (subject_kind, subject_id, decision, reason, decided_by, evidence, decided_at)"
                                  " VALUES ('deployment', ?, 'deploy_bootstrap', ?, ?, '[]', ?)", (deployment_id, self.redact(reason), decided_by, now))
            did = cur.lastrowid
            self._insert_ledger_row(deployment_id=deployment_id, pack_id=pack_id, pack_revision=pack_revision, data_release_id=data_release_id,
                                    release_bindings=release_bindings, runtime_version=runtime_version, pack_commit=pack_commit, pack_tag=pack_tag,
                                    config_sha256=config_sha256, decision_id=did, created_at=now, model_release_id=model_release_id,
                                    model_bindings=model_bindings)
        return {"deployment_id": deployment_id, "pack_id": pack_id, "pack_revision": pack_revision, "data_release_id": data_release_id,
                "runtime_version": runtime_version, "decision_id": did}

    def add_data_release_auto(self, *, deployment_id, pack_revision, data_release_id, store_name, previous, version, manifest_sha256,
                              config_sha256, evidence, model_given, model_release_id=None):
        """EV-044 CHG-222 (D-260 (1) · D-283 (3), R12): decision(data_release_auto, by r12-auto) + one ledger row in one transaction —
        the same deployment id and revision, one vector DB store moved from `previous` to `version`. The base row is the last row of
        that id and revision whose store binding is `previous`; when the deploy side said which model runs (model_given), only rows
        with that model (null-safe) — so a model that is not running is never written; otherwise the new row's model is unknown (null).
        Everything else comes from the base row (the model cannot change on this path — H-3). Returns (row dict, written). Raises
        ValueError with the reason; nothing is written then."""
        from packrt.trace import data_release_id as drs_of
        if not self.ledger_v2:
            raise ValueError("migration 0010이 아직 없습니다(공유 DB면 0010 적용 뒤) — data_release_auto 줄을 쓰지 않음")
        now = time.time()
        with self.lock, self.db:
            cands = []
            for r in self.db.execute("SELECT * FROM deployment WHERE deployment_id=? AND pack_revision=? ORDER BY created_at",
                                     (deployment_id, pack_revision)).fetchall():
                r = dict(r)
                b = json.loads(r["release_bindings"] or "{}")
                if (b.get(store_name) or {}).get("release_id") == previous:
                    cands.append((r, b))
            if not cands:
                raise ValueError(f"기준 줄이 없습니다: {deployment_id} · {pack_revision[:23]}에 store '{store_name}' = {previous}인 장부 줄 없음")
            if model_given:
                cands = [(r, b) for r, b in cands if (r.get("model_release_id") or None) == (model_release_id or None)]
                if not cands:
                    fix = "--dev bootstrap으로 그 모델의 장부 줄을 먼저 등록" if deployment_id.endswith("@dev-local") else "배포 승인 필요(H-3)"
                    raise ValueError(f"장부에 없는 모델로 돌고 있습니다({model_release_id or '모델 없음'}) — {fix}")
            base, binds = cands[-1]
            binds = {**binds, store_name: {"release_id": version, "sha256": manifest_sha256}}
            drs = drs_of({k: (v or {}).get("release_id") for k, v in binds.items()})
            if drs != data_release_id:
                raise ValueError(f"자료 묶음이 맞지 않습니다: 다시 계산한 값 {drs} ≠ --drs {data_release_id}")
            mrs = (model_release_id or None) if model_given else None
            mbind = (json.loads(base["model_bindings"]) if base.get("model_bindings") else None) if model_given else None
            key = (deployment_id, pack_revision, data_release_id, mrs)
            if self._ledger_row_exists(*key):
                old = self.db.execute("SELECT * FROM deployment WHERE deployment_id=? AND pack_revision=? AND data_release_id=? "
                                      "AND COALESCE(model_release_id, '') = COALESCE(?, '')", key).fetchone()
                return dict(old), False
            n = len(evidence.get("reports") or [])
            reason = f"자동 반영(R12): 보고서 {n}건 · {store_name} {previous} → {version}"
            did = self.db.execute("INSERT INTO decision (subject_kind, subject_id, decision, reason, decided_by, evidence, decided_at)"
                                  " VALUES ('deployment', ?, 'data_release_auto', ?, 'r12-auto', ?, ?)",
                                  (deployment_id, self.redact(reason), json.dumps({**evidence, "base_decision_id": base["decision_id"]},
                                                                                  ensure_ascii=False), now)).lastrowid
            self._insert_ledger_row(deployment_id=deployment_id, pack_id=base["pack_id"], pack_revision=pack_revision, data_release_id=data_release_id,
                                    release_bindings=binds, runtime_version=base["runtime_version"], pack_commit=base["pack_commit"],
                                    pack_tag=base["pack_tag"], config_sha256=config_sha256, decision_id=did, created_at=now,
                                    model_release_id=mrs, model_bindings=mbind)
            row = dict(self.db.execute("SELECT * FROM deployment WHERE decision_id=?", (did,)).fetchone())
        return row, True

    def add_deploy_approval(self, *, deployment_id, decided_by, reason, evidence):
        """EV-022 CHG-113: decision(deploy_approved) — one row, append only (decision contract 3). subject = the deployment it
        approves; evidence carries the release, version, revisions, change id and request file."""
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO decision (subject_kind, subject_id, decision, reason, decided_by, evidence, decided_at)"
                                  " VALUES ('deployment', ?, 'deploy_approved', ?, ?, ?, ?)",
                                  (deployment_id, self.redact(reason), decided_by, json.dumps(evidence, ensure_ascii=False), time.time()))
            return cur.lastrowid

    def add_verdict_with_deploy_approval(self, uid, verdict, *, deployment_id, decided_by, reason, evidence):
        """EV-035 CHG-186: '배포 승인' on a 승인 대기 version — the person's adopt verdict (append only) and decision(deploy_approved) in
        one transaction: both or neither."""
        with self.lock, self.db:
            seq = self.db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM verdicts WHERE run_id=?", (uid,)).fetchone()[0]
            self.db.execute("INSERT INTO verdicts VALUES (?,?,?)", (uid, seq, self._j(verdict)))
            cur = self.db.execute("INSERT INTO decision (subject_kind, subject_id, decision, reason, decided_by, evidence, decided_at)"
                                  " VALUES ('deployment', ?, 'deploy_approved', ?, ?, ?, ?)",
                                  (deployment_id, self.redact(reason), decided_by, json.dumps(evidence, ensure_ascii=False), time.time()))
            return cur.lastrowid

    def add_rollback(self, *, deployment_id, decided_by, reason, evidence):
        """EV-SUI-01 CHG-S03 (D-140): decision(deploy_rolled_back) — one row, append only. subject = the deployment of the version it
        switches back to; evidence carries that release, the release it leaves, the rollback id and the revision. No ledger row, no
        request file: the deploy side's `deploy-check rollback` takes the id and reports through its status file."""
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO decision (subject_kind, subject_id, decision, reason, decided_by, evidence, decided_at)"
                                  " VALUES ('deployment', ?, 'deploy_rolled_back', ?, ?, ?, ?)",
                                  (deployment_id, self.redact(reason), decided_by, json.dumps(evidence, ensure_ascii=False), time.time()))
            return cur.lastrowid

    def rollbacks(self):
        with self.lock:
            return [{**dict(r), "evidence": json.loads(r["evidence"])} for r in
                    self.db.execute("SELECT * FROM decision WHERE decision = 'deploy_rolled_back' ORDER BY decision_id")]

    def deploy_approvals(self):
        with self.lock:
            return [{**dict(r), "evidence": json.loads(r["evidence"])} for r in
                    self.db.execute("SELECT * FROM decision WHERE decision = 'deploy_approved' ORDER BY decision_id")]

    def last_decided_by(self):
        """D-456: the alias of the last decision a person made (a verdict · a deploy approval · a rollback) — the decider fields start
        with it; the automatic and bootstrap rows are not a person's."""
        with self.lock:
            r = self.db.execute("SELECT decided_by FROM decision WHERE decision IN ('change_approved', 'hold', 'rejected', 'deploy_approved', 'deploy_rolled_back')"
                                " ORDER BY decided_at DESC, decision_id DESC LIMIT 1").fetchone()
        return r["decided_by"] if r else None

    def decision(self, decision_id):
        with self.lock:
            r = self.db.execute("SELECT * FROM decision WHERE decision_id=?", (decision_id,)).fetchone()
            return {**dict(r), "evidence": json.loads(r["evidence"])} if r else None

    def register_deployment(self, *, decision_id, deployment_id, pack_id, pack_revision, data_release_id, release_bindings, runtime_version,
                            config_sha256, pack_commit=None, pack_tag=None, model_release_id=None, model_bindings=None):
        """EV-022 S-20: LLMOps' own ledger row for a deploy approval, written once the Pack session reported `tagged`. Add only:
        nothing is written and False comes back when this approval already wrote a row (EV-044 A2-B1: a row written before migration
        0010 without model columns is not written again after it) or when a row with the same key is there. The bootstrap rules of
        add_deployment stay as they are (PRES-119)."""
        with self.lock, self.db:
            d = self.db.execute("SELECT decision FROM decision WHERE decision_id=?", (decision_id,)).fetchone()
            if not d or d["decision"] != "deploy_approved":
                raise ValueError("배포 기록 줄은 배포 승인 결정에만 묶습니다")
            if self.db.execute("SELECT 1 FROM deployment WHERE decision_id=?", (decision_id,)).fetchone() or \
                    self._ledger_row_exists(deployment_id, pack_revision, data_release_id, model_release_id):
                return False
            self._insert_ledger_row(deployment_id=deployment_id, pack_id=pack_id, pack_revision=pack_revision, data_release_id=data_release_id,
                                    release_bindings=release_bindings, runtime_version=runtime_version, pack_commit=pack_commit, pack_tag=pack_tag,
                                    config_sha256=config_sha256, decision_id=decision_id, created_at=time.time(),
                                    model_release_id=model_release_id, model_bindings=model_bindings)
            return True

    def deployments(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM deployment ORDER BY created_at")]

    def registration_check(self, run):
        """EV-029 CHG-134 (GAP-005 B): the TraceWriter's registration_check read through this Store, either backend — the meaning and
        error codes of tracewriter.sqlite_registration_check: a deployment run (an Agent Pack only) matches one deployment row on
        (deployment_id, pack_revision, data_release_id), an evaluation run names an existing Run; LangGraph code has no deployment."""
        from tracewriter import TraceWriteError
        origin = run.get("origin")
        try:
            with self.lock:
                if origin == "deployment":
                    ok = run.get("source_kind") == "agent_pack" and self.db.execute(
                        "SELECT 1 FROM deployment WHERE deployment_id = ? AND pack_revision = ? AND data_release_id = ?",
                        (run.get("deployment_id"), run.get("pack_revision"), run.get("data_release_id"))).fetchone() is not None
                elif origin == "evaluation":
                    ok = self.db.execute("SELECT 1 FROM runs WHERE id = ?", (run.get("evaluation_run_id"),)).fetchone() is not None
                else:
                    raise TraceWriteError("trace_schema", f"모르는 origin입니다: {origin}")
        except DB_ERRORS as e:
            raise TraceWriteError("trace_unavailable", f"등록 기록을 읽을 수 없습니다: {type(e).__name__}") from None
        if ok:
            return
        if origin == "deployment":
            raise TraceWriteError("deployment_not_registered",
                                  f"등록되지 않은 배포입니다: deployment_id={run.get('deployment_id')}, pack_revision={run.get('pack_revision')}, "
                                  f"data_release_id={run.get('data_release_id')}")
        raise TraceWriteError("unknown_evaluation_run", f"없는 평가 실행입니다: evaluation_run_id={run.get('evaluation_run_id')}")

    # ------------------------------------------------------------------ edits and node positions (EV-022 CHG-108 · 109 · 115)
    def _edit(self, r):
        if not r:
            return None
        return {"edit_id": r["edit_id"], "project_id": r["project_id"], "release": r["release_id"], "base_dir": r["base_dir"],
                "base_revision": r["base_revision"], "lines": json.loads(r["lines"]), "revision": r["revision"],
                "check": json.loads(r["check_result"]), "graph": json.loads(r["graph"]) if r["graph"] else None,
                "started_at": r["started_at"], "updated_at": r["updated_at"], "ended": r["ended"], "result_release_id": r["result_release_id"]}

    def edit(self, edit_id):
        """One edit by id, open or ended (EV-022 CHG-113: a deploy approval reads the change lines a confirmed version came from)."""
        with self.lock:
            return self._edit(self.db.execute("SELECT * FROM release_edit WHERE edit_id=?", (edit_id,)).fetchone())

    def open_edit(self, pid):
        with self.lock:
            return self._edit(self.db.execute("SELECT * FROM release_edit WHERE project_id=? AND ended IS NULL", (pid,)).fetchone())

    def start_edit(self, pid, rid, base_dir, base_revision):
        """S-1: one open edit per Project — a second start raises one of INTEGRITY_ERRORS (the partial unique index)."""
        now = time.time()
        with self.lock, self.db:
            self.db.execute("INSERT INTO release_edit (project_id, release_id, base_dir, base_revision, started_at, updated_at) VALUES (?,?,?,?,?,?)",
                            (pid, rid, str(base_dir), base_revision, now, now))
        return self.open_edit(pid)

    def save_edit(self, edit_id, *, lines, revision, check, graph):
        with self.lock, self.db:
            self.db.execute("UPDATE release_edit SET lines=?, revision=?, check_result=?, graph=?, updated_at=? WHERE edit_id=? AND ended IS NULL",
                            (self._j(lines), revision, self._j(check), self._j(graph) if graph is not None else None, time.time(), edit_id))

    def end_edit(self, edit_id, how, result_release_id=None, memo=None):
        with self.lock, self.db:
            self.db.execute("UPDATE release_edit SET ended=?, ended_at=?, result_release_id=?, memo=? WHERE edit_id=? AND ended IS NULL",
                            (how, time.time(), result_release_id, memo, edit_id))

    def set_edit_result(self, edit_id, result_release_id):
        """EV-035: the version a wheel's confirmed edit became (written once, right after its registration)."""
        with self.lock, self.db:
            self.db.execute("UPDATE release_edit SET result_release_id=? WHERE edit_id=? AND ended='confirmed' AND result_release_id IS NULL", (result_release_id, edit_id))

    def drop_unregistered_edit(self, edit_id):
        """EV-035: a wheel's edit row whose registration failed leaves nothing (PRES-181) — only a confirmed row without a version."""
        with self.lock, self.db:
            self.db.execute("DELETE FROM release_edit WHERE edit_id=? AND ended='confirmed' AND result_release_id IS NULL", (edit_id,))

    def layout(self, owner):
        with self.lock:
            r = self.db.execute("SELECT positions FROM node_layout WHERE owner=?", (owner,)).fetchone()
            return json.loads(r["positions"]) if r else None

    def set_layout(self, owner, positions):
        with self.lock, self.db:
            self.db.execute("INSERT INTO node_layout VALUES (?,?,?) ON CONFLICT(owner) DO UPDATE SET positions=excluded.positions, updated_at=excluded.updated_at",
                            (owner, self._j(positions), time.time()))

    def replace_pack_revision(self, rid, *, code_hash, graph, pack):
        """'현재 버전 수정' (S-2 · S-14): the release keeps its id and name; its revision, graph and Pack record change. The Runs keep
        the code hash they ran with, so earlier evaluations and verdicts stay records of the earlier revision."""
        with self.lock, self.db:
            self.db.execute("UPDATE releases SET code_hash=?, graph=?, updated_at=? WHERE id=?", (code_hash, self._j(graph), time.time(), rid))
            self.db.execute("UPDATE pack_releases SET data=? WHERE release_id=?", (self._j(pack), rid))
        return self.release(rid)

    # ------------------------------------------------------------------ evaluation cases (EV-020 CHG-98)
    def add_promotion(self, pid, rid, run_id, deployment_id, revision, input_ref, value, observed, by, reason):
        """EV-025 OPS-3: one operational Run as an evaluation case (append only). A second time for the same Run in the Project raises
        one of INTEGRITY_ERRORS (the caller answers 409)."""
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO case_promotion (project_id, release_id, source_run_id, deployment_id, source_pack_revision, input_ref,"
                                  " input, observed, promoted_by, reason, promoted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                  (pid, rid, run_id, deployment_id, revision, input_ref, self._j(value), self._j(observed), self._r(by.strip()),
                                   self._r(reason.strip()), time.time()))
            return self.promotion(cur.lastrowid)

    def promotion(self, promotion_id):
        with self.lock:
            r = self.db.execute("SELECT * FROM case_promotion WHERE promotion_id=?", (promotion_id,)).fetchone()
            return self._promotion(r) if r else None

    def promotions(self, pid):
        with self.lock:
            return [self._promotion(r) for r in self.db.execute("SELECT * FROM case_promotion WHERE project_id=? ORDER BY promotion_id", (pid,))]

    @staticmethod
    def _promotion(r):
        d = dict(r)
        d["input"], d["observed"] = json.loads(d["input"]), json.loads(d["observed"])
        return d

    # ---------------------------------------------------------------- EV-030 auto cycle: diagnosis (CHG-150 · 151)
    _DIAG_JSON = ("rule_result", "ratio_result", "judge_result")
    _DIAG_SET = _DIAG_JSON + ("status", "judge_kind", "judge_model", "signal", "suspect_node", "error_kind", "finished_at")

    def diagnosis_waiting(self, pid, rid, run_id, deployment_id, revision, batch, rubric):
        """A waiting diagnosis row for an operational Run unless that Run already has one under this rubric (one Run is diagnosed
        once per rubric — V-DIAG-RESUME). Returns the new row's id, or None when it was already there."""
        with self.lock, self.db:
            if self.db.execute("SELECT diagnosis_id FROM ops_diagnosis WHERE project_id=? AND run_id=? AND rubric_version=?",
                               (pid, run_id, rubric)).fetchone():
                return None
            cur = self.db.execute("INSERT INTO ops_diagnosis (project_id, release_id, run_id, deployment_id, pack_revision, batch, status,"
                                  " rubric_version, created_at) VALUES (?,?,?,?,?,?,'waiting',?,?)",
                                  (pid, rid, run_id, deployment_id, revision, batch, rubric, time.time()))
            return cur.lastrowid

    def diagnosis_update(self, diagnosis_id, **fields):
        """Sets the named columns of one diagnosis row (the same row again when a failed one is retried — V-DIAG-UNREADABLE ②)."""
        bad = set(fields) - set(self._DIAG_SET)
        if bad:
            raise ValueError(f"모르는 진단 칸: {sorted(bad)}")
        cols = sorted(fields)
        vals = [self._j(fields[c]) if c in self._DIAG_JSON and fields[c] is not None else fields[c] for c in cols]
        with self.lock, self.db:
            self.db.execute(f"UPDATE ops_diagnosis SET {', '.join(c + '=?' for c in cols)} WHERE diagnosis_id=?", (*vals, diagnosis_id))

    # EV-030 AC-4 (CHG-157): the improvement wheel — one open per Project (partial unique index), its log append only
    _WHEEL_SET = ("cell", "cell_state", "iteration", "outcome", "ended_at")

    def add_wheel(self, pid, kind, started_by, rid, revision, scope, goal, cell, cell_state, settings):
        with self.lock, self.db:
            seq = (self.db.execute("SELECT MAX(seq) AS m FROM auto_wheel WHERE project_id=?", (pid,)).fetchone()["m"] or 0) + 1
            cur = self.db.execute("INSERT INTO auto_wheel (project_id, seq, kind, started_by, base_release_id, base_revision, scope, goal, cell,"
                                  " cell_state, settings, started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                  (pid, seq, kind, started_by, rid, revision, scope, goal, cell, cell_state, settings, time.time()))
            wid = cur.lastrowid
        return self.wheel(wid)

    def wheel(self, wid):
        with self.lock:
            r = self.db.execute("SELECT * FROM auto_wheel WHERE wheel_id=?", (wid,)).fetchone()
            return dict(r) if r else None

    def open_wheel(self, pid):
        with self.lock:
            r = self.db.execute("SELECT * FROM auto_wheel WHERE project_id=? AND ended_at IS NULL", (pid,)).fetchone()
            return dict(r) if r else None

    def wheels(self, pid):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM auto_wheel WHERE project_id=? ORDER BY seq DESC", (pid,))]

    def open_wheels(self):
        """EV-051 CHG-W2-26: every Project's open wheel (one query — the vector DB version sweep and its admin command)."""
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM auto_wheel WHERE ended_at IS NULL ORDER BY wheel_id")]

    def ended_wheels(self, since=None):
        """EV-051 CHG-W2-27: the wheels that ended (at or after `since`), any Project."""
        with self.lock:
            if since is None:
                return [dict(r) for r in self.db.execute("SELECT * FROM auto_wheel WHERE ended_at IS NOT NULL ORDER BY wheel_id")]
            return [dict(r) for r in self.db.execute("SELECT * FROM auto_wheel WHERE ended_at IS NOT NULL AND ended_at >= ? ORDER BY wheel_id", (since,))]

    def wheel_state(self, wid):
        """EV-035: the main line's progress of one wheel (③ suites · baseline Runs · a hold), kept beside the settings in force under
        the key '_main' of the settings column (the settings keys themselves never change after the wheel starts)."""
        w = self.wheel(wid)
        return (json.loads(w["settings"]) or {}).get("_main") or {} if w else {}

    def set_wheel_state(self, wid, state):
        with self.lock, self.db:
            r = self.db.execute("SELECT settings FROM auto_wheel WHERE wheel_id=?", (wid,)).fetchone()
            s = json.loads(r["settings"]) or {}
            s["_main"] = state
            self.db.execute("UPDATE auto_wheel SET settings=? WHERE wheel_id=?", (self._j(s), wid))

    def wheel_stop(self, wid):
        """D-476 덧 20: a person's stop request of the wheel {by, reason, at}, or None — kept beside '_main' under '_stop' (a step's
        set_wheel_state never touches it; no new column)."""
        w = self.wheel(wid)
        return (json.loads(w["settings"]) or {}).get("_stop") if w else None

    def request_wheel_stop(self, wid, by, reason):
        """Writes the request once (the first one stays). Returns (request, new)."""
        with self.lock, self.db:
            r = self.db.execute("SELECT settings FROM auto_wheel WHERE wheel_id=?", (wid,)).fetchone()
            s = json.loads(r["settings"]) or {}
            if s.get("_stop"):
                return s["_stop"], False
            s["_stop"] = {"by": by, "reason": reason or "", "at": time.time()}
            self.db.execute("UPDATE auto_wheel SET settings=? WHERE wheel_id=?", (self._j(s), wid))
            return s["_stop"], True

    # EV-035 CHG-181: the wheel's hypothesis rows (append only — the outcome of a tried one lives in its auto_candidate row, ML-3)
    def add_hypothesis(self, wid, iteration, batch, batch_no, node, knob, value, detail, result, discarded_reason, source):
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO auto_hypothesis (wheel_id, iteration, batch, batch_no, node, knob, value, cheap_test, result,"
                                  " discarded_reason, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                  (wid, iteration, batch, batch_no, node, knob, self._j(value), self._j(detail), result,
                                   self.redact(discarded_reason) if discarded_reason else None, source, time.time()))
            return cur.lastrowid

    def hypotheses(self, wid):
        with self.lock:
            out = []
            for r in self.db.execute("SELECT * FROM auto_hypothesis WHERE wheel_id=? ORDER BY hypothesis_id", (wid,)):
                d = dict(r)
                d["value"] = json.loads(d["value"]) if d.get("value") else None
                d["detail"] = json.loads(d.pop("cheap_test")) if d.get("cheap_test") else {}
                out.append(d)
            return out

    # EV-035 CHG-182 · 188: the wheel's rows (one tried change each — a row is not a version, D-121)
    _CAND_JSON = ("hypothesis_ids", "lines", "check_result", "cheap_test", "table_result", "recommendation")
    _CAND_SET = ("lines", "revision", "check_result", "cheap_test", "table_result", "executions", "rank", "outcome", "recommendation", "release_id")

    def add_candidate(self, wid, iteration, hypothesis_ids, node, lines):
        now = time.time()
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO auto_candidate (wheel_id, iteration, hypothesis_ids, node, lines, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                                  (wid, iteration, self._j(hypothesis_ids), node, self._j(lines), now, now))
            return cur.lastrowid

    def candidate_update(self, cid, **fields):
        bad = set(fields) - set(self._CAND_SET)
        if bad:
            raise ValueError(f"모르는 후보 줄 칸: {sorted(bad)}")
        cols = sorted(fields)
        vals = [self._j(fields[c]) if c in self._CAND_JSON and fields[c] is not None else fields[c] for c in cols]
        with self.lock, self.db:
            self.db.execute(f"UPDATE auto_candidate SET {', '.join(c + '=?' for c in cols)}, updated_at=? WHERE candidate_id=?", (*vals, time.time(), cid))

    def candidates(self, wid):
        with self.lock:
            out = []
            for r in self.db.execute("SELECT * FROM auto_candidate WHERE wheel_id=? ORDER BY candidate_id", (wid,)):
                d = dict(r)
                for c in self._CAND_JSON:
                    d[c] = json.loads(d[c]) if d.get(c) else None
                out.append(d)
            return out

    def add_confirmed_edit(self, pid, rid, base_dir, base_revision, lines, revision, check, result_release_id, memo):
        """EV-035 CHG-183: the wheel's change lines as one edit that is already confirmed (ended 'confirmed') — the same row a person's
        confirmed edit leaves, never an open edit (the Project's one open edit stays the person's)."""
        now = time.time()
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO release_edit (project_id, release_id, base_dir, base_revision, lines, revision, check_result, graph,"
                                  " started_at, updated_at, ended, ended_at, result_release_id, memo) VALUES (?,?,?,?,?,?,?,NULL,?,?,'confirmed',?,?,?)",
                                  (pid, rid, str(base_dir), base_revision, self._j(lines), revision, self._j(check), now, now, now, result_release_id, self._r(memo)))
            return cur.lastrowid

    # EV-090 (D-495 ②): one wheel's records carried between stores (llmops.wheelcarry) — raw rows out, one transaction in
    CARRY_TABLES = ("releases", "pack_releases", "release_edit", "auto_wheel", "auto_hypothesis", "auto_candidate", "auto_wheel_log",
                    "eval_suites", "runs", "run_cases", "run_events", "verdicts")

    def carry_rows(self, table, where, args=()):
        """Raw rows of one carried table (JSON columns as stored text) — `table` from CARRY_TABLES only."""
        if table not in self.CARRY_TABLES:
            raise ValueError(f"옮기지 않는 표: {table}")
        with self.lock:
            return [dict(r) for r in self.db.execute(f"SELECT * FROM {table} WHERE {where}", tuple(args))]

    @contextlib.contextmanager
    def carry_tx(self):
        """One transaction for a carry: yields (insert(table, row) → the new identity or None, exec(sql, args)); any error rolls all back."""
        with self.lock, self.db:
            def insert(table, row):
                if table not in self.CARRY_TABLES:
                    raise ValueError(f"옮기지 않는 표: {table}")
                cols = list(row)
                return self.db.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                       tuple(row[c] for c in cols)).lastrowid
            yield insert, lambda sql, args=(): self.db.execute(sql, tuple(args))

    # EV-035 CHG-187: what the wheels' paid steps spent (append only — no update or delete path)
    def add_spend(self, wid, step, calls, tokens_in, tokens_out, usd, model):
        with self.lock, self.db:
            self.db.execute("INSERT INTO auto_spend (wheel_id, step, calls, tokens_in, tokens_out, usd, model, created_at) VALUES (?,?,?,?,?,?,?,?)",
                            (wid, step, int(calls), int(tokens_in), int(tokens_out), float(usd), model, time.time()))

    def spend(self, wid=None, since=None):
        """{calls, usd} of one wheel, or of every wheel since a time (this server's start) — with the sealed tests called outside a
        wheel (D-486 덧 9), which the server and daily caps count the same way."""
        q, a = "SELECT COALESCE(SUM(calls), 0) AS calls, COALESCE(SUM(usd), 0) AS usd FROM auto_spend WHERE 1=1", []
        if wid is not None:
            q += " AND wheel_id=?"; a.append(wid)
        if since is not None:
            q += " AND created_at>=?"; a.append(since)
        with self.lock:
            r = self.db.execute(q, tuple(a)).fetchone()
            usd = float(r["usd"])
            if wid is None and self.sealed_ready:
                usd += float(self.db.execute("SELECT COALESCE(SUM(usd), 0) AS usd FROM sealed_use WHERE created_at>=?", (since or 0,)).fetchone()["usd"])
            return {"calls": int(r["calls"]), "usd": usd}

    def add_sealed_use(self, *, set12, project_id, release_id, base_release_id, result, called_by, reason, counts, usd, line, usd_measured=0.0,
                       usd_estimated=0.0, revision=""):
        """D-486 덧 9: one use of a sealed set outside a wheel (append only — no update or delete path); counts only. 덧 12: its spend
        `usd` = `usd_measured` (tokens × prices) + `usd_estimated` (the careful unit where no usage came back)."""
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO sealed_use (set_sha12, project_id, release_id, revision, base_release_id, result, called_by, reason, counts,"
                                  " usd, usd_measured, usd_estimated, line, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                  (set12, project_id, release_id, revision or "", base_release_id, result, called_by, self._r(reason), self._j(counts), float(usd),
                                   float(usd_measured), float(usd_estimated), self._r(line), time.time()))
            return cur.lastrowid

    def sealed_uses(self, set12=None):
        """The uses outside a wheel, oldest first (every set, or one set by its sha's first 12)."""
        q, a = "SELECT * FROM sealed_use", ()
        if set12 is not None:
            q, a = q + " WHERE set_sha12=?", (set12,)
        with self.lock:
            return [dict(r) for r in self.db.execute(q + " ORDER BY use_id", a)]

    def set_suite_auto(self, sid, auto):
        """EV-035 CHG-180: the wheel's marks on a suite it registered (sets · role · wheel) — the cases and hashes stay as registered."""
        with self.lock, self.db:
            d = json.loads(self.db.execute("SELECT data FROM eval_suites WHERE id=?", (sid,)).fetchone()["data"])
            d["auto"] = auto
            self.db.execute("UPDATE eval_suites SET data=? WHERE id=?", (self._j(d), sid))
        return self.suite(sid)

    def wheel_update(self, wid, **fields):
        bad = set(fields) - set(self._WHEEL_SET)
        if bad:
            raise ValueError(f"모르는 바퀴 칸: {sorted(bad)}")
        cols = sorted(fields)
        with self.lock, self.db:
            self.db.execute(f"UPDATE auto_wheel SET {', '.join(c + '=?' for c in cols)} WHERE wheel_id=?", (*[fields[c] for c in cols], wid))

    # EV-069 CHG-EF-1 · EF-2: a release's fixed flow (one row, the list as packrt read it) and the Project's improvement settings
    def fixed_flow(self, rid):
        with self.lock:
            r = self.db.execute("SELECT * FROM fixed_flow WHERE release_id=?", (rid,)).fetchone()
        return {**dict(r), "items": json.loads(r["items"])} if r else None

    def fixed_flows(self, rids):
        """D-485: {release id: row} for several releases in one read (the version bands)."""
        rids = list(rids)
        if not rids:
            return {}
        with self.lock:
            rows = self.db.execute(f"SELECT * FROM fixed_flow WHERE release_id IN ({','.join('?' * len(rids))})", rids).fetchall()
        return {r["release_id"]: {**dict(r), "items": json.loads(r["items"])} for r in rows}

    def set_fixed_flow(self, rid, revision, items, confirmed_by=None):
        now = time.time()
        with self.lock, self.db:
            self.db.execute("DELETE FROM fixed_flow WHERE release_id=?", (rid,))
            self.db.execute("INSERT INTO fixed_flow (release_id, pack_revision, items, knob_ranges, confirmed_by, confirmed_at, updated_at)"
                            " VALUES (?,?,?,?,?,?,?)", (rid, revision, json.dumps(items, ensure_ascii=False), "{}", confirmed_by,
                                                         now if confirmed_by else None, now))
        return self.fixed_flow(rid)

    def confirm_fixed_flow(self, rid, by):
        with self.lock, self.db:
            self.db.execute("UPDATE fixed_flow SET confirmed_by=?, confirmed_at=? WHERE release_id=? AND confirmed_by IS NULL", (by, time.time(), rid))
        return self.fixed_flow(rid)

    def auto_settings(self, pid):
        with self.lock:
            r = self.db.execute("SELECT data FROM auto_settings WHERE project_id=?", (pid,)).fetchone()
        return json.loads(r["data"]) if r else {}

    def set_auto_settings(self, pid, data, by):
        with self.lock, self.db:
            self.db.execute("DELETE FROM auto_settings WHERE project_id=?", (pid,))
            self.db.execute("INSERT INTO auto_settings (project_id, data, updated_by, updated_at) VALUES (?,?,?,?)",
                            (pid, json.dumps(data, ensure_ascii=False), by, time.time()))
        return self.auto_settings(pid)

    def wheel_log(self, wid, cell, text):
        with self.lock, self.db:
            self.db.execute("INSERT INTO auto_wheel_log (wheel_id, cell, text, created_at) VALUES (?,?,?,?)", (wid, cell, self.redact(text), time.time()))

    def wheel_logs(self, wid):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM auto_wheel_log WHERE wheel_id=? ORDER BY log_id", (wid,))]

    def add_label(self, pid, rid, run_id, node, label, list_version, by, reason):
        """One person's label on an operational Run (CHG-154): append only, the last one counts (no update or delete path)."""
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO review_label (project_id, release_id, run_id, node, label, label_list_version, by_alias, reason,"
                                  " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                                  (pid, rid, run_id, node, label, list_version, self.redact(by), self.redact(reason), time.time()))
            return cur.lastrowid

    def labels(self, pid, rid=None):
        sql, args = "SELECT * FROM review_label WHERE project_id=?", [pid]
        if rid:
            sql, args = sql + " AND release_id=?", args + [rid]
        with self.lock:
            return [dict(r) for r in self.db.execute(sql + " ORDER BY label_id", tuple(args))]

    def diagnoses(self, pid, rid=None, statuses=None):
        sql, args = "SELECT * FROM ops_diagnosis WHERE project_id=?", [pid]
        if rid:
            sql, args = sql + " AND release_id=?", args + [rid]
        if statuses:
            sql += " AND status IN (" + ",".join("?" * len(statuses)) + ")"
            args += list(statuses)
        with self.lock:
            return [self._diagnosis(r) for r in self.db.execute(sql + " ORDER BY diagnosis_id", tuple(args))]

    @classmethod
    def _diagnosis(cls, r):
        d = dict(r)
        for c in cls._DIAG_JSON:
            d[c] = json.loads(d[c]) if d[c] else None
        return d

    def add_evaluation_case(self, evaluation_run_id, case_index, case_id, run_id):
        with self.lock, self.db:
            self.db.execute("INSERT INTO evaluation_case VALUES (?,?,?,?)", (evaluation_run_id, case_index, case_id, run_id))

    def evaluation_cases(self, evaluation_run_id):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM evaluation_case WHERE evaluation_run_id=? ORDER BY case_index", (evaluation_run_id,))]

    def evaluation_run(self, uid):
        with self.lock:
            r = self.db.execute("SELECT * FROM evaluation_run WHERE evaluation_run_id=?", (uid,)).fetchone()
            return dict(r) if r else None

    def create_run(self, pid, release_id, meta, cases):
        uid = "run-" + uuid.uuid4().hex[:10]
        now = time.time()
        with self.lock, self.db:
            seq = self.db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM runs WHERE project_id=?", (pid,)).fetchone()[0]
            meta = {**meta, "label": f"R-{seq}"}
            self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,NULL)", (uid, pid, seq, release_id, self._j(meta), "실행 중", now))
            for i, c in enumerate(cases):
                self.db.execute("INSERT INTO run_cases VALUES (?,?,?)", (uid, i, self._j(c)))
        return uid

    def update_case(self, uid, idx, data):
        with self.lock, self.db:
            self.db.execute("UPDATE run_cases SET data=? WHERE run_id=? AND idx=?", (self._j(data), uid, idx))

    def add_event(self, uid, event):
        with self.lock, self.db:
            seq = self.db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id=?", (uid,)).fetchone()[0]
            self.db.execute("INSERT INTO run_events VALUES (?,?,?)", (uid, seq, self._j({**event, "seq": seq})))
        return seq

    def events(self, uid, after=0):
        with self.lock:
            return [json.loads(r["data"]) for r in self.db.execute("SELECT data FROM run_events WHERE run_id=? AND seq>? ORDER BY seq", (uid, after))]

    def set_run_meta(self, uid, key, value):
        """One field of the Run record set while it runs (EV-002: the execution manifest reported by the Pack runtime)."""
        with self.lock, self.db:
            meta = json.loads(self.db.execute("SELECT meta FROM runs WHERE id=?", (uid,)).fetchone()["meta"])
            meta[key] = value
            self.db.execute("UPDATE runs SET meta=? WHERE id=?", (self._j(meta), uid))

    def finish_run(self, uid, status, notice=None, summary=None, cases=()):
        """End state, per-case scores/diagnoses and the Run summary in one transaction (no reader sees a finished Run without them)."""
        with self.lock, self.db:
            meta = json.loads(self.db.execute("SELECT meta FROM runs WHERE id=?", (uid,)).fetchone()["meta"])
            if notice:
                meta["notice"] = notice
            meta["summary"] = summary
            for c in cases:
                self.db.execute("UPDATE run_cases SET data=? WHERE run_id=? AND idx=?", (self._j(c), uid, c["i"]))
            self.db.execute("UPDATE runs SET status=?, finished_at=?, meta=? WHERE id=?", (status, time.time(), self._j(meta), uid))

    def add_verdict(self, uid, verdict):
        """Verdicts are records made by a person: append-only (no update or delete exists)."""
        with self.lock, self.db:
            seq = self.db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM verdicts WHERE run_id=?", (uid,)).fetchone()[0]
            self.db.execute("INSERT INTO verdicts VALUES (?,?,?)", (uid, seq, self._j(verdict)))

    def verdicts(self, uid):
        with self.lock:
            return [json.loads(r["data"]) for r in self.db.execute("SELECT data FROM verdicts WHERE run_id=? ORDER BY seq", (uid,))]

    @staticmethod
    def _run_record(r, cases, verdicts):
        return {"uid": r["id"], "project_id": r["project_id"], "rel": r["release_id"], "status": r["status"], "created_at": r["created_at"],
                "finished_at": r["finished_at"], **json.loads(r["meta"]), "cases": cases, "verdicts": verdicts}

    def run(self, uid):
        with self.lock:
            r = self.db.execute("SELECT * FROM runs WHERE id=?", (uid,)).fetchone()
            if not r:
                return None
            cases = [json.loads(c["data"]) for c in self.db.execute("SELECT data FROM run_cases WHERE run_id=? ORDER BY idx", (uid,))]
            return self._run_record(r, cases, self.verdicts(uid))

    def _project_runs(self, pid=None):
        """{project id: its Runs in seq order} as run() makes each — of one Project or of every Project — in three statements
        whatever the number of Runs (D-470 덧 21: /api/state read each Run up to five times, three statements a time)."""
        where, args = ("WHERE project_id=?", (pid,)) if pid else ("", ())
        rows = self.db.execute(f"SELECT * FROM runs {where} ORDER BY seq", args).fetchall()
        of = f"WHERE run_id IN (SELECT id FROM runs {where})" if pid else ""
        cases, verdicts, out = {}, {}, {}
        for c in self.db.execute(f"SELECT run_id, data FROM run_cases {of} ORDER BY run_id, idx", args):
            cases.setdefault(c["run_id"], []).append(json.loads(c["data"]))
        for v in self.db.execute(f"SELECT run_id, data FROM verdicts {of} ORDER BY run_id, seq", args):
            verdicts.setdefault(v["run_id"], []).append(json.loads(v["data"]))
        for r in rows:
            out.setdefault(r["project_id"], []).append(self._run_record(r, cases.get(r["id"], []), verdicts.get(r["id"], [])))
        return out

    def runs(self, pid):
        """The Project's Runs as every list uses them (Run tab, verdicts, compare, monitor). Runs left by the removed candidate
        feature (meta.candidate, EV-020/021) are not among them: they are earlier-flow records, read with legacy() (EV-022 S-15)."""
        with self.lock:
            return [r for r in self._project_runs(pid).get(pid, []) if not r.get("candidate")]

    def legacy(self, pid, out=None):
        """Earlier-flow records of the removed candidate feature, shown folded and never compared or judged (EV-022 S-15, manual
        p.40): the candidate evaluation Runs and the change decisions about the same candidates. The candidate tables are not read;
        a decision is matched by the candidate id the Run recorded in its meta. `out`: the Project's Runs already read."""
        with self.lock:
            out = self._project_runs(pid).get(pid, []) if out is None else out
            runs = [r for r in out if r.get("candidate")]
            ids = sorted({str((r["candidate"] or {}).get("candidate_id")) for r in runs})
            rows = self.db.execute(f"SELECT * FROM decision WHERE subject_kind='candidate' AND subject_id IN ({','.join('?' * len(ids))}) ORDER BY decision_id",
                                   ids).fetchall() if ids else []
            decisions = [{**dict(r), "evidence": json.loads(r["evidence"])} for r in rows]
            return {"runs": runs, "decisions": decisions}

    def add_input_set(self, pid, name, source, cases, release_name=None, provider=None):
        """Input sets are kept as made: no update or delete exists (UA-15)."""
        sid = "set-" + uuid.uuid4().hex[:8]
        with self.lock, self.db:
            self.db.execute("INSERT INTO input_sets VALUES (?,?,?,?,?,?,?,?)", (sid, pid, self._r(name), source, self._r(release_name), provider, self._j(cases), time.time()))
        return self.input_set(sid)

    def input_set(self, sid):
        with self.lock:
            r = self.db.execute("SELECT * FROM input_sets WHERE id=?", (sid,)).fetchone()
            return self._input_set_record(r) if r else None

    @staticmethod
    def _input_set_record(r):
        return {"id": r["id"], "project_id": r["project_id"], "name": r["name"], "source": r["source"], "release": r["release_name"], "provider": r["provider"],
                "cases": json.loads(r["cases"]), "at": time.strftime("%m-%d %H:%M", time.localtime(r["created_at"]))}

    def input_sets(self, pid):
        with self.lock:
            return [self.input_set(r["id"]) for r in self.db.execute("SELECT id FROM input_sets WHERE project_id=? ORDER BY created_at, rowid", (pid,))]

    def add_bundle(self, pid, run_input, run_ids):
        """A monitor comparison: the Runs started together on one run input (each Run is an ordinary Run record)."""
        bid = "bundle-" + uuid.uuid4().hex[:8]
        with self.lock, self.db:
            self.db.execute("INSERT INTO bundles VALUES (?,?,?,?,?)", (bid, pid, self._j(run_input), self._j(run_ids), time.time()))
        return self.bundle(bid)

    def bundle(self, bid):
        with self.lock:
            r = self.db.execute("SELECT * FROM bundles WHERE id=?", (bid,)).fetchone()
            return self._bundle_record(r) if r else None

    @staticmethod
    def _bundle_record(r):
        return {"id": r["id"], "project_id": r["project_id"], "input": json.loads(r["input"]), "runs": json.loads(r["runs"]),
                "at": time.strftime("%m-%d %H:%M", time.localtime(r["created_at"]))}

    def bundles(self, pid):
        with self.lock:
            return [self.bundle(r["id"]) for r in self.db.execute("SELECT id FROM bundles WHERE project_id=? ORDER BY created_at, rowid", (pid,))]

    def save_brief(self, pid, title, fields, bid=None):
        """Implementation Brief (P-12): a text record only — nothing reads it to make code or run anything."""
        now = time.time()
        with self.lock, self.db:
            if bid:
                self.db.execute("UPDATE briefs SET title=?, fields=?, updated_at=? WHERE id=? AND project_id=?", (self._r(title), self._j(fields), now, bid, pid))
            else:
                bid = "brief-" + uuid.uuid4().hex[:8]
                self.db.execute("INSERT INTO briefs VALUES (?,?,?,?,?,?)", (bid, pid, self._r(title), self._j(fields), now, now))
        return self.brief(bid)

    def brief(self, bid):
        with self.lock:
            r = self.db.execute("SELECT * FROM briefs WHERE id=?", (bid,)).fetchone()
            return self._brief_record(r) if r else None

    @staticmethod
    def _brief_record(r):
        return {"id": r["id"], "project_id": r["project_id"], "title": r["title"], "fields": json.loads(r["fields"]),
                "at": time.strftime("%m-%d %H:%M", time.localtime(r["updated_at"]))}

    def briefs(self, pid):
        with self.lock:
            return [self.brief(r["id"]) for r in self.db.execute("SELECT id FROM briefs WHERE project_id=? ORDER BY created_at, rowid", (pid,))]

    def delete_counts(self, pid=None, rid=None):
        """What a delete would remove (the confirm dialog and the delete result use the same numbers)."""
        with self.lock:
            where, arg = ("project_id=?", pid) if pid else ("release_id=?", rid)
            runs = [r["id"] for r in self.db.execute(f"SELECT id FROM runs WHERE {where} ORDER BY seq", (arg,))]
            q = ",".join("?" * len(runs))
            verdicts = self.db.execute(f"SELECT COUNT(*) FROM verdicts WHERE run_id IN ({q})", runs).fetchone()[0] if runs else 0
            rels = [self.release(r["id"]) for r in self.db.execute("SELECT id FROM releases WHERE " + ("project_id=?" if pid else "id=?") + " ORDER BY created_at",
                                                                   (pid or rid,))]
            out = {"release": len(rels), "run": len(runs), "verdict": verdicts, "upload_copy": sum(r["how"] == "업로드" for r in rels), "run_ids": runs, "releases": rels}
            packs = sum(r["sourceKind"] == "agent_pack" for r in rels)
            if packs:
                out["pack_copy"] = packs  # only when there is one, so the counts of a Project without Packs stay as they were
            if pid:
                out["input_set"] = self.db.execute("SELECT COUNT(*) FROM input_sets WHERE project_id=?", (pid,)).fetchone()[0]
                out["bundle"] = self.db.execute("SELECT COUNT(*) FROM bundles WHERE project_id=?", (pid,)).fetchone()[0]
                out["brief"] = self.db.execute("SELECT COUNT(*) FROM briefs WHERE project_id=?", (pid,)).fetchone()[0]
                sids = [r["id"] for r in self.db.execute("SELECT id FROM eval_suites WHERE project_id=? ORDER BY created_at, rowid", (pid,))]
                out["suite_ids"] = sids
                if sids:
                    out["eval_suite"] = len(sids)   # only when there is one (the counts of a Project without suites stay as they were)
            return out

    def delete_project(self, pid):
        """One transaction: the Project and everything under it (releases, Runs with their cases, events and verdict history,
        validation data, input sets, monitor bundles, briefs) — by foreign-key cascade."""
        with self.lock, self.db:
            self.db.execute("DELETE FROM projects WHERE id=?", (pid,))

    def delete_release(self, rid):
        """One transaction: the release and its Runs (cases, events, verdict history by cascade); the Runs leave the monitor
        bundles that held them, and a bundle with no Run left is removed."""
        with self.lock, self.db:
            gone = {r["id"] for r in self.db.execute("SELECT id FROM runs WHERE release_id=?", (rid,))}
            pid = self.db.execute("SELECT project_id FROM releases WHERE id=?", (rid,)).fetchone()["project_id"]
            self.db.execute("DELETE FROM releases WHERE id=?", (rid,))
            for b in self.db.execute("SELECT id, runs FROM bundles WHERE project_id=?", (pid,)).fetchall():
                keep = [u for u in json.loads(b["runs"]) if u not in gone]
                if not keep:
                    self.db.execute("DELETE FROM bundles WHERE id=?", (b["id"],))
                elif len(keep) != len(json.loads(b["runs"])):
                    self.db.execute("UPDATE bundles SET runs=? WHERE id=?", (self._j(keep), b["id"]))

    def has_validation_data(self, pid):
        with self.lock:
            return self.db.execute("SELECT 1 FROM validation_data WHERE project_id=?", (pid,)).fetchone() is not None

    def project(self, pid, runs=None):
        """`runs`: the Project's Runs already read (projects() reads every Project's together)."""
        with self.lock:
            r = self.db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            if not r:
                return None
            d = self.db.execute("SELECT name, cases FROM validation_data WHERE project_id=?", (pid,)).fetchone()
            runs = self._project_runs(pid).get(pid, []) if runs is None else runs
            return {"id": r["id"], "name": r["name"], "desc": r["description"], "scoring": json.loads(r["scoring"]), "rule": json.loads(r["rule"]),
                    "data": {"name": d["name"], "cases": json.loads(d["cases"])} if d else None, "created_at": r["created_at"],
                    # filled by later slices (runs, input sets, verdicts, briefs, bundles, activity)
                    "releases": self.releases(pid), "sets": self.input_sets(pid), "suites": self.suites(pid), "runs": [x for x in runs if not x.get("candidate")],
                    "legacy": self.legacy(pid, runs), "verdicts": {}, "briefs": self.briefs(pid), "bundles": self.bundles(pid), "activity": [],
                    "promotions": self.promotions(pid)}   # EV-025 OPS-3: 올린 case (P12)

    def suite_runs(self, rid, limit=20):
        """EV-077 (D-454 덧): the release's finished evaluation-suite Runs, newest first, by last_suite_run's rule (no confirm-suite
        Run, no candidate Run) — the ratio picks, per kind, the newest one with cases of that kind. At most `limit` Runs are read."""
        out = []
        with self.lock:
            ids = [r["id"] for r in self.db.execute("SELECT id, meta FROM runs WHERE release_id=? AND status='완료' ORDER BY seq DESC", (rid,)).fetchall()
                   if (lambda m: (m.get("auto") or {}).get("role") != "confirm" and not m.get("candidate")
                       and (m.get("input") or {}).get("kind") in ("suite", "평가 suite"))(json.loads(r["meta"]))]
        for i in ids[:limit]:
            r = self.run(i)
            if r:
                out.append(r)
        return out

    def last_suite_run(self, rid, statuses=("완료",)):
        """The release's last finished evaluation-suite Run (one Run read, not every Run — EV-030 ratio signal). A wheel's confirm-suite
        Run (auto role 'confirm', EV-043 CHG-211) is never it: the next wheel and the ratio signal keep the work suite. D-476 덧 21: a Run
        a person (or the app's evaluation) made comes before any the wheel made (the steadiness baseline · hidden · replay · row — the
        work suite without its hidden cases); the wheel's newest counts only on a version with no person's suite Run (EV-043 A3-N3).
        D-484 덧 7: `statuses` = the end states taken ('완료' only for the ratio signal · the efficiency wheel; the accuracy wheel's base
        also takes '일부 실패' — the failed cases are the ones to fix)."""
        wheel = None
        marks = ",".join("?" * len(statuses))
        with self.lock:
            for r in self.db.execute(f"SELECT id, meta FROM runs WHERE release_id=? AND status IN ({marks}) ORDER BY seq DESC", (rid, *statuses)).fetchall():
                meta = json.loads(r["meta"])
                if (meta.get("auto") or {}).get("role") == "confirm":
                    continue
                if not meta.get("candidate") and (meta.get("input") or {}).get("kind") in ("suite", "평가 suite"):   # a finished suite Run
                    if not meta.get("auto"):                                                                           # only ('완료')
                        return self.run(r["id"])
                    wheel = wheel or r["id"]
        return self.run(wheel) if wheel else None

    def project_ids(self):
        """The Project ids only (EV-030: the automatic diagnosis looks for 운영 versions without loading every Run — AC-1 review)."""
        with self.lock:
            return [r["id"] for r in self.db.execute("SELECT id FROM projects ORDER BY created_at")]

    def pack_projects(self, pairs):
        """The ids of the Projects holding an Agent Pack release of one of the (pack id, revision) pairs, oldest first (EV-030 AC-1
        review-3: a diagnosis pass looks only at the Projects a tagged deployment can belong to, not at every Project's releases)."""
        want = {tuple(x) for x in pairs}
        if not want:
            return []
        with self.lock:
            rows = self.db.execute("SELECT r.project_id, k.data FROM pack_releases k JOIN releases r ON r.id = k.release_id"
                                   " JOIN projects p ON p.id = r.project_id ORDER BY p.created_at, r.created_at").fetchall()
        out = []
        for r in rows:
            pk = json.loads(r["data"])
            if (pk.get("id"), pk.get("revision")) in want and r["project_id"] not in out:
                out.append(r["project_id"])
        return out

    def projects(self):
        """Every Project as project() makes each — state-n1 (D-463 덧 17): each table read once for all of them (the Runs in three
        statements, D-470 덧 21), not once a Project · release · item."""
        with self.lock:
            runs = self._project_runs()
            group = lambda sql, make: {k: [make(r) for r in rs] for k, rs in self._by_project(sql).items()}
            data = {r["project_id"]: r for r in self.db.execute("SELECT project_id, name, cases FROM validation_data")}
            packs = {r["release_id"]: r["data"] for r in self.db.execute("SELECT release_id, data FROM pack_releases")}
            rels = group("SELECT * FROM releases ORDER BY created_at", lambda r: self._release_record(r, packs.get(r["id"])))
            sets = group("SELECT * FROM input_sets ORDER BY created_at, rowid", self._input_set_record)
            suites = group("SELECT * FROM eval_suites ORDER BY created_at, rowid", self._suite_record)
            briefs = group("SELECT * FROM briefs ORDER BY created_at, rowid", self._brief_record)
            bundles = group("SELECT * FROM bundles ORDER BY created_at, rowid", self._bundle_record)
            proms = group("SELECT * FROM case_promotion ORDER BY promotion_id", self._promotion)
            out = []
            for r in self.db.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall():
                pid, mine, d = r["id"], runs.get(r["id"], []), data.get(r["id"])
                out.append({"id": pid, "name": r["name"], "desc": r["description"], "scoring": json.loads(r["scoring"]), "rule": json.loads(r["rule"]),
                            "data": {"name": d["name"], "cases": json.loads(d["cases"])} if d else None, "created_at": r["created_at"],
                            "releases": rels.get(pid, []), "sets": sets.get(pid, []), "suites": suites.get(pid, []), "runs": [x for x in mine if not x.get("candidate")],
                            "legacy": self.legacy(pid, mine), "verdicts": {}, "briefs": briefs.get(pid, []), "bundles": bundles.get(pid, []), "activity": [],
                            "promotions": proms.get(pid, [])})
            return out

    def _by_project(self, sql):
        out = {}
        for r in self.db.execute(sql).fetchall():
            out.setdefault(r["project_id"], []).append(r)
        return out

    def open_edits(self):
        """state-n1: {project id: its open edit} — the answer's one read (open_edit per Project before)."""
        with self.lock:
            return {r["project_id"]: self._edit(r) for r in self.db.execute("SELECT * FROM release_edit WHERE ended IS NULL").fetchall()}

