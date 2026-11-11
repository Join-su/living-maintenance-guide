"""EV-007 P-03 · P-04 · P-05: retrieve@1 keeps what the 0.11.0 code (e6f5a26) gave for the retrieve_min Pack — the hits each node
writes (id · score · order · text · source_type · store · index_release · fallback_used), the node_ended meta of each retrieve (fallback_reason
included), every trace write (SQL · arguments · transaction edges, trace.retrieval rows included) and the whole run manifest — compared as
JSON text (BYTE) with tests/fixtures/retrieve_min/expected-e6f5a26.json.
Cases C1 ~ C6 run as one graph: plain · one-value filter · list filter · filter then relax · mmr with a threshold · table where then relax.
Fake embedding only (64 numbers, no provider call). Values that change with the runtime version or the machine are taken out first —
the list is the expected file's `excluded`. The expected file was written by the run folder's evidence/s0/characterize_retrieve.py
(EV-007, git-ignored), which calls characterize() below."""
import hashlib
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from packrt.run.engine import Runtime
from tracewriter import RunSink, TraceWriter

FIX = Path(__file__).resolve().parent / "fixtures" / "retrieve_min"
EXPECTED_FILE = FIX / "expected-e6f5a26.json"
RUN_ID, TRACE_ID = "run_01JC0000000000000000000007", "0123456789abcdef0123456789abcd07"
CTX = {"run_id": RUN_ID, "trace_id": TRACE_ID, "origin": "evaluation", "evaluation_run_id": "ev007", "deployment_id": None, "retention_class": "dev"}
INPUT = {"text": "공구 마모 점검 전에 전원을 내린다", "group": "TW", "groups": ["TW", "PW"], "rare": "XX"}
HEX16 = re.compile(r"^[0-9a-f]{16}$")
EXCLUDED = ["timestamps -> <TS>", "span ids (16 hex) -> <HEX16>", "temp folder and python path -> <TMP> · <PY>", "ms", "pack_env",
            "runtime.digest", "runtime.version", "runtime_version", "execution_config.sha256 (the config names the temp folder)",
            "trace.event body_sha256 -> <BODY_SHA>", "trace.run manifest_sha256 -> <MANIFEST_SHA>"]


class FakeCursor:
    def __init__(self, log):
        self.log, self._one = log, None

    def execute(self, sql, params=()):
        self.log.append({"sql": " ".join(sql.split()), "params": list(params)})
        self._one = (params[1],) if "INSERT INTO trace.event" in sql else None

    def fetchone(self):
        return self._one


class FakeTx:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        self.log.append({"tx": "begin"})
        return self

    def __exit__(self, *exc):
        self.log.append({"tx": "commit" if exc[0] is None else "rollback"})
        return False


class FakeConn:
    def __init__(self, log):
        self.log, self.closed = log, False

    def transaction(self):
        return FakeTx(self.log)

    def cursor(self):
        return FakeCursor(self.log)

    def execute(self, sql, params=()):
        self.log.append({"sql": " ".join(sql.split()), "params": list(params)})

    def rollback(self):
        self.log.append({"tx": "rollback"})

    def close(self):
        self.closed = True


def norm(v, subs):
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if k in ("ms", "pack_env", "runtime_version"):
                continue
            if k == "runtime" and isinstance(x, dict):
                x = {kk: vv for kk, vv in x.items() if kk not in ("digest", "version")}
            if k == "execution_config" and isinstance(x, dict):
                x = {kk: vv for kk, vv in x.items() if kk != "sha256"}
            out[k] = norm(x, subs)
        return out
    if isinstance(v, list):
        return [norm(x, subs) for x in v]
    if isinstance(v, str):
        for old, new in subs:
            v = v.replace(old, new)
        return "<HEX16>" if HEX16.match(v) else v
    return v


