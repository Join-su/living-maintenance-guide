"""move-to-pg (EV-029 CHG-133): LLMOps' records from the old SQLite file llmops.db into the shared PostgreSQL `llmops` schema
(migration 0005), as llmops_app — the server's account; db_owner and the superuser are not used.

- check (default): read the old file (SQLite mode=ro) — rows per table, deployment.pack_commit old → new, and with --path-map a
  preview of the path columns that would change. Nothing is written.
- apply: only when every llmops table of the target is empty, in one transaction: rows as they are (identity values kept, the next
  value set with setval — the sequence UPDATE grant of migration 0005), then per table the row count and a content hash are compared
  with the expected content: the old file's content with only deployment.pack_commit replaced by the new number. Then the moved
  mark next to the old file and move-record.json. A stop anywhere writes nothing (one transaction, no mark).
- reverse: the PostgreSQL records into a NEW SQLite file in a new data folder (SELECT only); the old file is never overwritten.

pack_commit rule (deploy report 023 section 2, step 6; GAP-009 (라)): ① empty stays empty ② the old 40 characters: a 40-character
value as it is; a short one only when the same revision's row has a 40-character value starting with it, exactly one commit-map old
number starts with it, and the two are the same ③ new = its commit-map pair, missing or 0000… stops ④ with a pack tag, the Pack
repository's `rev-parse <tag>^{commit}` must be the new number (dev-local rows stop at ③) ⑤ any mismatch stops the whole move
⑥ one record line per row. Revision and data release id never change.

No DSN, password or row value is printed; messages carry deployment ids, table names and counts."""
import hashlib
import json
import re
import sqlite3
import subprocess
import time
from pathlib import Path

from .store import MOVED_MARK, SCHEMA, TABLES

ORD_TABLES = ("eval_suites", "input_sets", "bundles", "briefs")   # SQLite rowid order ↔ PostgreSQL `ord` identity
IDENTITY = {"decision": "decision_id", "release_edit": "edit_id", "case_promotion": "promotion_id"}
INSERT_ORDER = ("projects", "validation_data", "releases", "runs", "run_cases", "input_sets", "bundles", "briefs", "verdicts", "pack_releases",
                "eval_suites", "run_events", "decision", "deployment", "evaluation_case", "release_edit", "node_layout", "case_promotion")
ORDER_BY = {"projects": "id", "validation_data": "project_id", "releases": "id", "runs": "id", "run_cases": "run_id, idx",
            "verdicts": "run_id, seq", "pack_releases": "release_id", "run_events": "run_id, seq", "decision": "decision_id",
            "deployment": "deployment_id, pack_revision, data_release_id", "evaluation_case": "evaluation_run_id, case_index",
            "release_edit": "edit_id", "node_layout": "owner", "case_promotion": "promotion_id"}   # ord tables: by their order column
# the path columns a later VPS move would rewrite (D-77) — previewed only, never changed by this tool
PATH_COLUMNS = (("releases", "path"), ("pack_releases", "data"), ("runs", "meta"), ("release_edit", "base_dir"), ("eval_suites", "data"),
                ("decision", "evidence"))
ZERO = re.compile(r"^0+$")


class MoveStop(Exception):
    """The move stops; nothing is written. `deployment_id` and `branch` name the pack_commit rule that stopped it, when it was one."""
    def __init__(self, message, deployment_id=None, branch=None):
        super().__init__(message)
        self.deployment_id, self.branch = deployment_id, branch


assert set(INSERT_ORDER) == set(TABLES)
_TRANSFORM_HOOK = None   # test hook only (V-MOVE-PACKCOMMIT ㉨): a function given each deployment row about to be written


# ------------------------------------------------------------------ the old file
def _columns(db, table):
    return [(r[1], (r[2] or "").upper()) for r in db.execute(f"PRAGMA table_info({table})")]


