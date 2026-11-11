"""PostgreSQL release lookup for the execution config (pack resource contract 3): a release a PostgreSQL backend reads is a row of
`<pack_id>.data_release`, loaded by the release loader. Read with the runtime account only; the DSN comes from an environment
variable named in the config and never appears in a message or a record (errors keep the exception type only)."""
import hashlib
import json
import re

RUNTIME_ACCOUNT = "runtime_reader"
IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class PgError(ValueError):
    pass


def ident(name, what):
    if not (isinstance(name, str) and IDENT.match(name)):
        raise PgError(f"{what} 이름을 PostgreSQL 이름으로 쓸 수 없습니다: {name!r}")
    return name


def connect(dsn):
    """A read-only session (default_transaction_read_only): the runtime reads, it never writes, whatever the account may do."""
    try:
        import psycopg
    except ImportError:
        raise PgError("PostgreSQL backend에는 Pack 환경에 psycopg가 필요합니다") from None
    try:
        return psycopg.connect(dsn, connect_timeout=5, autocommit=True, options="-c default_transaction_read_only=on")
    except Exception as e:  # noqa: BLE001 - the message may quote the DSN: keep the type only
        raise PgError(f"DB에 연결할 수 없습니다: {type(e).__name__}") from None


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


ROW_KEYS = ("release_id", "kind", "sha256", "manifest", "embedding_provider", "embedding_model", "embedding_dim", "embedding_mode", "store")
_ROW_SQL = ('SELECT release_id, kind, sha256, manifest, embedding_provider, embedding_model, embedding_dim, embedding_mode, store '
            'FROM "{schema}".data_release WHERE release_id ')


def _read(dsn, schema, query):
    """Open a read-only session, refuse an account other than runtime_reader before reading anything, run query(conn, schema)."""
    schema = ident(schema, "schema(Pack id)")
    conn = connect(dsn)
    try:
        try:
            user = conn.execute("SELECT current_user").fetchone()[0]
        except Exception as e:  # noqa: BLE001 - type only
            raise PgError(f"DB 계정을 확인할 수 없습니다: {type(e).__name__}") from None
        if user != RUNTIME_ACCOUNT:
            raise PgError(f"실행용 DB 계정은 {RUNTIME_ACCOUNT}여야 합니다(지금: {user})")
        try:
            return query(conn, schema)
        except PgError:
            raise
        except Exception as e:  # noqa: BLE001 - a missing schema or table, no privilege: type only
            raise PgError(f"'{schema}.data_release'를 읽을 수 없습니다: {type(e).__name__}") from None
    finally:
        conn.close()


def _row(row):
    out = dict(zip(ROW_KEYS, row))
    if isinstance(out["manifest"], str):
        out["manifest"] = json.loads(out["manifest"])
    return out


def release_row(dsn, schema, release_id):
    """{release_id, kind, sha256, manifest, embedding_provider, embedding_model, embedding_dim, embedding_mode, store} or None. Refuses
    an account other than runtime_reader before reading anything."""
    row = _read(dsn, schema, lambda conn, s: conn.execute(_ROW_SQL.format(schema=s) + "= %s", (release_id,)).fetchone())
    return None if row is None else _row(row)


def release_rows(dsn, schema, release_ids):
    """EV-008 CHG-P01 2: the rows of several releases read at once (a retrieve@2 store's base and candidates) — {release_id: row};
    an id with no row is absent. Same account rule as release_row."""
    rows = _read(dsn, schema, lambda conn, s: conn.execute(_ROW_SQL.format(schema=s) + "= ANY(%s) ORDER BY release_id",
                                                           (list(release_ids),)).fetchall())
    return {r["release_id"]: r for r in map(_row, rows)}
