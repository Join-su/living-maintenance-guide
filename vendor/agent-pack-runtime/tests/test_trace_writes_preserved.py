"""PRES-F01 · F02 (EV-004): the tracewriter writes what the 0.1.2 code (301a120) wrote for a decide_min and an llm_min run — SQL
text, arguments, transaction edges, payload files — compared as JSON text (BYTE) with tests/fixtures/trace_writes/expected-301a120.json.
The fake connection and the list of values left out are the ones the run folder's evidence/scripts/characterize_writes.py used."""
import hashlib
import io
import json
import re
import shutil
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path

import packrt
from packrt.fake.jev import FakeJevProvider
from packrt.run import providers
from packrt.run.engine import Runtime
from tracewriter import RunSink, TraceWriter

FIX = Path(__file__).resolve().parent / "fixtures"
EXPECTED = json.loads((FIX / "trace_writes" / "expected-301a120.json").read_text(encoding="utf-8"))
KEY = "fake-openai-key-fb0-0123456789"
ENV = {"OPENAI_API_KEY": KEY, "OPENAI_BASE_URL": "http://127.0.0.1:9/v1"}
RUN_ID, TRACE_ID = "run_01JC0000000000000000000000", "0123456789abcdef0123456789abcdef"
CTX = {"run_id": RUN_ID, "trace_id": TRACE_ID, "origin": "deployment", "evaluation_run_id": None, "deployment_id": "fb0@test", "retention_class": "standard"}
HEX16 = re.compile(r"^[0-9a-f]{16}$")


class FakeCursor:
    def __init__(self, log):
        self.log, self._one = log, None

    def execute(self, sql, params=()):
        self.log.append({"sql": " ".join(sql.split()), "params": list(params)})
        self._one = (params[1],) if "INSERT INTO trace.event" in sql else None   # RETURNING seq: a new row

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


def norm_body(v, tmps):
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if k in ("ms", "pack_env"):
                continue
            if k == "runtime" and isinstance(x, dict):
                x = {kk: vv for kk, vv in x.items() if kk != "digest"}
            if k == "execution_config" and isinstance(x, dict):
                x = {kk: vv for kk, vv in x.items() if kk != "sha256"}
            out[k] = norm_body(x, tmps)
        return out
    if isinstance(v, list):
        return [norm_body(x, tmps) for x in v]
    if isinstance(v, str):
        for t in tmps:
            v = v.replace(t, "<TMP>")
        return "<HEX16>" if HEX16.match(v) else v
    return v


def norm_log(log, tmps):
    out = []
    for item in log:
        if "sql" not in item:
            out.append(item)
            continue
        params = []
        for i, p in enumerate(item["params"]):
            if isinstance(p, datetime):
                p = "<TS>"
            elif type(p).__name__ == "Jsonb":
                p = {"jsonb": norm_body(p.obj, tmps)}
            elif isinstance(p, str):
                p = norm_body(p, tmps)
            params.append(p)
        if item["sql"].startswith("INSERT INTO trace.event"):
            params[-1] = "<BODY_SHA>"
        if item["sql"].startswith("INSERT INTO trace.run"):
            params[12] = "<MANIFEST_SHA>"
        out.append({"sql": item["sql"], "params": params})
    return out


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    body = {"id": "chatcmpl-fb0", "object": "chat.completion", "model": "gpt-4.1-mini-2025-04-14", "system_fingerprint": "fp_fb0",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps({"summary": "결제 두 번", "category": "billing", "confidence": 0.9}, ensure_ascii=False), "refusal": None}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}
    return Resp(json.dumps(body).encode())


def write_run(name, entry, value, make_rt):
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "p"
        shutil.copytree(FIX / name, work, ignore=shutil.ignore_patterns("releases", "expected-*.json"))
        roots = Path(tmp) / "releases"
        roots.mkdir()
        (work / "execution-config.yaml").write_text(
            f"config_version: 1\nentrypoint: {entry}\n"
            f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{roots}"]\nstores: {{}}\nmodels: {{}}\n', encoding="utf-8")
        log = []
        writer = TraceWriter(payload_root=Path(tmp) / "payloads", secrets=[KEY], connect=lambda: FakeConn(log))
        rt = make_rt(work)
        res = rt.run(value, context=dict(CTX), sink=RunSink(writer, dict(CTX)), review={"policy": "auto_approve"})
        files = {p.relative_to(Path(tmp) / "payloads").as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted((Path(tmp) / "payloads").rglob("*")) if p.is_file()}
        tmps = sorted({tmp, str(Path(tmp).resolve())}, key=len, reverse=True)
        return {"status": res["status"], "outcome": res["outcome"], "log": norm_log(log, tmps), "payload_files": files}


def http_rt(work):
    rt = Runtime(work, env=ENV)
    rt.provider = providers.HttpProvider(rt.models, ENV)
    return rt


def b(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def unversioned(log):
    """The packrt version a run records (trace.run runtime_version, run_started body runtime_version and manifest runtime.version)
    changes with every release on purpose (EV-006 CHG-R10: 0.12.0); both sides put the same mark there before comparing."""
    out = json.loads(json.dumps(log, default=str))
    for item in out:
        if item.get("sql", "").startswith("INSERT INTO trace.run"):
            item["params"][10] = "<RUNTIME_VERSION>"
        for p in item.get("params", []):
            body = p.get("jsonb") if isinstance(p, dict) else None
            if isinstance(body, dict) and "runtime_version" in body:
                body["runtime_version"] = "<RUNTIME_VERSION>"
                if isinstance((body.get("manifest") or {}).get("runtime"), dict):
                    body["manifest"]["runtime"]["version"] = "<RUNTIME_VERSION>"
    return out


def test_event_writes_are_byte_identical(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    got = {"decide_min": write_run("decide_min", "triage", {"id": "T-1", "text": "결제가 두 번 됐어요", "channel": "web"}, lambda w: Runtime(w, provider=FakeJevProvider())),
           "llm_min": write_run("llm_min", "ask", {"id": "T-1", "text": "결제가 두 번 됐어요"}, http_rt)}
    for k, v in got.items():
        want = EXPECTED[k]
        assert v["status"] == want["status"] == "succeeded"
        assert b(unversioned(v["log"])) == b(unversioned(want["log"])), k   # PRES-F01: every SQL, argument and transaction edge
        run_row = next(x["params"] for x in v["log"] if x.get("sql", "").startswith("INSERT INTO trace.run"))
        assert run_row[10] == packrt.__version__, k                    # the mark hides the value, so check it is this packrt
        assert b(v["payload_files"]) == b(want["payload_files"]), k   # PRES-F02: payload names and bytes
    assert KEY not in b(got)
    assert "trace.feedback" not in b(got)                             # an event run never touches the new table