def read_old(db_path):
    """{table: [row dict, …]} of the 18 tables in a fixed order (ord tables carry `ord` = rowid), read with mode=ro. A table the old
    file does not have yet (case_promotion before the server first started with OPS-3) is empty. The candidate tables are not read."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    try:
        have = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        out = {}
        for t in INSERT_ORDER:
            if t not in have:
                out[t] = []
                continue
            cols = _columns(db, t)
            names = [c for c, _ in cols]
            if t in ORD_TABLES:
                rows = db.execute(f"SELECT {', '.join(names)}, rowid FROM {t} ORDER BY rowid").fetchall()
                out[t] = [_typed(dict(zip(names + ["ord"], r)), dict(cols)) for r in rows]
            else:
                rows = db.execute(f"SELECT {', '.join(names)} FROM {t} ORDER BY {_order(t, names)}").fetchall()
                out[t] = [_typed(dict(zip(names, r)), dict(cols)) for r in rows]
        return out
    finally:
        db.close()


def _typed(row, types):
    """Values as PostgreSQL gives them back: REAL columns as float (SQLite may hold an integer there), INTEGER as int."""
    for k, v in row.items():
        if v is None:
            continue
        t = types.get(k, "INTEGER" if k == "ord" else "")
        if t == "REAL":
            row[k] = float(v)
        elif t == "INTEGER":
            row[k] = int(v)
    return row


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ------------------------------------------------------------------ pack_commit rule
def read_commit_map(path):
    """filter-repo's commit-map ('old new' per line, header 'old new')."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{40}", parts[0]):
            out[parts[0]] = parts[1]
    return out


