"""packrt 0.14.0(D-475 · 보고 149 Q-1 (가) · Q-2 (나) · Q-3 (가)): llm node `reasoning_effort` — models.yaml provider
`models.reasoning`(그 칸을 받는 chat 모델)에 든 모델에만 보냄, 그 모델은 max_tokens를 max_completion_tokens로 받음, temperature는
Pack 말대로 보냄(검사 경고만). 칸을 안 쓰는 Pack은 몸 · 기록 · revision이 0.13.2와 같음. urlopen을 바꿔 끼움 — 소켓 0."""
import io
import json
import shutil
import sys
import urllib.error
from pathlib import Path

import pytest

from packrt.ir import load_pack
from packrt.run import providers
from packrt.run.engine import Runtime

FIX = Path(__file__).resolve().parent / "fixtures" / "llm_min"
KEY = "fake-openai-key-r0140-0123456789"
ENV = {"OPENAI_API_KEY": KEY, "OPENAI_BASE_URL": "http://127.0.0.1:9/v1"}
NODE = "model: gpt-4.1-mini, prompt: prompts/draft.md, output: Answer, temperature: 0}"
TICKET = {"id": "T-1", "text": "결제가 두 번 됐어요"}
OK = json.dumps({"summary": "결제 두 번", "category": "billing", "confidence": 0.9}, ensure_ascii=False)
OFF = json.loads((FIX.parent / "llm_min_off.json").read_text(encoding="utf-8"))
LUNA = "gpt-6-luna"
GRAPH = "ask"


def models_yaml(reasoning=None, chat=("gpt-4.1-mini", "gpt-4.1-mini-2025-04-14", LUNA)):
    extra = "" if reasoning is None else f", reasoning: [{', '.join(reasoning)}]"
    return ("providers:\n  openai: {env: OPENAI_API_KEY, base_url_env: OPENAI_BASE_URL, "
            f"models: {{chat: [{', '.join(chat)}]{extra}}}}}\n")


def folder(tmp_path, node=None, reasoning=None, chat=None):
    work = tmp_path / "p"
    shutil.copytree(FIX, work)
    text = (work / "pack.yaml").read_text(encoding="utf-8")
    assert text.count(NODE) == 1
    if node is not None:
        text = text.replace(NODE, node)
    (work / "pack.yaml").write_text(text, encoding="utf-8")
    if reasoning is not None or chat is not None:
        (work / "models.yaml").write_text(models_yaml(reasoning, *(() if chat is None else (chat,))), encoding="utf-8")
    return work


def node(model=LUNA, extra="reasoning_effort: none", temperature=True):
    parts = [f"model: {model}", "prompt: prompts/draft.md", "output: Answer"] + (["temperature: 0"] if temperature else [])
    return ", ".join(parts + ([extra] if extra else [])) + "}"


def msgs(res, kind):
    return [p["message"] for p in getattr(res, kind)]


class Recorder:
    def __init__(self):
        self.events, self.payloads = [], []

    def put_payload(self, value, *, role, retention_class):
        self.payloads.append(role)
        return {"ref": "sha256:" + f"{len(self.payloads):064x}", "size": 0, "media_type": "application/json"}

    def emit(self, event):
        self.events.append(event)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def reply(content=OK):
    return {"id": "chatcmpl-r", "object": "chat.completion", "model": LUNA,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}


@pytest.fixture
def http(monkeypatch):
    seen, answers = [], []

    def fake_urlopen(req, timeout=None):
        seen.append(req.data.decode("utf-8"))
        a = answers.pop(0) if answers else reply()
        if isinstance(a, Exception):
            raise a
        return Resp(json.dumps(a).encode())
    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    return seen, answers


