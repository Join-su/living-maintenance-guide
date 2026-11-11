"""등록 대조(S-06) — llmops 장부의 deployment 줄 읽기(VFE EV-005 ES-E, CHG-02, GAP-005 B).

서버 · scripts/deploy-status.py · scripts/es3_check.py가 같은 함수 ledger_rows로 장부 줄을 읽는다. 세 칸(deployment_id · pack_revision ·
data_release_id) 대조와 V-04 규칙은 그대로이고, 장부에는 쓰지 않는다(S-20 — 등록은 llmops만).
- llmops_db: llmops.db(SQLite) deployment 표를 읽기 전용으로(mode=ro, WAL이면 임시 사본). Mac 예행연습용으로 남김.
  서버의 실행 대조는 공유 기록기 함수(tracewriter sqlite_registration_check) 그대로 쓴다.
- llmops_pg: PostgreSQL llmops.deployment를 실행용 계정 runtime_reader(LLMOPSAGENT_RUNTIME_DSN)로 SELECT만
  (표 모양 = llmops db/migrations 0005, 권한 = 0006: llmops USAGE + llmops.deployment SELECT). 읽기 전용 거래로 연다.
- none: 대조 없음.
접속 정보 값은 출력 · 기록하지 않는다(오류 문구에는 형 이름만)."""
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from tracewriter import TraceWriteError, sqlite_registration_check

from .execution import runtime_dsn

MODES = {"none", "llmops_db", "llmops_pg"}
COLUMNS = ("deployment_id", "pack_revision", "data_release_id", "pack_commit", "pack_tag")
MODEL_COLUMN = "model_release_id"   # llmops migration 0010(EV-044)부터 — 없으면 지금처럼 세 칸 대조(0010 전 기간, CHG-226)
MODEL_BINDINGS_COLUMN = "model_bindings"   # 같은 0010 칸 — {모델 이름: {release_id, …}} JSON 글. 거부 글에 어느 모델이 다른지만 씀(D-373)
MAX_MODELS, MAX_LEDGER_VALUES = 32, 16   # 거부 details 크기 상한(D-373)
PG_TABLE = "llmops.deployment"


def model_ok(row, model_release_id):
    """장부 줄의 모델 칸이 이 배포가 불러온 모델 묶음과 맞는지(D-326 Q-B · D-328 Q-1 (가)): 칸이 없거나(0010 전) null(0010 전에 쓴 줄)이면
    지금처럼 받아들이고, 값이 있으면 packrt `model_release_id`(`mrs:` …, 모델 없는 Pack은 None)와 같아야 함."""
    have = row.get(MODEL_COLUMN)
    return have is None or have == model_release_id


class LedgerUnavailable(Exception):
    """장부에 닿지 못함(접속 · 권한 · 읽기 오류). 문구에는 형 이름만 둔다."""


def ledger_dsn(settings):
    """llmops_pg일 때 장부를 읽을 DSN(실행용과 같은 이름 · 같은 찾는 곳: 환경 변수, 없으면 runtime_db.env_file). 그 밖에는 None."""
    if settings.registration.get("mode") != "llmops_pg":
        return None
    return runtime_dsn(settings.runtime_db.get("env_file"))


def ledger_name(registration):
    """사람이 읽을 장부 이름(경로 끝 이름 또는 PG 표 이름)."""
    return Path(registration["path"]).name if registration.get("mode") == "llmops_db" else PG_TABLE


def ledger_rows(registration, deployment_id, dsn=None):
    """배포 id의 장부 줄(dict 목록, 장부에 들어간 순서 — SQLite rowid, PG는 ord 칸이 없어 created_at 순으로 근사). 장부가 없으면 None.
    PG에 닿지 못하면 LedgerUnavailable(SQLite 읽기 오류는 옮기기 전 deploy-status와 같이 그대로 올라감)."""
    mode = registration.get("mode")
    if mode == "llmops_db":
        return sqlite_rows(registration["path"], deployment_id)
    if mode == "llmops_pg":
        return pg_rows(dsn, deployment_id)
    raise ValueError(f"등록 대조 설정이 아닙니다(registration.mode {mode})")


def sqlite_rows(db_path, deployment_id):
    p = Path(db_path)
    if not p.exists():
        return None
    with p.open("rb") as f:
        wal = f.read(20)[18:20] == b"\x02\x02"   # 파일 머리 18·19바이트 = 2: WAL 모드
    if wal:   # WAL 모드는 mode=ro로 열어도 곁 파일(-wal·-shm)을 만들 수 있다 → 원장(+ -wal)을 임시 폴더에 복사해 읽는다
        with tempfile.TemporaryDirectory(prefix="deploy-status-") as tmp:
            copy = Path(tmp) / p.name
            shutil.copyfile(p, copy)
            if Path(f"{p}-wal").exists():
                shutil.copyfile(f"{p}-wal", f"{copy}-wal")
            return _sqlite_select(copy, deployment_id, uri=False)
    return _sqlite_select(p, deployment_id, uri=True)


def _sqlite_select(p, deployment_id, *, uri):
    con = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True) if uri else sqlite3.connect(p)
    try:
        con.row_factory = sqlite3.Row
        cols = {r[1] for r in con.execute("PRAGMA table_info(deployment)")}
        want = [c for c in COLUMNS if c in cols]
        return [dict(r) for r in con.execute(f"SELECT {', '.join(want)} FROM deployment WHERE deployment_id = ? ORDER BY rowid", (deployment_id,))]
    finally:
        con.close()


