"""EV-008 P0 (PRES-P01 · P07 · P10): the retrieve@1 pgvector path keeps what the 0.12.0 code (fc5abfa) gave — the store binding read
from `<pack_id>.data_release` with the runtime account, every SQL statement sent to PostgreSQL, the hits each node writes, every trace
write (node meta and the whole run manifest included) — and the probe's error shape for a Pack with no retrieve@2 pgvector store.
Compared as JSON text (BYTE) with tests/fixtures/retrieve_min/expected-pg-fc5abfa.json.
No database: `packrt.ir.pg.connect` is replaced by a fake connection over the retrieve_min index release (its chunks and vectors as
data_release · manual_chunk rows). The expected file was written by characterize() below on fc5abfa (EV-008 run folder
evidence/p0, git-ignored)."""
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from packrt.ir import pg
from packrt.run.engine import Runtime
from tracewriter import RunSink, TraceWriter

from test_retrieve1_preserved import CTX, EXCLUDED, INPUT, FakeConn, b, norm, norm_log

FIX = Path(__file__).resolve().parent / "fixtures" / "retrieve_min"
EXPECTED_FILE = FIX / "expected-pg-fc5abfa.json"
DSN = "postgresql:///llmopsagent?user=runtime_reader&password=SECRET-p0"   # never reaches a socket; must never be printed (PRES-P08)
REL = FIX / "releases" / "indexes" / "ir-docs-v1"


class FakePgCursor:
    def __init__(self, rows, cols):
        self._rows, self.description = rows, [type("Col", (), {"name": c})() for c in cols]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakePg:
    """A runtime_reader session over one schema: data_release rows and chunk tables, answering only the statements packrt sends."""

    def __init__(self, log, user="runtime_reader", releases=(), tables=None):
        self.log, self.user, self.closed = log, user, False
        self.releases = {r["release_id"]: r for r in releases}
        self.tables = tables or {}

    def execute(self, sql, args=()):
        text = " ".join(sql.split())
        self.log.append({"pg": text, "args": list(args)})
        if text == "SELECT current_user":
            return FakePgCursor([(self.user,)], ["current_user"])
        m = re.match(r'^SELECT release_id, kind, sha256, manifest, embedding_provider, embedding_model, embedding_dim, embedding_mode, store '
                     r'FROM "(\w+)"\.data_release WHERE release_id = %s$', text)
        if m:
            r = self.releases.get(args[0])
            keys = ("release_id", "kind", "sha256", "manifest", "embedding_provider", "embedding_model", "embedding_dim", "embedding_mode", "store")
            return FakePgCursor([tuple(r[k] for k in keys)] if r else [], list(keys))
        m = re.match(r'^SELECT chunk_id, text, metadata, embedding::text AS embedding FROM "(\w+)"\."(\w+)" WHERE release_id = %s ORDER BY chunk_id$', text)
        if m:
            rows = sorted((x for x in self.tables.get(m.group(2), []) if x["release_id"] == args[0]), key=lambda x: x["chunk_id"])
            return FakePgCursor([(x["chunk_id"], x["text"], x["metadata"], x["embedding"]) for x in rows], ["chunk_id", "text", "metadata", "embedding"])
        raise AssertionError(f"모르는 SQL: {text}")

    def close(self):
        self.log.append({"pg": "close"})
        self.closed = True


