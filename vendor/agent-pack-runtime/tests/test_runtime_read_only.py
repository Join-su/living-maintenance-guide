"""EV-010 CHG-Q01 (D-331): the runtime's PostgreSQL session is read-only. Every read path — release_row and release_rows when the
execution config is read, _PgReader.rows while a node runs — opens its connection through pg.connect with
default_transaction_read_only=on and autocommit (EV-008 PRES-P03). A fake psycopg records the connect arguments; nothing is opened.
EV-010 CHG-Q02: the runtime source never imports the release loader (packrt.index.load writes to the database) — read as source,
so a late import inside a function counts too."""
import ast
import sys
import types
from pathlib import Path

import pytest

from packrt.ir import pg
from packrt.run import backends

DSN = "postgresql:///llmopsagent?user=runtime_reader&password=SECRET-q01"
READ_ONLY = "-c default_transaction_read_only=on"


class _Cur:
    description = ()

    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _Conn:
    closed = False

    def execute(self, sql, args=None):
        return _Cur([("runtime_reader",)] if sql == "SELECT current_user" else [])

    def close(self):
        self.closed = True


@pytest.fixture
def calls(monkeypatch):
    """The fake psycopg: each connect call is kept as (args, kwargs)."""
    seen = []
    fake = types.ModuleType("psycopg")

    def connect(*args, **kwargs):
        seen.append((args, kwargs))
        return _Conn()

    fake.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    return seen


def read_only(call):
    args, kwargs = call
    return args == (DSN,) and kwargs.get("autocommit") is True and READ_ONLY in (kwargs.get("options") or "")


def test_connect_opens_a_read_only_autocommit_session(calls):
    pg.connect(DSN)
    assert len(calls) == 1 and read_only(calls[0])


def test_release_row_reads_through_a_read_only_session(calls):
    assert pg.release_row(DSN, "task10", "ir-none") is None
    assert len(calls) == 1 and read_only(calls[0])


def test_release_rows_reads_through_a_read_only_session(calls):
    assert pg.release_rows(DSN, "sel", ["ir-a", "ir-b"]) == {}
    assert len(calls) == 1 and read_only(calls[0])


def test_the_node_reader_reads_through_one_read_only_session(calls):
    reader = backends._PgReader(DSN)
    assert reader.rows("SELECT 1", ()) == [] and reader.rows("SELECT 1", ()) == []
    reader.close()
    assert len(calls) == 1 and read_only(calls[0])   # opened on first use, then reused


# ---------------------------------------------------------------- CHG-Q02
PKG = Path(pg.__file__).resolve().parents[1]                     # packrt/packrt
LOADER = "packrt.index.load"


def runtime_sources():
    return sorted([*(PKG / "run").rglob("*.py"), *(PKG / "ir").rglob("*.py"), PKG / "probe.py", PKG / "runner.py"])


def loader_uses(source, module):
    """Every way a module's source could reach packrt.index.load: import / from-import (absolute or relative, at any depth, so a late
    import inside a function counts) and the module name as a string (importlib)."""
    package = module.split(".")[:-1]
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name == LOADER or a.name.startswith(LOADER + ".")]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join(package[: len(package) - node.level + 1] + ([base] if base else []))
            found += [n for n in [base, *(f"{base}.{a.name}" for a in node.names)] if n == LOADER or n.startswith(LOADER + ".")]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and (node.value == LOADER or node.value.startswith(LOADER + ".")):
            found.append(node.value)
    return found


def module_of(path):
    return ".".join(path.relative_to(PKG.parent).with_suffix("").parts)


def test_the_runtime_source_never_imports_the_loader():
    files = runtime_sources()
    assert PKG / "run" / "engine.py" in files and PKG / "ir" / "releases.py" in files and PKG / "runner.py" in files
    uses = {module_of(f): loader_uses(f.read_text(encoding="utf-8"), module_of(f)) for f in files}
    assert {m: u for m, u in uses.items() if u} == {}


@pytest.mark.parametrize("source", [
    "import packrt.index.load", "from packrt.index import load", "from packrt.index.load import main", "from ..index import load",
    "from ..index.load import main", "def f():\n    import packrt.index.load", "importlib.import_module('packrt.index.load')"])
def test_the_source_check_sees_every_form(source):
    """The check itself, on text placed as if it were packrt/ir/x.py."""
    assert loader_uses(source + "\n", "packrt.ir.x")


def test_the_source_check_finds_the_one_real_caller():
    main = PKG / "index" / "__main__.py"
    assert loader_uses(main.read_text(encoding="utf-8"), module_of(main))