def git_rev_parse(pack_repo):
    """rev_parse(tag) → the commit the Pack repository's tag points to (read only), or None."""
    def rev_parse(tag):
        r = subprocess.run(["git", "-C", str(pack_repo), "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"], capture_output=True, text=True,
                           timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    return rev_parse


def new_pack_commits(rows, commit_map, rev_parse):
    """For every deployment row: (new pack_commit, record line). Raises MoveStop naming the row and the branch (㉠ ~ ㉧)."""
    full = {}   # revision → the 40-character values seen on its rows
    for r in rows:
        if r.get("pack_commit") and len(r["pack_commit"]) == 40:
            full.setdefault(r["pack_revision"], set()).add(r["pack_commit"])
    out = []
    for r in rows:
        dep, old, tag = r["deployment_id"], r.get("pack_commit"), r.get("pack_tag")
        if not old:                                                                                  # ①
            out.append((old, f"{dep}: (빈 칸) → (빈 칸)"))
            continue
        if len(old) == 40:                                                                           # ②
            old40 = old
        else:
            starts = [o for o in commit_map if o.startswith(old)]
            if not starts:
                raise MoveStop(f"{dep}: 짧은 번호 {old}로 시작하는 commit-map 옛 번호가 없습니다(㉠)", dep, "㉠")
            if len(starts) > 1:
                raise MoveStop(f"{dep}: 짧은 번호 {old}로 시작하는 commit-map 옛 번호가 {len(starts)}개입니다(㉡)", dep, "㉡")
            same = full.get(r["pack_revision"], set())
            if not same:
                raise MoveStop(f"{dep}: 같은 revision의 40자 줄이 없습니다(㉢)", dep, "㉢")
            matching = {s for s in same if s.startswith(old)}
            if not matching:
                raise MoveStop(f"{dep}: 같은 revision 줄의 40자가 {old}로 시작하지 않습니다(㉣)", dep, "㉣")
            if len(matching) != 1 or next(iter(matching)) != starts[0]:
                raise MoveStop(f"{dep}: 같은 revision 줄의 40자와 commit-map 옛 번호가 다릅니다(㉤)", dep, "㉤")
            old40 = starts[0]
        new = commit_map.get(old40)                                                                  # ③
        if not new:
            raise MoveStop(f"{dep}: commit-map에 짝이 없습니다(㉥)", dep, "㉥")
        if ZERO.match(new):
            raise MoveStop(f"{dep}: commit-map 짝이 0000…입니다(㉦)", dep, "㉦")
        if tag and not dep.endswith("@dev-local"):                                                   # ④
            got = rev_parse(tag)
            if got != new:
                raise MoveStop(f"{dep}: 태그 {tag}가 가리키는 commit이 새 번호와 다릅니다(㉧)", dep, "㉧")
        out.append((new, f"{dep}: {old}({len(old)}) → {new} ({tag or '태그 없음'})"))             # ⑥
    return out


# ------------------------------------------------------------------ expected content and hashes
def expected_content(old, pack_commits):
    exp = {t: [dict(r) for r in rows] for t, rows in old.items()}
    for r, (new, _) in zip(exp["deployment"], pack_commits):
        r["pack_commit"] = new
    return exp


# EV-044 (A2-N2 · A3-N1): deployment rows that differ only by model release sort the same in SQLite and PostgreSQL (NULL placed
# by COALESCE, not by each database's NULL order). Used only when the rows carry the model column — an older file is read by the
# columns it has.
DEPLOYMENT_ORDER_V2 = "deployment_id, pack_revision, data_release_id, COALESCE(model_release_id, ''), created_at"


def _order(table, columns):
    if table == "deployment" and "model_release_id" in columns:
        return DEPLOYMENT_ORDER_V2
    return ORDER_BY[table]


def table_hash(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _pg_rows(conn, table, columns):
    order = "ord" if table in ORD_TABLES else _order(table, columns)
    cur = conn.execute(f"SELECT {', '.join(columns)} FROM llmops.{table} ORDER BY {order}")
    return [dict(zip(columns, r)) for r in cur.fetchall()]


def _pg_columns(conn, table):
    return [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'llmops' AND table_name = %s "
                                       "ORDER BY ordinal_position", (table,)).fetchall()]


def target_counts(conn):
    return {t: conn.execute(f"SELECT count(*) FROM llmops.{t}").fetchone()[0] for t in INSERT_ORDER}


# ------------------------------------------------------------------ check · apply · reverse
def check(old_db, commit_map, rev_parse, path_map=None):
    """What a move would do — counts, pack_commit lines, and with path_map ('old prefix', 'new prefix') a preview. Writes nothing."""
    old = read_old(old_db)
    out = {"tables": {t: len(rows) for t, rows in old.items()}, "pack_commit": [line for _, line in new_pack_commits(old["deployment"], commit_map, rev_parse)]}
    if path_map:
        a, b = path_map
        prev = {}
        for t, col in PATH_COLUMNS:
            hits = [r for r in old[t] if isinstance(r.get(col), str) and a in r[col]]
            if hits:
                ex = hits[0][col]
                i = ex.index(a)
                before = ex[max(0, i - 20): i + len(a) + 40]
                prev[f"{t}.{col}"] = {"rows": len(hits), "example": {"before": before, "after": before.replace(a, b, 1)}}
        out["path_map"] = {"from": a, "to": b, "columns": prev, "written": 0}
    return out


def apply(old_db, conn, commit_map, rev_parse, record_dir=None):
    """Move the old file's records into an empty llmops schema in one transaction (conn: llmops_app, autocommit off is set here).
    Returns the move record. On any stop nothing stays in the target and no mark is written."""
    if not conn.autocommit:
        raise MoveStop("이전 연결은 autocommit이어야 합니다(이전 전체가 한 트랜잭션)")
    old_db = Path(old_db)
    sha_before = file_sha256(old_db)
    old = read_old(old_db)
    pack_commits = new_pack_commits(old["deployment"], commit_map, rev_parse)
    expected = expected_content(old, pack_commits)
    counts = target_counts(conn)
    if any(counts.values()):
        raise MoveStop("대상 llmops 표가 비어 있지 않습니다: " + ", ".join(f"{t} {n}" for t, n in counts.items() if n))
    record = {"moved_at": time.time(), "old_file": str(old_db), "old_sha256": sha_before, "tables": {}, "pack_commit": [line for _, line in pack_commits]}
    where = Path(record_dir) if record_dir else old_db.parent
    record_file, mark = where / "move-record.json", old_db.parent / MOVED_MARK
    if mark.exists() or record_file.exists():   # only files this run writes are removed on a stop
        raise MoveStop("이미 이전됨 표시 또는 move-record.json이 있습니다 — 이전하지 않음")
    try:
        with conn.transaction():
            for t in INSERT_ORDER:
                rows = expected[t]
                if not rows:
                    continue
                cols = list(rows[0])
                for r in rows:
                    if t == "deployment" and _TRANSFORM_HOOK:
                        r = _TRANSFORM_HOOK(dict(r))
                    conn.execute(f"INSERT INTO llmops.{t} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", [r[c] for c in cols])
            for t, col in list(IDENTITY.items()) + [(t, "ord") for t in ORD_TABLES]:   # next values continue after the moved ones
                seq = f"llmops.{t}_{col}_seq"
                top = conn.execute(f"SELECT max({col}) FROM llmops.{t}").fetchone()[0]
                conn.execute("SELECT setval(%s, %s, %s)", (seq, top or 1, top is not None))
            keys = {(r["deployment_id"], r["pack_revision"], r["data_release_id"]) for r in old["deployment"]}   # ㉨ revision · drs never change
            for r in (_pg_rows(conn, "deployment", ["deployment_id", "pack_revision", "data_release_id"]) if old["deployment"] else []):
                if (r["deployment_id"], r["pack_revision"], r["data_release_id"]) not in keys:
                    raise MoveStop(f"{r['deployment_id']}: revision 또는 자료 묶음이 옛 줄과 다릅니다(㉨)", r["deployment_id"], "㉨")
            for t in INSERT_ORDER:                                                       # count and content against the expected
                cols = list(expected[t][0]) if expected[t] else _pg_columns(conn, t)
                got = _pg_rows(conn, t, cols)
                want = expected[t]
                if len(got) != len(want) or table_hash(got) != table_hash(want):
                    raise MoveStop(f"{t}: 옮긴 뒤 줄 수 · 내용이 기대와 다릅니다(줄 {len(got)} / {len(want)})", None, t)
                record["tables"][t] = {"rows": len(want), "sha256": table_hash(want)}
            if file_sha256(old_db) != sha_before:      # still inside the transaction: a changed old file rolls everything back
                raise MoveStop("옛 파일이 이전 중에 바뀌었습니다 — 쓴 것 없음")
            # the record and the moved mark are written before the commit and removed again if the commit does not happen,
            # so 'records in PostgreSQL' and 'old file marked as moved' go together
            record_file.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            mark.write_text(f"moved to PostgreSQL llmops at {time.strftime('%Y-%m-%d %H:%M:%S')} — move-record.json\n", encoding="utf-8")
    except BaseException:
        for f in (record_file, mark):
            f.unlink(missing_ok=True)
        raise
    return record


def reverse(conn, out_dir):
    """The PostgreSQL records into a new SQLite llmops.db in out_dir (SELECT only). Refuses an existing llmops.db there."""
    out_dir = Path(out_dir)
    target = out_dir / "llmops.db"
    if target.exists() or (out_dir / MOVED_MARK).exists():
        raise MoveStop(f"새 data 폴더가 아닙니다(이미 llmops.db 또는 이전됨 표시가 있음): {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target)
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(SCHEMA)
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS deployment_key ON deployment "
                   "(deployment_id, pack_revision, data_release_id, COALESCE(model_release_id, ''))")   # EV-044, as Store makes it
        with db:
            for t in INSERT_ORDER:
                cols = [c for c in _pg_columns(conn, t)]
                rows = _pg_rows(conn, t, cols)
                for r in rows:
                    names = [c for c in cols if c != "ord"] + (["rowid"] if t in ORD_TABLES else [])
                    vals = [r[c] for c in cols if c != "ord"] + ([r["ord"]] if t in ORD_TABLES else [])
                    db.execute(f"INSERT INTO {t} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})", vals)
    finally:
        db.close()
    return target