def norm_log(log, subs):
    out = []
    for item in log:
        if "sql" not in item:
            out.append(item)
            continue
        params = []
        for p in item["params"]:
            if isinstance(p, datetime):
                p = "<TS>"
            elif type(p).__name__ == "Jsonb":
                p = {"jsonb": norm(p.obj, subs)}
            elif isinstance(p, str):
                p = norm(p, subs)
            params.append(p)
        if item["sql"].startswith("INSERT INTO trace.event"):
            params[-1] = "<BODY_SHA>"
        if item["sql"].startswith("INSERT INTO trace.run"):
            params[12] = "<MANIFEST_SHA>"
            params[10] = "<RUNTIME_VERSION>"
        out.append({"sql": item["sql"], "params": params})
    return out


def characterize():
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "p"
        shutil.copytree(FIX, work, ignore=shutil.ignore_patterns("releases", "expected-*.json"))
        roots = Path(tmp) / "releases"
        shutil.copytree(FIX / "releases", roots)
        (work / "execution-config.yaml").write_text(
            "config_version: 1\nentrypoint: probe\n"
            f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{roots}"]\n'
            "stores:\n  docs: {backend: flat-local, release: ir-docs-v1, embedding: {mode: fake}}\n"
            "  rows: {backend: sqlite-file, release: tr-rows-v1}\nmodels: {}\n", encoding="utf-8")
        log = []
        payloads = Path(tmp) / "payloads"
        writer = TraceWriter(payload_root=payloads, secrets=[], connect=lambda: FakeConn(log))
        rt = Runtime(work, env={})
        res = rt.run(dict(INPUT), context=dict(CTX), sink=RunSink(writer, dict(CTX)), review={"policy": "auto_approve"})
        subs = [(t, "<TMP>") for t in sorted({tmp, str(Path(tmp).resolve())}, key=len, reverse=True)] + [(sys.executable, "<PY>")]
        files = {}
        for p in sorted(payloads.rglob("*")):
            if p.is_file():
                raw = p.read_bytes()
                try:
                    body = norm(json.loads(raw), subs)
                except ValueError:
                    body = norm(raw.decode("utf-8", "replace"), subs)
                files[p.relative_to(payloads).as_posix()] = {"sha256": hashlib.sha256(raw).hexdigest(), "body": body}
        return {"status": res["status"], "outcome": norm(res.get("outcome"), subs), "log": norm_log(log, subs), "payload_files": files}


def b(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def test_retrieve1_run_is_byte_identical():
    want = json.loads(EXPECTED_FILE.read_text(encoding="utf-8"))
    got = characterize()
    assert got["status"] == want["run"]["status"] == "succeeded"
    assert b(got["payload_files"]) == b(want["run"]["payload_files"])   # P-03: the hits each node wrote (payload bodies) and their bytes
    assert b(got["log"]) == b(want["run"]["log"])                       # P-04 · P-05: trace writes, node meta and the whole run manifest
    assert b(got["outcome"]) == b(want["run"]["outcome"])
    assert want["excluded"] == EXCLUDED


def test_expected_covers_every_case():
    """The six cases really happened in the expected file: list filter, relax with its reason, mmr, a table store and a score tie."""
    want = json.loads(EXPECTED_FILE.read_text(encoding="utf-8"))["run"]
    metas = [p["jsonb"]["meta"] for e in want["log"] if "sql" in e and e["sql"].startswith("INSERT INTO trace.event")
             for p in e["params"] if isinstance(p, dict) and (p.get("jsonb") or {}).get("meta", {}).get("kind") == "retrieve@1"]
    by_store = {}
    for r in metas:
        by_store.setdefault(r["store"], []).append(r)
    assert len(by_store.get("docs", [])) == 5 and len(by_store.get("rows", [])) == 1
    assert sum(r["fallback_used"] for rs in by_store.values() for r in rs) == 2
    assert all(r["fallback_reason"] for rs in by_store.values() for r in rs if r["fallback_used"])
