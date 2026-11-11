"""Week-1 registration check (shared DB design "단계별 적용", trace contract 8): LLMOps records are still in SQLite `llmops.db`,
so trace cannot hold a foreign key to them. The writer calls this before it records run_started. The database is opened read-only
(SQLite URI mode=ro); nothing is written. Week 2 replaces this with foreign keys (registration_check=None)."""
import sqlite3
from pathlib import Path

from .errors import TraceWriteError


def _connect_ro(db_path):
    p = Path(db_path).resolve()
    if not p.is_file():
        return None
    try:
        return sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as e:
        raise TraceWriteError("trace_unavailable", f"등록 기록을 열 수 없습니다: {type(e).__name__}") from None


def _exists(db, sql, args):
    try:
        return db.execute(sql, args).fetchone() is not None
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):   # table not there yet: nothing is registered
            return False
        raise TraceWriteError("trace_unavailable", f"등록 기록을 읽을 수 없습니다: {type(e).__name__}") from None


def sqlite_registration_check(db_path):
    """A registration_check for TraceWriter. A deployment run must match one `deployment` row on all three of
    (deployment_id, pack_revision, data_release_id); an evaluation run must name an existing LLMOps Run (llmops.db `runs.id`, the Run
    uid — the change contract's "runs.uid" names the same value; the column is `id`).
    LangGraph code releases (source_kind langgraph) are evaluation only and have no deployment check."""
    def check(run):
        origin = run.get("origin")
        db = _connect_ro(db_path)
        try:
            if origin == "deployment":
                ok = db is not None and run.get("source_kind") == "agent_pack" and _exists(
                    db, "SELECT 1 FROM deployment WHERE deployment_id = ? AND pack_revision = ? AND data_release_id = ?",
                    (run.get("deployment_id"), run.get("pack_revision"), run.get("data_release_id")))
                if not ok:
                    raise TraceWriteError("deployment_not_registered",
                                          f"등록되지 않은 배포입니다: deployment_id={run.get('deployment_id')}, pack_revision={run.get('pack_revision')}, "
                                          f"data_release_id={run.get('data_release_id')}")
            elif origin == "evaluation":
                ok = db is not None and _exists(db, "SELECT 1 FROM runs WHERE id = ?", (run.get("evaluation_run_id"),))
                if not ok:
                    raise TraceWriteError("unknown_evaluation_run", f"없는 평가 실행입니다: evaluation_run_id={run.get('evaluation_run_id')}")
            else:
                raise TraceWriteError("trace_schema", f"모르는 origin입니다: {origin}")
        finally:
            if db is not None:
                db.close()
    return check
