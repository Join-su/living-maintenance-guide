"""Resource objects a plain Pack function receives by name (spec v0.5 supplement 3, 11 `custom_code@3`).

A table resource reads the table release the execution config binds to the store: `rows()` and `metadata`, nothing else.
It only reads (SQLite is opened read-only; writes are refused by the sandbox as before) and holds no key value."""
import copy
import json
import sqlite3
from pathlib import Path

class ResourceError(RuntimeError):
    pass


def _ident(name, what):
    if not (isinstance(name, str) and name.replace("_", "").isalnum()):
        raise ResourceError(f"{what} 이름을 쓸 수 없습니다: {name!r}")
    return name


class TableResource:
    """rows(): every row as a flat dict, ordered by the store `key`; metadata: a copy of the release manifest.
    The table name is the manifest `table`, else the store name (retrieve@1 keeps its own rule)."""

    def __init__(self, store_name, store_doc, release_folder):
        self._name = store_name
        self._folder = Path(release_folder)
        self._manifest = json.loads((self._folder / "manifest.json").read_text(encoding="utf-8"))
        self._table = _ident(self._manifest.get("table") or store_name, "표")
        self._key = _ident(store_doc.get("key"), "key")
        real, root = (self._folder / str(self._manifest.get("file", ""))).resolve(), self._folder.resolve()
        if root not in real.parents:
            raise ResourceError(f"저장소 '{store_name}' release의 표 파일이 release 폴더 밖을 가리킵니다: {self._manifest.get('file')}")
        self._file = real

    @property
    def metadata(self):
        return copy.deepcopy(self._manifest)

    def rows(self):
        db = sqlite3.connect(f"file:{self._file}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            cols = [r[1] for r in db.execute(f'PRAGMA table_info("{self._table}")')]
            if not cols:
                raise ResourceError(f"저장소 '{self._name}'의 표 '{self._table}'가 없습니다")
            if self._key not in cols:
                raise ResourceError(f"저장소 '{self._name}'의 표 '{self._table}'에 key 열 '{self._key}'가 없습니다")
            return [dict(r) for r in db.execute(f'SELECT * FROM "{self._table}" ORDER BY "{self._key}"')]
        finally:
            db.close()


class PgTableResource:
    """The same resource over a PostgreSQL table release (backend `postgres-table`): rows() reads `<schema>.<table>` for the
    bound release_id, without the release_id column, ordered by the store key; metadata is the release manifest from
    `data_release`. Read with the runtime account only."""

    def __init__(self, store_name, store_doc, binding, table=None):
        from .backends import BackendError, PostgresTable
        self._name = store_name
        self._key = _ident(store_doc.get("key"), "key")
        self._manifest = binding.get("manifest") or {}
        try:   # the runtime passes its own store object, so retrieve and custom_code share one connection per store
            self._table = table if table is not None else PostgresTable(**binding, key=self._key)
        except BackendError as e:
            raise ResourceError(str(e)) from None

    @property
    def metadata(self):
        return copy.deepcopy(self._manifest)

    def rows(self):
        from .backends import BackendError
        t = self._table
        try:
            rows = t.reader.rows(f'SELECT * FROM "{t.schema}"."{t.table}" WHERE release_id = %s ORDER BY "{self._key}"', (t.release_id,))
        except BackendError as e:
            raise ResourceError(f"저장소 '{self._name}'를 읽을 수 없습니다: {e}") from None
        if rows and self._key not in rows[0]:
            raise ResourceError(f"저장소 '{self._name}'의 표 '{t.table}'에 key 열 '{self._key}'가 없습니다")
        return [{k: v for k, v in r.items() if k != "release_id"} for r in rows]