def pg_rows(dsn, deployment_id):
    """llmops.deployment 줄(runtime_reader SELECT만, 읽기 전용 거래). 표 · schema가 없으면 None."""
    if not dsn:
        raise LedgerUnavailable("접속 정보 없음")
    import psycopg
    from psycopg import errors
    try:
        with psycopg.connect(dsn, connect_timeout=5, options="-c default_transaction_read_only=on") as c:
            has_model = c.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = 'llmops' AND table_name = 'deployment' "
                                  "AND column_name = %s", (MODEL_COLUMN,)).fetchone() is not None   # 0010 유무를 칸 목록으로(오류로 판단하지 않음)
            has_bindings = has_model and c.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = 'llmops' AND table_name = 'deployment' "
                                                   "AND column_name = %s", (MODEL_BINDINGS_COLUMN,)).fetchone() is not None
            cols = COLUMNS + ((MODEL_COLUMN,) if has_model else ()) + ((MODEL_BINDINGS_COLUMN,) if has_bindings else ())
            cur = c.execute(f"SELECT {', '.join(cols)} FROM {PG_TABLE} WHERE deployment_id = %s "
                            "ORDER BY created_at, pack_revision, data_release_id", (deployment_id,))
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except (errors.UndefinedTable, errors.InvalidSchemaName):
        return None
    except psycopg.Error as e:
        raise LedgerUnavailable(type(e).__name__) from None


def _ledger_models(row):
    """장부 줄 model_bindings 글 → {이름: release_id}. 칸이 없거나 읽지 못하면 None(이름 대조 안 함)."""
    raw = row.get(MODEL_BINDINGS_COLUMN)
    try:
        doc = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    return {str(k): (v.get("release_id") if isinstance(v, dict) else v) for k, v in doc.items()}


def refusal(run, rows, model_release_id=None, running_models=None, model_nodes=None):
    """거부 까닭(D-373): 같은 revision · 자료 묶음 줄이 있는데 모델만 안 맞으면 model_mismatch, 그 밖은 not_registered.
    (reason, 글, details 덧). 글은 not_registered면 지금 글 그대로(공유 기록기와 같게), model_mismatch면 실행 · 장부 모델 값을 적음."""
    head = (f"deployment_id={run.get('deployment_id')}, pack_revision={run.get('pack_revision')}, "
            f"data_release_id={run.get('data_release_id')}")
    same = [r for r in rows or [] if r["pack_revision"] == run.get("pack_revision") and r["data_release_id"] == run.get("data_release_id")]
    if not same:
        return "not_registered", f"등록되지 않은 배포입니다: {head}", {"reason": "not_registered"}
    ledger_ids = sorted({str(r.get(MODEL_COLUMN)) for r in same if r.get(MODEL_COLUMN)})[:MAX_LEDGER_VALUES]
    running = dict(running_models or {})
    ledger = next((m for m in (_ledger_models(r) for r in same) if m is not None), None)
    names = sorted(set(running) | set(ledger or {}))
    if ledger is not None:
        names = [n for n in names if running.get(n) != ledger.get(n)]   # 다른 모델만(어느 node의 모델이 다른지)
    models = {n: {"running": running.get(n), "ledger": (ledger or {}).get(n), "nodes": sorted((model_nodes or {}).get(n, []))}
              for n in names[:MAX_MODELS]}
    msg = (f"장부의 모델과 다른 배포입니다: {head} — 실행 모델 {model_release_id}, 장부 모델 {', '.join(ledger_ids) or '-'}"
           "(같은 revision · 자료 묶음 줄)")
    return "model_mismatch", msg, {"reason": "model_mismatch", "model_release_id": model_release_id, "ledger_model_release_ids": ledger_ids,
                                   "models": models}


def registration_check(registration, dsn=None, model_release_id=None, running_models=None, model_nodes=None):
    """TraceWriter registration_check. llmops_db는 공유 기록기 함수 그대로, llmops_pg는 ledger_rows로 세 칸 + 모델 칸(있으면, model_ok) 대조,
    none은 None. model_release_id = 이 배포 Runtime의 packrt release_line 값(trace 형식 바뀜 0, D-326 Q-B).
    llmops_pg 거부면 check.last_refusal에 까닭 · 모델 칸을 남김(부를 때마다 비움, 실행은 한 번에 하나 — runner가 API details에 옮김, D-373)."""
    mode = registration.get("mode")
    if mode == "llmops_db":   # 1주차: llmops.db deployment 표를 읽기 전용으로 대조(세 칸 모두 같아야 함)
        return sqlite_registration_check(registration["path"])
    if mode != "llmops_pg":
        return None

    def check(run):
        check.last_refusal = None
        if run.get("origin") != "deployment":
            raise TraceWriteError("trace_schema", f"배포 서버는 deployment 실행만 기록합니다: origin={run.get('origin')}")
        try:
            rows = pg_rows(dsn, run.get("deployment_id"))
        except LedgerUnavailable as e:
            raise TraceWriteError("trace_unavailable", f"등록 장부를 읽을 수 없습니다: {e}") from None
        ok = run.get("source_kind") == "agent_pack" and any(
            r["pack_revision"] == run.get("pack_revision") and r["data_release_id"] == run.get("data_release_id") and model_ok(r, model_release_id)
            for r in rows or [])
        if not ok:   # not_registered 문구는 공유 기록기와 같게, 모델만 다르면 따로 말함(D-341 · D-373)
            _, msg, extra = refusal(run, rows, model_release_id, running_models, model_nodes)
            check.last_refusal = extra
            raise TraceWriteError("deployment_not_registered", msg)
    check.last_refusal = None
    return check