def run(work, provider=None):
    roots = work.parent / "releases"
    roots.mkdir(exist_ok=True)
    (work / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: ask\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{roots}"]\nstores: {{}}\nmodels: {{}}\n', encoding="utf-8")
    rt = Runtime(work, env=ENV)
    rt.provider = provider or providers.HttpProvider(rt.models, ENV)
    rec = Recorder()
    res = rt.run(TICKET, sink=rec, review={"policy": "auto_approve"})
    meta = [e for e in rec.events if e["type"] == "node_ended" and e["node_id"] == "draft"]
    return res, (meta[0]["body"]["meta"] if meta else None)


# ------------------------------------------------------------------ R1 칸
@pytest.mark.parametrize("value", ["none", "minimal", "low", "medium", "high", "xhigh", "max"])
def test_r1_the_values_are_taken(tmp_path, value):
    res = load_pack(folder(tmp_path, node(extra=f"reasoning_effort: {value}", temperature=False), reasoning=[LUNA]))
    assert res.ok and msgs(res, "errors") == [] and msgs(res, "warnings") == []


def test_r1_an_unknown_value_is_an_error(tmp_path):
    res = load_pack(folder(tmp_path, node(extra="reasoning_effort: fast", temperature=False), reasoning=[LUNA]))
    assert not res.ok and any("reasoning_effort" in str(p.get("path")) or "fast" in p["message"] for p in res.errors)


# ------------------------------------------------------------------ R2 models.yaml
def test_r2_a_reasoning_model_outside_chat_is_an_error(tmp_path):
    res = load_pack(folder(tmp_path, reasoning=["gpt-x"]))
    assert not res.ok and any("reasoning 목록에 chat 목록 밖 모델" in m and "gpt-x" in m for m in msgs(res, "errors"))


def test_r2_reasoning_not_a_list_is_an_error(tmp_path):
    work = folder(tmp_path)
    (work / "models.yaml").write_text("providers:\n  openai: {env: OPENAI_API_KEY, base_url_env: OPENAI_BASE_URL, "
                                      "models: {chat: [gpt-4.1-mini], reasoning: gpt-4.1-mini}}\n", encoding="utf-8")
    res = load_pack(work)
    assert not res.ok and any("reasoning은 모델 이름 목록" in m for m in msgs(res, "errors"))


def test_r2_a_node_model_outside_the_list_is_a_warning(tmp_path):
    res = load_pack(folder(tmp_path, node(model="gpt-4.1-mini", temperature=False), reasoning=[LUNA]))
    assert res.ok and any("reasoning_effort는 보내지 않습니다" in m and "gpt-4.1-mini" in m for m in msgs(res, "warnings"))


# ------------------------------------------------------------------ R3 요청 몸
def test_r3_a_listed_model_gets_the_value(tmp_path, http):
    res, _ = run(folder(tmp_path, node(temperature=False), reasoning=[LUNA]))
    body = json.loads(http[0][0])
    assert res["status"] == "succeeded" and body["model"] == LUNA and body["reasoning_effort"] == "none"


def test_r3_a_model_outside_the_list_gets_no_key(tmp_path, http):
    res, _ = run(folder(tmp_path, node(temperature=False), reasoning=[]))
    assert res["status"] == "succeeded" and "reasoning_effort" not in json.loads(http[0][0])


@pytest.mark.parametrize("reasoning", [None, ["gpt-4.1-mini"]])
def test_r3_no_field_is_the_old_body_byte_for_byte(tmp_path, http, reasoning):
    res, meta = run(folder(tmp_path, reasoning=reasoning))
    assert res["status"] == "succeeded" and http[0] == [OFF["body"]] and meta == OFF["meta"]


# ------------------------------------------------------------------ R4 temperature
def test_r4_temperature_and_effort_both_go_with_a_warning(tmp_path, http):
    work = folder(tmp_path, node(extra="reasoning_effort: low"), reasoning=[LUNA])
    res = load_pack(work)
    assert res.ok and any("reasoning_effort low와 temperature를 함께" in m for m in msgs(res, "warnings"))
    run(work)
    body = json.loads(http[0][0])
    assert body["temperature"] == 0 and body["reasoning_effort"] == "low"


def test_r4_effort_none_with_temperature_has_no_warning(tmp_path):
    res = load_pack(folder(tmp_path, node(), reasoning=[LUNA]))
    assert res.ok and msgs(res, "warnings") == []


def test_r4_a_refusal_keeps_the_status_and_hides_the_key(tmp_path, http):
    secret = "Unsupported value: 'temperature' " + KEY   # 받은 글에 키가 있어도 오류 글에는 가려짐(0.12.x 그대로)
    err = urllib.error.HTTPError("http://127.0.0.1:9/v1/chat/completions", 400, "Bad Request", {},
                                 io.BytesIO(json.dumps({"error": {"message": secret, "code": "unsupported_value"}}).encode()))
    http[1].append(err)
    res, _ = run(folder(tmp_path, node(extra="reasoning_effort: low"), reasoning=[LUNA]))
    assert res["status"] == "failed" and "HTTP 400" in json.dumps(res["error"], ensure_ascii=False)
    assert KEY not in json.dumps(res["error"], ensure_ascii=False)


# ------------------------------------------------------------------ R5 trace
def test_r5_record_has_the_sent_value(tmp_path, http):
    _, meta = run(folder(tmp_path, node(temperature=False), reasoning=[LUNA]))
    assert meta["request_config"] == {"temperature": None, "max_tokens": None, "reasoning_effort": "none"}
    json.dumps(meta["request_config"])


def test_r5_record_says_what_was_not_sent(tmp_path, http):
    _, meta = run(folder(tmp_path, node(model="gpt-4.1-mini", extra="reasoning_effort: low"), reasoning=[LUNA]))
    assert meta["request_config"] == {"temperature": 0, "max_tokens": None, "reasoning_effort_skipped": "low"}


# ------------------------------------------------------------------ R7 가짜 provider
class FixedChat:
    """The receiving fakes' fixed shape (deploy fake_provider · task10 quirks): strict · seed only."""

    def __init__(self):
        self.calls = []

    def chat(self, provider, model, messages, schema=None, temperature=None, max_tokens=None, *, strict=False, seed=None):
        self.calls.append(model)
        return OK, {"prompt_tokens": 1, "completion_tokens": 1}


def test_r7_a_fixed_fake_runs_a_pack_without_the_field(tmp_path):
    fake = FixedChat()
    res, _ = run(folder(tmp_path, reasoning=["gpt-4.1-mini"]), fake)
    assert res["status"] == "succeeded" and fake.calls == ["gpt-4.1-mini"]


def test_r7_a_fixed_fake_gets_one_clear_error(tmp_path):
    fake = FixedChat()
    res, _ = run(folder(tmp_path, node(temperature=False), reasoning=[LUNA]), fake)
    text = json.dumps(res["error"], ensure_ascii=False)
    assert res["status"] == "failed" and fake.calls == [] and "provider가 reasoning_effort를 받지 않습니다" in text


def test_r7_a_kw_fake_gets_the_keyword(tmp_path):
    calls = []

    class Kw:
        def chat(self, *a, **kw):
            calls.append(kw)
            return OK, {}
    res, _ = run(folder(tmp_path, node(temperature=False), reasoning=[LUNA]), Kw())
    assert res["status"] == "succeeded" and calls == [{"reasoning_effort": "none"}]


# ------------------------------------------------------------------ R8 모델 바꿈(효율 바퀴 꼴)
def test_r8_a_swapped_model_outside_the_list_warns_and_sends_nothing(tmp_path, http):
    from packrt.ir.patch import apply_patch
    base = folder(tmp_path, node(temperature=False), reasoning=[LUNA])
    out = tmp_path / "cand"
    res = apply_patch(base, [{"op": "set", "graph": GRAPH, "node": "draft", "param": "model", "value": "gpt-4.1-mini"}], out_dir=out)
    assert res.ok, res.errors
    loaded = load_pack(out)
    assert loaded.ok and any("reasoning_effort는 보내지 않습니다" in m for m in msgs(loaded, "warnings"))
    r, meta = run(out)
    assert r["status"] == "succeeded" and "reasoning_effort" not in json.loads(http[0][0])
    assert meta["request_config"].get("reasoning_effort_skipped") == "none"


def test_r8_the_wheel_cannot_edit_the_effort():
    from packrt.ir.patch import EDITABLE
    assert "reasoning_effort" not in EDITABLE["llm"]   # Q-2 (나): 보기만


# ------------------------------------------------------------------ R9 max_completion_tokens
def test_r9_a_listed_model_gets_max_completion_tokens(tmp_path, http):
    run(folder(tmp_path, node(extra="reasoning_effort: none, max_tokens: 50", temperature=False), reasoning=[LUNA]))
    body = json.loads(http[0][0])
    assert body["max_completion_tokens"] == 50 and "max_tokens" not in body


def test_r9_a_model_outside_the_list_keeps_max_tokens(tmp_path, http):
    run(folder(tmp_path, node(model="gpt-4.1-mini", extra="max_tokens: 50", temperature=False), reasoning=[LUNA]))
    body = json.loads(http[0][0])
    assert body["max_tokens"] == 50 and "max_completion_tokens" not in body