def docs_rows():
    """retrieve_min ir-docs-v1 as the loader would have put it: one data_release row, one manual_chunk row per chunk (vector as text)."""
    manifest = json.loads((REL / "manifest.json").read_text(encoding="utf-8"))
    chunks = [json.loads(l) for l in (REL / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    vectors = json.loads((REL / "vectors.json").read_text(encoding="utf-8"))
    e = manifest["embedding"]
    release = {"release_id": "ir-docs-v1", "kind": "index", "sha256": manifest["index_sha256"], "manifest": manifest,
               "embedding_provider": e["provider"], "embedding_model": e["model"], "embedding_dim": e["dim"], "embedding_mode": e["mode"],
               "store": "docs"}
    chunk_rows = [{"release_id": "ir-docs-v1", "chunk_id": c["id"], "text": c["text"], "metadata": c["metadata"],
                   "embedding": json.dumps(vectors[c["id"]])} for c in chunks]
    return release, chunk_rows


def pack_copy(tmp, docs_line):
    work = Path(tmp) / "p"
    shutil.copytree(FIX, work, ignore=shutil.ignore_patterns("releases", "expected-*.json"))
    roots = Path(tmp) / "releases"
    shutil.copytree(FIX / "releases", roots)
    (work / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: probe\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{roots}"]\n'
        f"stores:\n  docs: {docs_line}\n  rows: {{backend: sqlite-file, release: tr-rows-v1}}\nmodels: {{}}\n", encoding="utf-8")
    return work


PG_DOCS = "{backend: pgvector, release: ir-docs-v1, connection: {env: LLMOPSAGENT_RUNTIME_DSN}, embedding: {mode: fake}}"


def characterize(mp):
    release, chunk_rows = docs_rows()
    pglog = []
    mp.setattr(pg, "connect", lambda dsn: (pglog.append({"pg": "connect", "dsn_is_env_value": dsn == DSN}),
                                           FakePg(pglog, releases=[release], tables={"manual_chunk": chunk_rows}))[1])
    mp.setenv("LLMOPSAGENT_RUNTIME_DSN", DSN)   # PgVector reads its DSN from the process environment
    with tempfile.TemporaryDirectory() as tmp:
        work = pack_copy(tmp, PG_DOCS)
        log = []
        payloads = Path(tmp) / "payloads"
        writer = TraceWriter(payload_root=payloads, secrets=[], connect=lambda: FakeConn(log))
        rt = Runtime(work, env={"LLMOPSAGENT_RUNTIME_DSN": DSN})
        binding = json.loads(json.dumps(rt.bindings["stores"]["docs"], default=str))
        res = rt.run(dict(INPUT), context=dict(CTX), sink=RunSink(writer, dict(CTX)), review={"policy": "auto_approve"})
        closed = rt.close()
        subs = [(t, "<TMP>") for t in sorted({tmp, str(Path(tmp).resolve())}, key=len, reverse=True)] + [(sys.executable, "<PY>")]
        files = {}
        for p in sorted(payloads.rglob("*")):
            if p.is_file():
                raw = p.read_bytes()
                try:
                    body = norm(json.loads(raw), subs)
                except ValueError:
                    body = norm(raw.decode("utf-8", "replace"), subs)
                files[p.relative_to(payloads).as_posix()] = body
        return {"binding": norm(binding, subs), "pg_log": pglog, "closed": closed, "status": res["status"],
                "outcome": norm(res.get("outcome"), subs), "log": norm_log(log, subs), "payload_files": files}


def probe_error(mp):
    """The probe's error shape (PRES-P07): a Pack with a pgvector retrieve@1 store whose graph cannot be built (the model release the
    config names is missing) — no retrieve@2 store, so 0.12.1 must print exactly the 0.11.0 shape."""
    from packrt import probe
    mp.delenv("LLMOPSAGENT_RUNTIME_DSN", raising=False)   # the probe gets no DSN (EV-020 CHG-97)
    with tempfile.TemporaryDirectory() as tmp:
        work = pack_copy(tmp, PG_DOCS)
        cfg = work / "execution-config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("models: {}", "models: {ghost: {release: no-such-model}}"), encoding="utf-8")
        out = probe.main(str(work))
        return json.loads(json.dumps(norm(out, [(tmp, "<TMP>"), (str(Path(tmp).resolve()), "<TMP>")]), ensure_ascii=False))


@pytest.fixture(scope="module")
def want():
    return json.loads(EXPECTED_FILE.read_text(encoding="utf-8"))


def test_pg_binding_and_sql_are_byte_identical(monkeypatch, want):
    got = characterize(monkeypatch)
    assert got["status"] == want["run"]["status"] == "succeeded"
    assert b(got["binding"]) == b(want["run"]["binding"])      # _pg_store: table · manifest_sha256 (canonical) · E-3 fields
    assert b(got["pg_log"]) == b(want["run"]["pg_log"])        # account check, one data_release read, one chunk read, closes
    assert got["closed"] == want["run"]["closed"]


def test_pg_run_is_byte_identical(monkeypatch, want):
    got = characterize(monkeypatch)
    assert b(got["payload_files"]) == b(want["run"]["payload_files"])   # the hits each node wrote
    assert b(got["log"]) == b(want["run"]["log"])                       # node meta · drs · the whole run manifest (PRES-P10)
    assert b(got["outcome"]) == b(want["run"]["outcome"])
    assert want["excluded"] == EXCLUDED


def test_probe_error_shape_without_retrieve2_pg(monkeypatch, want):
    got = probe_error(monkeypatch)
    assert b(got) == b(want["probe_error"])
    assert set(got) == {"error", "message"}   # no index_missing · index_unchecked key on this Pack (D-308)


def test_dsn_never_printed(monkeypatch, want):
    assert "SECRET-p0" not in json.dumps(want, ensure_ascii=False)
    got = characterize(monkeypatch)
    assert "SECRET-p0" not in json.dumps(got, ensure_ascii=False, default=str)


def test_expected_covers_the_pg_path(want):
    """The expected file really went through PostgreSQL: account check before the release read, the manual_chunk table, and the
    same hits as the flat-local expected file (the vectors are the same numbers)."""
    sqls = [e["pg"] for e in want["run"]["pg_log"]]
    assert sqls.index("SELECT current_user") < next(i for i, s in enumerate(sqls) if "data_release" in s)
    assert any('"retrieve_min"."manual_chunk"' in s for s in sqls)
    assert want["run"]["binding"]["table"] == "manual_chunk" and want["run"]["binding"]["backend"] == "pgvector"
    flat = json.loads((FIX / "expected-e6f5a26.json").read_text(encoding="utf-8"))["run"]["log"]
    assert retrievals(want["run"]["log"]) == retrievals(flat)


def retrievals(log):
    """(store, release, results) of every retrieve@1 node meta in a trace write log, in write order."""
    return [(p["jsonb"]["meta"]["store"], p["jsonb"]["meta"]["release_id"], p["jsonb"]["meta"]["results"])
            for e in log if "sql" in e and e["sql"].startswith("INSERT INTO trace.event")
            for p in e["params"] if isinstance(p, dict) and (p.get("jsonb") or {}).get("meta", {}).get("kind") == "retrieve@1"]


if __name__ == "__main__":   # write the expected file (run on fc5abfa only): python tests/test_pg_preserved.py <out.json>
    mp = pytest.MonkeyPatch()
    try:
        run = characterize(mp)
        mp.undo()
        err = probe_error(mp)
    finally:
        mp.undo()
    Path(sys.argv[1]).write_text(json.dumps({"excluded": EXCLUDED, "run": run, "probe_error": err}, ensure_ascii=False, indent=1,
                                            sort_keys=True, default=str) + "\n", encoding="utf-8")
