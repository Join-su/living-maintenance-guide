"""`packrt.index load` (EV-008 CHG-P03, contract C · D-260 (2) · D-283 · D-298): one vector DB version made by `packrt.index build`
into the shared PostgreSQL — schema = the Pack id, `data_release` one row + `index_chunk` one row per chunk, in one transaction, as
the account `index_loader` only. Everything is checked before anything is written (index_invalid); `--check` stops there. Only SELECT
and INSERT: same id and sha is `exists`, same id and other sha is `load_conflict`, nothing is updated or deleted. The DSN comes from
the environment variable --dsn-env names and appears in no output (errors keep the exception type only). psycopg is imported when
connecting; the runtime never imports this module (PRES-P03). CLI only in 0.12.1 (no public name)."""
import json
import math
import os
import time
from pathlib import Path

from ..ir import load_pack
from ..ir import pg
from ..ir.releases import ReleaseError, check_index
from .recipe import BUILDER

LOADER_ACCOUNT = "index_loader"
DSN_ENV = "LLMOPSAGENT_INDEX_LOADER_DSN"
TABLE = "index_chunk"   # the table of every packrt.index build release (D-298: chosen by manifest builder, no manifest field)
MODES = ("real", "fake")
CODES = ("index_invalid", "load_refused", "load_conflict", "load_failed")
FLOAT4_MAX = 3.4028234663852886e38   # pgvector stores float4: a larger finite value would fail only in the database
REFUSED_STATES = {"42501", "3F000", "42P01"}   # insufficient_privilege · invalid_schema_name · undefined_table: account · schema · grant


class LoadError(RuntimeError):
    def __init__(self, code, message):
        assert code in CODES, code
        self.code = code
        super().__init__(message)


def _vector_text(vec):
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _no_constant(name):
    """json parse_constant: NaN · Infinity are valid Python JSON but PostgreSQL jsonb refuses them (22P02) — refuse before the DB."""
    raise LoadError("index_invalid", f"release 폴더 JSON에 {name}이 있습니다(PostgreSQL jsonb가 받지 않음)")


def _has_nul(value):
    """A NUL character anywhere (text · keys · values): PostgreSQL text and jsonb refuse it."""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_nul(v) for v in value)
    return False


