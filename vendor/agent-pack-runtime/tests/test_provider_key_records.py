"""V-HTTP-CHAT · V-KEY-CTRL at the engine (EV-001 CHG-107 · CHG-108, PRES-106): when a decide node or a chat (llm@1) node fails on
a provider error — an HTTP error body holding the key, or a key holding a control character — nothing the run records carries
the key. urlopen is replaced — no socket is opened."""
import io
import json
import shutil
import sys
import urllib.error
from pathlib import Path

import pytest

import packrt
from packrt.run import providers
from packrt.run.engine import Runtime

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "decide_min"
TICKET = {"id": "T-1", "text": "결제가 두 번 됐어요", "channel": "web"}
KEY = "fake-key-abcdefghij0123456789"
CHAT_PACK = """ir_version: 1
runtime_contract: {min_runtime_version: %s}
pack: {id: chat_min, version: V0, title: "chat 시험"}
env: {required: [OPENAI_API_KEY], optional: []}
schemas:
  Ticket: schemas/ticket.json
  Note: schemas/note.json
graphs:
  reply:
    state:
      ticket: Ticket
      note: Note
    nodes:
      - {id: draft, kind: llm@1, params: {provider: openai, model: gpt-4.1-mini, prompt: prompts/draft.md, output: Note}, reads: [ticket], writes: [note]}
    edges: [[START, draft], [draft, END]]
entrypoints:
  - {graph: reply, input: Ticket, output: Note}
trace: {contract: 1}
"""


class Recorder:
    def __init__(self):
        self.events, self.payloads = [], []

    def put_payload(self, value, *, role, retention_class):
        self.payloads.append({"role": role, "value": value})
        return {"ref": "sha256:" + f"{len(self.payloads):064x}", "size": 0, "media_type": "application/json"}

    def emit(self, event):
        self.events.append(event)


def pack(tmp_path, kind):
    work = tmp_path / "p"
    shutil.copytree(FIXTURE, work)
    if kind == "decide":
        text = (work / "pack.yaml").read_text(encoding="utf-8").replace("min_runtime_version: 0.10.0", f"min_runtime_version: {packrt.__version__}")
        entry = "triage"
    else:
        text = CHAT_PACK % packrt.__version__
        (work / "models.yaml").write_text("providers:\n  openai: {env: OPENAI_API_KEY, base_url_env: OPENAI_BASE_URL, models: {chat: [gpt-4.1-mini]}}\n", encoding="utf-8")
        (work / "prompts").mkdir()
        (work / "prompts" / "draft.md").write_text("답장을 쓰세요: {state.ticket.text}\n", encoding="utf-8")
        entry = "reply"
    (work / "pack.yaml").write_text(text, encoding="utf-8")
    roots = tmp_path / "releases"
    roots.mkdir()
    (work / "execution-config.yaml").write_text(
        f"config_version: 1\nentrypoint: {entry}\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{roots}"]\nstores: {{}}\nmodels: {{}}\n', encoding="utf-8")
    return work


ENV = {"decide": {"JEV_BASE_URL": "http://127.0.0.1:9"}, "chat": {"OPENAI_BASE_URL": "http://127.0.0.1:9/v1"}}
NAME = {"decide": "JEV_API_KEY", "chat": "OPENAI_API_KEY"}


@pytest.mark.parametrize("kind", ["decide", "chat"])
@pytest.mark.parametrize("case", ["error_body", "control_char"])
def test_a_failed_provider_call_leaves_no_key_in_the_records(tmp_path, monkeypatch, kind, case):
    key = KEY if case == "error_body" else KEY + "\ntail"
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url, 401, "err", {}, io.BytesIO(("x" * 286 + key + " bad key").encode()))
    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    rt = Runtime(pack(tmp_path, kind))
    rt.provider = providers.HttpProvider(rt.models, dict(ENV[kind], **{NAME[kind]: key}))
    rec = Recorder()
    res = rt.run(TICKET, sink=rec, review={"policy": "auto_approve"})
    assert res["status"] == "failed", res
    recorded = json.dumps([rec.events, rec.payloads, res], ensure_ascii=False, default=str)
    assert key not in recorded and KEY[:8] not in recorded
    if case == "error_body":
        assert len(calls) == 1 and "[가림]" in str(res["error"])
    else:
        assert calls == [] and NAME[kind] in str(res["error"])
