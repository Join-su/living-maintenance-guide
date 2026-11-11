"""EV-008 H1 · V-LOAD · V-LOAD-CHECK · V-SECRET · V-NETSCAN (CHG-P03, contract C · D-298 · answers 073 · 074): `packrt.index load`
against a fake connection only — no socket, no database. The H0 versions are `checked` with no connection and go folder → rows → folder
byte for byte; loaded writes data_release one row + index_chunk one row per chunk in one transaction as index_loader; the same version
again is `exists`, another sha under the same id `load_conflict`, another account `load_refused` (only `SELECT current_user`), a
failure part way `load_failed` after a rollback; folder problems are `index_invalid` before any connection; only SELECT and INSERT;
the DSN is in no output; exit codes 0 · 1 · 2; the runtime does not import the loader."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_index_build import H0, pack

from packrt.index import load as load_mod
from packrt.index.__main__ import main
from packrt.index.corpus import line
from packrt.ir.releases import dir_hash

INDEXES = H0 / "indexes"
H0_FOUR = ["ir-manuals-bb52ba51", "ir-manuals-7dd35828", "ir-history-c6510147", "ir-history-543e3885"]
DSN = "postgresql://u:SECRET@127.0.0.1/db"   # loopback only (answer 073); never connected to
ENV = {"LLMOPSAGENT_INDEX_LOADER_DSN": DSN}


class DbError(Exception):
    def __init__(self, sqlstate=None):
        super().__init__(f"server said something about {DSN}")
        self.sqlstate = sqlstate


class FakeDB:
    """A fake psycopg connection: records every statement, keeps committed rows, answers the loader's few SELECTs."""

    def __init__(self, user="index_loader", fail_insert_at=None, count_delta=0, dims_off=0, select_error=None, fail_cursor=False,
                 fail_rollback=False):
        self.user, self.fail_insert_at, self.count_delta, self.dims_off, self.select_error = user, fail_insert_at, count_delta, dims_off, select_error
        self.fail_cursor, self.fail_rollback = fail_cursor, fail_rollback
        self.sql, self.dsns = [], []
        self.data_release, self.index_chunk = [], []   # committed
        self.pending = None
        self.commits = self.rollbacks = self.closed = 0

    def connect(self, dsn):
        self.dsns.append(dsn)
        self.pending = {"data_release": [], "index_chunk": []}
        return self

    def cursor(self):
        if self.fail_cursor:
            raise DbError()
        return Cursor(self)

    def commit(self):
        self.commits += 1
        self.data_release += self.pending["data_release"]
        self.index_chunk += self.pending["index_chunk"]
        self.pending = {"data_release": [], "index_chunk": []}

    def rollback(self):
        self.rollbacks += 1
        if self.fail_rollback:
            raise DbError()
        self.pending = {"data_release": [], "index_chunk": []}

    def close(self):
        self.closed += 1


class Cursor:
    def __init__(self, db):
        self.db, self.one = db, None

    def execute(self, sql, params=()):
        db = self.db
        db.sql.append((" ".join(sql.split()), list(params)))
        if sql == "SELECT current_user":
            self.one = (db.user,)
        elif sql.startswith("SELECT sha256 FROM"):
            if db.select_error:
                raise DbError(db.select_error)
            hit = [r for r in db.data_release if r[0] == params[0]]
            self.one = (hit[0][3],) if hit else None
        elif sql.startswith("INSERT INTO") and ".data_release" in sql:
            db.pending["data_release"].append(tuple(params))
        elif "vector_dims" in sql:
            self.one = (db.dims_off,)
        elif sql.startswith("SELECT count(*)"):
            self.one = (len([r for r in db.pending["index_chunk"] if r[0] == params[0]]) + db.count_delta,)
        else:
            raise AssertionError(f"unexpected SQL {sql}")

    def executemany(self, sql, rows):
        assert sql.startswith("INSERT INTO") and ".index_chunk" in sql
        self.db.sql.append((" ".join(sql.split()), len(rows)))
        for i, r in enumerate(rows):
            if self.db.fail_insert_at == i:
                raise DbError()
            self.db.pending["index_chunk"].append(tuple(r))

    def fetchone(self):
        return self.one


def no_connect(dsn):
    raise AssertionError("no connection may be made")


def h0_pack(tmp_path, change=None):
    return pack(tmp_path, change=change)


def copy_release(tmp_path, rid="ir-manuals-bb52ba51"):
    d = tmp_path / "rel" / rid
    shutil.copytree(INDEXES / rid, d)
    return d