def read_release(pack, release):
    """The checked contents to load: {schema, store, release_id, sha256, manifest_text, embedding, rows}; raises LoadError(index_invalid)."""
    folder = Path(release)
    res = load_pack(Path(pack))
    if not res.ok:
        raise LoadError("index_invalid", f"Pack을 불러올 수 없습니다: {res.errors[0]['message'] if res.errors else '알 수 없는 오류'}")
    try:
        schema = pg.ident((res.doc.get("pack") or {}).get("id"), "schema(Pack id)")
    except pg.PgError as e:
        raise LoadError("index_invalid", str(e)) from None
    try:
        check_index(folder)
        manifest_text = (folder / "manifest.json").read_text(encoding="utf-8")
        m = json.loads(manifest_text, parse_constant=_no_constant)
        chunks = [json.loads(l, parse_constant=_no_constant) for l in (folder / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        vectors = json.loads((folder / "vectors.json").read_text(encoding="utf-8"))
    except ReleaseError as e:
        raise LoadError("index_invalid", str(e)) from None
    except (OSError, ValueError) as e:
        raise LoadError("index_invalid", f"release 폴더를 읽을 수 없습니다: {type(e).__name__}") from None
    rid = m.get("release_id")
    if _has_nul(m):
        raise LoadError("index_invalid", f"release '{rid}'의 manifest에 NUL 글자(\\x00)가 있습니다(PostgreSQL이 받지 않음)")
    if m.get("builder") != BUILDER:
        raise LoadError("index_invalid", f"release '{rid}'는 packrt.index build가 만든 version이 아닙니다(builder {m.get('builder')})")
    store = m.get("store")
    sdoc = (res.doc.get("stores") or {}).get(store)
    if not isinstance(sdoc, dict) or sdoc.get("type") != "vector":
        raise LoadError("index_invalid", f"release '{rid}'의 store '{store}'가 Pack의 vector store가 아닙니다")
    emb, want = m.get("embedding") or {}, (sdoc.get("index") or {}).get("embedding") or {}
    if (emb.get("provider"), emb.get("model")) != (want.get("provider"), want.get("model")):
        raise LoadError("index_invalid", f"release '{rid}'의 임베딩 {emb.get('provider')}/{emb.get('model')}이 Pack 선언 "
                                         f"{want.get('provider')}/{want.get('model')}과 다릅니다(E-3)")
    if emb.get("mode") not in MODES or not isinstance(emb.get("dim"), int) or isinstance(emb.get("dim"), bool) or emb["dim"] <= 0:
        raise LoadError("index_invalid", f"release '{rid}'에 임베딩 mode(real | fake) · dim이 없습니다(E-3)")
    ids = [c.get("id") if isinstance(c, dict) and isinstance(c.get("id"), str) else None for c in chunks]
    if not isinstance(vectors, dict) or None in ids or len(set(ids)) != len(ids) or set(ids) != set(vectors):
        raise LoadError("index_invalid", f"release '{rid}'의 조각 id와 벡터 id가 맞지 않습니다")
    if m.get("chunks") != len(chunks):
        raise LoadError("index_invalid", f"release '{rid}'의 조각 수({len(chunks)})가 manifest chunks({m.get('chunks')})와 다릅니다")
    required = sdoc.get("metadata_required") or []
    rows = []
    for c in chunks:
        if not (isinstance(c["id"], str) and c["id"] and isinstance(c.get("text"), str) and isinstance(c.get("metadata"), dict)
                and isinstance(c.get("locator"), dict)):
            raise LoadError("index_invalid", f"조각 '{c['id']}'에 id · text · metadata · locator가 모두 있어야 합니다")
        if _has_nul([c["id"], c["text"], c["metadata"], c["locator"]]):
            raise LoadError("index_invalid", f"조각 '{c['id']}'에 NUL 글자(\\x00)가 있습니다(PostgreSQL text · jsonb가 받지 않음)")
        missing = [k for k in required if c["metadata"].get(k) is None]
        if missing:
            raise LoadError("index_invalid", f"조각 '{c['id']}'에 metadata_required {missing}가 없습니다")
        vec = vectors[c["id"]]
        if not isinstance(vec, list) or len(vec) != emb["dim"]:
            raise LoadError("index_invalid", f"조각 '{c['id']}'의 벡터 길이가 release dim({emb['dim']})과 다릅니다")
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and abs(x) <= FLOAT4_MAX for x in vec):
            raise LoadError("index_invalid", f"조각 '{c['id']}'의 벡터에 float4로 담을 수 있는 수가 아닌 값이 있습니다")
        rows.append((rid, c["id"], c["text"], json.dumps(c["metadata"], ensure_ascii=False, sort_keys=True, allow_nan=False),
                     json.dumps(c["locator"], ensure_ascii=False, sort_keys=True, allow_nan=False), _vector_text(vec)))
    return {"schema": schema, "store": store, "release_id": rid, "sha256": m.get("index_sha256"), "manifest_text": manifest_text,
            "embedding": {k: emb[k] for k in ("provider", "model", "dim", "mode")}, "rows": rows}


def psycopg_connect(dsn):
    """A writing session with autocommit off: the whole load is one transaction."""
    import psycopg
    return psycopg.connect(dsn, connect_timeout=5, autocommit=False)


def _db_error(e, what):
    code = "load_refused" if getattr(e, "sqlstate", None) in REFUSED_STATES else "load_failed"
    return LoadError(code, f"{what}: {type(e).__name__}")


def write(conn, rel):
    """One transaction: 'loaded' or 'exists'; raises LoadError (load_refused · load_conflict · load_failed) after a rollback."""
    s, rid, emb = rel["schema"], rel["release_id"], rel["embedding"]
    try:
        try:
            cur = conn.cursor()
            cur.execute("SELECT current_user")
            user = cur.fetchone()[0]
        except Exception as e:  # noqa: BLE001 - type only
            raise _db_error(e, "DB 계정을 확인할 수 없습니다") from None
        if user != LOADER_ACCOUNT:
            raise LoadError("load_refused", f"적재 계정은 {LOADER_ACCOUNT}여야 합니다(지금: {user})")
        try:
            cur.execute(f'SELECT sha256 FROM "{s}".data_release WHERE release_id = %s', (rid,))
            row = cur.fetchone()
        except Exception as e:  # noqa: BLE001 - a missing schema or table, no privilege: type only
            raise _db_error(e, f"'{s}.data_release'를 읽을 수 없습니다") from None
        if row is not None:
            if row[0] != rel["sha256"]:
                raise LoadError("load_conflict", f"release '{rid}'가 이미 다른 내용(sha256)으로 적재돼 있습니다 — 덮어쓰지 않습니다")
            conn.rollback()
            return "exists"
        try:
            cur.execute(f'INSERT INTO "{s}".data_release (release_id, store, kind, sha256, manifest, embedding_provider, embedding_model, '
                        f"embedding_dim, embedding_mode) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)",
                        (rid, rel["store"], "index", rel["sha256"], rel["manifest_text"], emb["provider"], emb["model"], emb["dim"], emb["mode"]))
            cur.executemany(f'INSERT INTO "{s}".{TABLE} (release_id, chunk_id, text, metadata, locator, embedding) '
                            f"VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::vector)", rel["rows"])
            cur.execute(f'SELECT count(*) FROM "{s}".{TABLE} WHERE release_id = %s', (rid,))
            n = cur.fetchone()[0]
            cur.execute(f'SELECT count(*) FROM "{s}".{TABLE} WHERE release_id = %s AND vector_dims(embedding) <> %s', (rid, emb["dim"]))
            off = cur.fetchone()[0]
        except Exception as e:  # noqa: BLE001 - type only
            raise _db_error(e, f"release '{rid}' 적재 실패") from None
        if n != len(rel["rows"]):
            raise LoadError("load_failed", f"release '{rid}'의 적재 줄 수({n})가 조각 수({len(rel['rows'])})와 다릅니다")
        if off != 0:
            raise LoadError("load_failed", f"release '{rid}'의 벡터 차원이 dim({emb['dim']})과 다른 줄이 {off}개입니다")
        try:
            conn.commit()
        except Exception as e:  # noqa: BLE001 - type only
            raise _db_error(e, f"release '{rid}' commit 실패") from None
        return "loaded"
    except BaseException as e:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 - the first error is the one reported
            pass
        if isinstance(e, LoadError) or not isinstance(e, Exception):
            raise
        raise _db_error(e, f"release '{rid}' 적재 실패") from None   # e.g. the exists rollback itself failed: type only


def load(pack, release, dsn_env=None, check=False, env=None, connect=None):
    """The stdout result {status, release_id, schema, table, chunks, sha256, seconds, error}; never raises LoadError."""
    t0 = time.monotonic()
    out = {"status": None, "release_id": None, "schema": None, "table": TABLE, "chunks": 0, "sha256": None, "seconds": 0.0, "error": None}
    try:
        try:
            rel = read_release(pack, release)
        except LoadError:
            raise
        except Exception as e:  # noqa: BLE001 - a folder of an unexpected shape: its type only, never a crash
            raise LoadError("index_invalid", f"release 폴더를 읽을 수 없습니다: {type(e).__name__}") from None
        out.update(release_id=rel["release_id"], schema=rel["schema"], chunks=len(rel["rows"]), sha256=rel["sha256"])
        if check:
            out["status"] = "checked"
        else:
            name = DSN_ENV if dsn_env is None else dsn_env
            dsn = (os.environ if env is None else env).get(name)
            if not dsn:
                raise LoadError("load_failed", f"DSN 환경 변수 {name}가 없습니다")
            try:
                conn = (connect or psycopg_connect)(dsn)
            except ImportError:
                raise LoadError("load_failed", "적재에는 psycopg가 필요합니다") from None
            except Exception as e:  # noqa: BLE001 - the message may quote the DSN: keep the type only
                raise LoadError("load_failed", f"DB에 연결할 수 없습니다: {type(e).__name__}") from None
            try:
                out["status"] = write(conn, rel)
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 - the load result is already decided
                    pass
    except LoadError as e:
        out.update(status="failed", error={"code": e.code, "message": str(e)})
    out["seconds"] = round(time.monotonic() - t0, 3)
    return out