def rehash(folder, edit_manifest=None):
    """After changing a file on purpose: files · index_sha256 written again so only the intended problem is left."""
    import hashlib
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    m["files"] = {f: hashlib.sha256((folder / f).read_bytes()).hexdigest() for f in ("chunks.jsonl", "vectors.json")}
    m["index_sha256"] = dir_hash(folder)
    if edit_manifest:
        edit_manifest(m)
    (folder / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")


def run_load(p, rel, db=None, **kw):
    db = db or FakeDB()
    return load_mod.load(p, rel, env=kw.pop("env", ENV), connect=db.connect, **kw), db


def back_to_folder(db, rid, out):
    """The committed rows of one version written back as a release folder (the manifest from data_release.manifest)."""
    out.mkdir(parents=True)
    rows = [r for r in db.index_chunk if r[0] == rid]
    chunks = [{"id": r[1], "text": r[2], "metadata": json.loads(r[3]), "locator": json.loads(r[4])} for r in rows]
    vectors = {r[1]: [float(x) for x in r[5].strip("[]").split(",")] for r in rows}
    (out / "chunks.jsonl").write_text("".join(line(c) for c in chunks), encoding="utf-8")
    (out / "vectors.json").write_text(json.dumps(vectors, sort_keys=True) + "\n", encoding="utf-8")
    (out / "manifest.json").write_text(next(r[4] for r in db.data_release if r[0] == rid), encoding="utf-8")


# ---- --check: the checks, no connection ----
@pytest.mark.parametrize("rid", H0_FOUR)
def test_check_gives_checked_with_no_connection(tmp_path, rid):
    r = load_mod.load(h0_pack(tmp_path), INDEXES / rid, check=True, env={}, connect=no_connect)
    m = json.loads((INDEXES / rid / "manifest.json").read_text(encoding="utf-8"))
    assert {k: r[k] for k in r if k != "seconds"} == {"status": "checked", "release_id": rid, "schema": "bld", "table": "index_chunk",
                                                       "chunks": m["chunks"], "sha256": m["index_sha256"], "error": None}


# ---- loaded · round trip ----
@pytest.mark.parametrize("rid", H0_FOUR)
def test_folder_rows_folder_is_byte_for_byte(tmp_path, rid):
    """What the loader sends (rows · values) gives the folder back byte for byte; data_release.manifest is sent as the manifest.json
    text itself (a real jsonb column keeps its value, not its bytes — the value is compared too).
    By the fake DB only: a real database normalises the manifest as jsonb and keeps vectors as float4 (manifest_sha256 · index_sha256 stay)."""
    r, db = run_load(h0_pack(tmp_path), INDEXES / rid)
    assert r["status"] == "loaded" and db.commits == 1 and db.rollbacks == 0 and db.closed == 1
    back = tmp_path / "back" / rid
    back_to_folder(db, rid, back)
    for f in ("chunks.jsonl", "vectors.json", "manifest.json"):
        assert (back / f).read_bytes() == (INDEXES / rid / f).read_bytes(), f
    assert dir_hash(back) == dir_hash(INDEXES / rid) == r["sha256"]
    assert json.loads(db.data_release[0][4]) == json.loads((INDEXES / rid / "manifest.json").read_text(encoding="utf-8"))


def test_loaded_writes_one_release_row_and_one_row_per_chunk_in_order(tmp_path):
    rid = "ir-history-c6510147"
    r, db = run_load(h0_pack(tmp_path), INDEXES / rid)
    m = json.loads((INDEXES / rid / "manifest.json").read_text(encoding="utf-8"))
    assert {k: r[k] for k in r if k != "seconds"} == {"status": "loaded", "release_id": rid, "schema": "bld", "table": "index_chunk",
                                                       "chunks": 3, "sha256": m["index_sha256"], "error": None}
    assert db.dsns == [DSN]
    steps = [s for s, _ in db.sql]
    assert steps == ["SELECT current_user",
                     'SELECT sha256 FROM "bld".data_release WHERE release_id = %s',
                     'INSERT INTO "bld".data_release (release_id, store, kind, sha256, manifest, embedding_provider, embedding_model, '
                     "embedding_dim, embedding_mode) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)",
                     'INSERT INTO "bld".index_chunk (release_id, chunk_id, text, metadata, locator, embedding) VALUES (%s, %s, %s, %s::jsonb, '
                     "%s::jsonb, %s::vector)",
                     'SELECT count(*) FROM "bld".index_chunk WHERE release_id = %s',
                     'SELECT count(*) FROM "bld".index_chunk WHERE release_id = %s AND vector_dims(embedding) <> %s']
    assert db.data_release == [(rid, "history", "index", m["index_sha256"], (INDEXES / rid / "manifest.json").read_text(encoding="utf-8"),
                                "openai", "text-embedding-3-small", 64, "fake")]
    chunks = [json.loads(l) for l in (INDEXES / rid / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r[1] for r in db.index_chunk] == [c["id"] for c in chunks]
    assert [json.loads(r[4]) for r in db.index_chunk] == [c["locator"] for c in chunks]   # {doc, record_id, char_start: 0, char_end}
    assert all(set(json.loads(r[4])) == {"doc", "record_id", "char_start", "char_end"} for r in db.index_chunk)


# ---- exists · load_conflict · load_refused ----
def test_the_same_version_again_is_exists_with_nothing_written(tmp_path):
    p, db = h0_pack(tmp_path), FakeDB()
    run_load(p, INDEXES / "ir-manuals-bb52ba51", db)
    n = len(db.sql)
    r, _ = run_load(p, INDEXES / "ir-manuals-bb52ba51", db)
    assert r["status"] == "exists" and r["error"] is None
    assert [s for s, _ in db.sql[n:]] == ["SELECT current_user", 'SELECT sha256 FROM "bld".data_release WHERE release_id = %s']
    assert (db.commits, db.rollbacks, len(db.data_release)) == (1, 1, 1)


def test_the_same_id_with_another_sha_is_load_conflict(tmp_path):
    p, db = h0_pack(tmp_path), FakeDB()
    rid = "ir-manuals-bb52ba51"
    db.data_release.append((rid, "manuals", "index", "0" * 64, "{}", "openai", "text-embedding-3-small", 64, "fake"))
    r, _ = run_load(p, INDEXES / rid, db)
    assert r["status"] == "failed" and r["error"]["code"] == "load_conflict"
    assert not any(s.startswith("INSERT") for s, _ in db.sql) and db.commits == 0 and db.rollbacks == 1


def test_another_account_is_load_refused_after_current_user_only(tmp_path):
    r, db = run_load(h0_pack(tmp_path), INDEXES / "ir-manuals-bb52ba51", FakeDB(user="llmops_app"))
    assert r["status"] == "failed" and r["error"] == {"code": "load_refused", "message": "적재 계정은 index_loader여야 합니다(지금: llmops_app)"}
    assert [s for s, _ in db.sql] == ["SELECT current_user"] and db.commits == 0


@pytest.mark.parametrize("state", ["42501", "3F000", "42P01"])
def test_no_schema_table_or_grant_is_load_refused(tmp_path, state):
    r, db = run_load(h0_pack(tmp_path), INDEXES / "ir-manuals-bb52ba51", FakeDB(select_error=state))
    assert r["error"] == {"code": "load_refused", "message": "'bld.data_release'를 읽을 수 없습니다: DbError"}
    assert not any(s.startswith("INSERT") for s, _ in db.sql) and db.commits == 0


# ---- load_failed: rollback, nothing kept ----
@pytest.mark.parametrize("db, message", [
    (FakeDB(fail_insert_at=2), "release 'ir-manuals-bb52ba51' 적재 실패: DbError"),
    (FakeDB(count_delta=-1), "release 'ir-manuals-bb52ba51'의 적재 줄 수(3)가 조각 수(4)와 다릅니다"),
    (FakeDB(dims_off=1), "release 'ir-manuals-bb52ba51'의 벡터 차원이 dim(64)과 다른 줄이 1개입니다"),
])
def test_a_failure_part_way_rolls_back_and_is_load_failed(tmp_path, db, message):
    r, _ = run_load(h0_pack(tmp_path), INDEXES / "ir-manuals-bb52ba51", db)
    assert r["status"] == "failed" and r["error"] == {"code": "load_failed", "message": message}
    assert (db.commits, db.rollbacks, db.data_release, db.index_chunk) == (0, 1, [], [])


def test_a_connection_error_is_load_failed_as_its_type_only(tmp_path):
    def refuse(dsn):
        raise DbError()
    r = load_mod.load(h0_pack(tmp_path), INDEXES / "ir-manuals-bb52ba51", env=ENV, connect=refuse)
    assert r["error"] == {"code": "load_failed", "message": "DB에 연결할 수 없습니다: DbError"}


@pytest.mark.parametrize("db, message", [(FakeDB(fail_cursor=True), "DB 계정을 확인할 수 없습니다: DbError"),
                                         (FakeDB(fail_rollback=True), "release 'ir-manuals-bb52ba51' 적재 실패: DbError")])
def test_a_driver_error_anywhere_is_a_json_failure_not_a_crash(tmp_path, db, message):
    p, rid = h0_pack(tmp_path), "ir-manuals-bb52ba51"
    if db.fail_rollback:   # the exists path ends with a rollback: the version is already there and that rollback fails
        db.data_release += run_load(p, INDEXES / rid)[1].data_release
    r, _ = run_load(p, INDEXES / rid, db)
    assert r["status"] == "failed" and r["error"] == {"code": "load_failed", "message": message}
    assert "SECRET" not in json.dumps(r, ensure_ascii=False) and db.commits == 0
    assert not any(s.startswith("INSERT") for s, _ in db.sql)


def test_no_dsn_variable_is_load_failed_naming_the_variable_only(tmp_path):
    r = load_mod.load(h0_pack(tmp_path), INDEXES / "ir-manuals-bb52ba51", dsn_env="MY_LOADER_DSN", env=ENV, connect=no_connect)
    assert r["error"] == {"code": "load_failed", "message": "DSN 환경 변수 MY_LOADER_DSN가 없습니다"} and r["release_id"] == "ir-manuals-bb52ba51"


# ---- folder problems: index_invalid before any connection ----
def _chunks(folder, edit):
    rows = [json.loads(l) for l in (folder / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    edit(rows)
    (folder / "chunks.jsonl").write_text("".join(line(c) for c in rows), encoding="utf-8")


def _vectors(folder, edit):
    v = json.loads((folder / "vectors.json").read_text(encoding="utf-8"))
    edit(v)
    (folder / "vectors.json").write_text(json.dumps(v, sort_keys=True) + "\n", encoding="utf-8")


def bad_hash(d):
    (d / "chunks.jsonl").write_text((d / "chunks.jsonl").read_text(encoding="utf-8") + "\n", encoding="utf-8")


def short_vector(d):
    _vectors(d, lambda v: v.__setitem__("MG-A#s1", v["MG-A#s1"][:-1]))
    rehash(d)


def no_locator(d):
    _chunks(d, lambda rows: rows[0].pop("locator"))
    rehash(d)


def other_vector_ids(d):
    _vectors(d, lambda v: v.__setitem__("MG-Z#s9", v.pop("MG-A#s1")))
    rehash(d)


def text_in_vector(d):
    _vectors(d, lambda v: v["MG-A#s1"].__setitem__(0, "x"))
    rehash(d)


def nan_in_vector(d):
    _vectors(d, lambda v: v["MG-A#s1"].__setitem__(0, float("nan")))   # json writes NaN and reads it back
    rehash(d)


def too_big_for_float4(d):
    _vectors(d, lambda v: v["MG-A#s1"].__setitem__(0, 1e39))
    rehash(d)


def bool_in_vector(d):
    _vectors(d, lambda v: v["MG-A#s1"].__setitem__(0, True))
    rehash(d)


def vectors_as_a_list(d):
    v = json.loads((d / "vectors.json").read_text(encoding="utf-8"))
    (d / "vectors.json").write_text(json.dumps(list(v.values())) + "\n", encoding="utf-8")
    rehash(d)


def nan_in_metadata(d):
    _chunks(d, lambda rows: rows[0]["metadata"].__setitem__("kind", float("nan")))   # line() writes NaN, as json.dumps does
    rehash(d)


def nul_in_text(d):
    _chunks(d, lambda rows: rows[0].__setitem__("text", rows[0]["text"] + "\x00"))
    rehash(d)


def nul_in_metadata(d):
    _chunks(d, lambda rows: rows[0]["metadata"].__setitem__("group", "T\x00W"))   # written as \u0000 inside the JSON line
    rehash(d)


def nan_in_manifest(d):
    rehash(d, lambda m: m.__setitem__("documents", float("nan")))


def other_builder(d):
    rehash(d, lambda m: m.__setitem__("builder", {"name": "task10.load", "version": 1}))


def no_builder(d):
    rehash(d, lambda m: m.pop("builder"))


def store_not_in_pack(d):
    rehash(d, lambda m: m.__setitem__("store", "notes"))


def store_is_a_table(d):
    rehash(d, lambda m: m.__setitem__("store", "rows"))


def no_mode(d):
    rehash(d, lambda m: m["embedding"].pop("mode"))


def with_a_table_store(p):
    p["stores"]["rows"] = {"type": "table", "schema": "Row", "key": "row_id"}
    p["schemas"]["Row"] = "schemas/hit.json"


def other_provider(p):
    p["stores"]["manuals"]["index"]["embedding"]["model"] = "text-embedding-3-large"


def needs_absent_metadata(p):
    p["stores"]["manuals"]["metadata_required"] = ["kind", "page"]


@pytest.mark.parametrize("folder_edit, pack_change, message", [
    (bad_hash, None, "release 'ir-manuals-bb52ba51'의 파일 hash가 manifest와 다릅니다: chunks.jsonl"),
    (short_vector, None, "조각 'MG-A#s1'의 벡터 길이가 release dim(64)과 다릅니다"),
    (no_locator, None, "조각 'MG-A#s1'에 id · text · metadata · locator가 모두 있어야 합니다"),
    (other_vector_ids, None, "release 'ir-manuals-bb52ba51'의 조각 id와 벡터 id가 맞지 않습니다"),
    (text_in_vector, None, "조각 'MG-A#s1'의 벡터에 float4로 담을 수 있는 수가 아닌 값이 있습니다"),
    (nan_in_vector, None, "조각 'MG-A#s1'의 벡터에 float4로 담을 수 있는 수가 아닌 값이 있습니다"),
    (too_big_for_float4, None, "조각 'MG-A#s1'의 벡터에 float4로 담을 수 있는 수가 아닌 값이 있습니다"),
    (bool_in_vector, None, "조각 'MG-A#s1'의 벡터에 float4로 담을 수 있는 수가 아닌 값이 있습니다"),
    (vectors_as_a_list, None, "release 'ir-manuals-bb52ba51'의 조각 id와 벡터 id가 맞지 않습니다"),
    (nan_in_metadata, None, "release 폴더 JSON에 NaN이 있습니다(PostgreSQL jsonb가 받지 않음)"),
    (nan_in_manifest, None, "release 폴더 JSON에 NaN이 있습니다(PostgreSQL jsonb가 받지 않음)"),
    (nul_in_text, None, "조각 'MG-A#s1'에 NUL 글자(\\x00)가 있습니다(PostgreSQL text · jsonb가 받지 않음)"),
    (nul_in_metadata, None, "조각 'MG-A#s1'에 NUL 글자(\\x00)가 있습니다(PostgreSQL text · jsonb가 받지 않음)"),
    (other_builder, None, "release 'ir-manuals-bb52ba51'는 packrt.index build가 만든 version이 아닙니다(builder {'name': 'task10.load', 'version': 1})"),
    (no_builder, None, "release 'ir-manuals-bb52ba51'는 packrt.index build가 만든 version이 아닙니다(builder None)"),
    (store_not_in_pack, None, "release 'ir-manuals-bb52ba51'의 store 'notes'가 Pack의 vector store가 아닙니다"),
    (store_is_a_table, with_a_table_store, "release 'ir-manuals-bb52ba51'의 store 'rows'가 Pack의 vector store가 아닙니다"),
    (no_mode, None, "release 'ir-manuals-bb52ba51'에 임베딩 mode(real | fake) · dim이 없습니다(E-3)"),
    (None, other_provider, "release 'ir-manuals-bb52ba51'의 임베딩 openai/text-embedding-3-small이 Pack 선언 openai/text-embedding-3-large과 다릅니다(E-3)"),
    (None, needs_absent_metadata, "조각 'MG-A#s1'에 metadata_required ['page']가 없습니다"),
])
@pytest.mark.parametrize("check", [True, False])
def test_folder_problems_are_index_invalid_before_any_connection(tmp_path, folder_edit, pack_change, message, check):
    d = copy_release(tmp_path)
    if folder_edit:
        folder_edit(d)
    r = load_mod.load(h0_pack(tmp_path, change=pack_change), d, check=check, env=ENV, connect=no_connect)
    assert r["status"] == "failed" and r["error"] == {"code": "index_invalid", "message": message}


def test_the_schema_is_the_pack_id(tmp_path):
    def rename(p):
        p["pack"]["id"] = "c1a15"
    r, db = run_load(h0_pack(tmp_path, change=rename), INDEXES / "ir-manuals-bb52ba51")
    assert r["schema"] == "c1a15" and all('"c1a15".' in s for s, _ in db.sql[1:])


# ---- only SELECT and INSERT · the DSN in no output ----
FORBIDDEN = re.compile(r"\b(UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COPY)\b", re.I)


def test_only_select_and_insert_are_ever_sent(tmp_path):
    p, db = h0_pack(tmp_path), FakeDB()
    for rid in H0_FOUR:
        run_load(p, INDEXES / rid, db)
    run_load(p, INDEXES / H0_FOUR[0], db)   # exists
    db.data_release[0] = db.data_release[0][:3] + ("0" * 64,) + db.data_release[0][4:]
    run_load(p, INDEXES / H0_FOUR[0], db)   # load_conflict
    failed = FakeDB(fail_insert_at=0)
    run_load(p, INDEXES / H0_FOUR[0], failed)
    db.sql += failed.sql
    assert failed.sql and db.sql and all(s.split()[0] in ("SELECT", "INSERT") and not FORBIDDEN.search(s) for s, _ in db.sql)
    src = Path(load_mod.__file__).read_text(encoding="utf-8")
    sent = re.findall(r"cur\.execute(?:many)?\(f?['\"]([A-Z]+)", src)
    assert sent and set(sent) <= {"SELECT", "INSERT"}


def test_the_dsn_is_in_no_output(tmp_path, monkeypatch, capsys):
    p = h0_pack(tmp_path)
    monkeypatch.setenv("LLMOPSAGENT_INDEX_LOADER_DSN", DSN)

    def leaky(dsn):
        raise DbError()
    monkeypatch.setattr(load_mod, "psycopg_connect", leaky)
    for argv in (["load", "--pack", str(p), "--release", str(INDEXES / "ir-manuals-bb52ba51"), "--json"],
                 ["load", "--pack", str(p), "--release", str(INDEXES / "ir-manuals-bb52ba51")]):
        assert main(argv) == 1
        out, err = capsys.readouterr()
        assert "SECRET" not in out + err and DSN not in out + err and "127.0.0.1" not in out + err
    for db in (FakeDB(user="llmops_app"), FakeDB(select_error="42501"), FakeDB(fail_insert_at=1)):
        r, _ = run_load(p, INDEXES / "ir-manuals-bb52ba51", db)
        assert "SECRET" not in json.dumps(r, ensure_ascii=False)


# ---- command line ----
def cli(args, tmp_path, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k != "LLMOPSAGENT_INDEX_LOADER_DSN"}
    env.update(PYTHONDONTWRITEBYTECODE="1", **(env_extra or {}))
    return subprocess.run([sys.executable, "-B", "-m", "packrt.index", "load", *args], capture_output=True, text=True, env=env, cwd=tmp_path)


def test_cli_exit_codes(tmp_path):
    p, rel = str(h0_pack(tmp_path)), str(INDEXES / "ir-history-543e3885")
    ok = cli(["--pack", p, "--release", rel, "--check", "--json"], tmp_path)
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["status"] == "checked" and list(json.loads(ok.stdout)) == ["status", "release_id", "schema", "table",
                                                                                              "chunks", "sha256", "seconds", "error"]
    for bad in (["--pack", p, "--release", rel, "--check", "--dsn-env", "X", "--json"], ["--pack", p, "--release", rel, "--check", "--dsn-env", "", "--json"],
                ["--pack", p, "--release", rel, "--dsn-env", "", "--json"], ["--pack", p, "--json"], ["--release", rel]):
        u = cli(bad, tmp_path)
        assert u.returncode == 2 and u.stdout == "", bad
    missing = cli(["--pack", p, "--release", rel, "--json"], tmp_path)   # no DSN variable: stops before connecting
    assert missing.returncode == 1
    assert json.loads(missing.stdout)["error"] == {"code": "load_failed", "message": "DSN 환경 변수 LLMOPSAGENT_INDEX_LOADER_DSN가 없습니다"}
    d = copy_release(tmp_path, "ir-history-543e3885")
    bad_hash(d)
    broken = cli(["--pack", p, "--release", str(d), "--check", "--json"], tmp_path)
    assert broken.returncode == 1 and json.loads(broken.stdout)["error"]["code"] == "index_invalid"


# ---- the runtime does not import the loader (PRES-P03) ----
def test_the_runtime_does_not_import_the_loader_or_psycopg(tmp_path):
    code = ("import sys, packrt, packrt.ir, packrt.run, packrt.run.engine, packrt.index, packrt.index.build; "
            "bad = [m for m in ('packrt.index.load', 'psycopg') if m in sys.modules]; "
            "import packrt.index.load; bad += ['psycopg'] if 'psycopg' in sys.modules else []; print(bad)")
    out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, cwd=tmp_path, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
