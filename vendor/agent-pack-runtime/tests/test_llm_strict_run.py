"""V-RUN (EV-002 CHG-L04 · L05 · L06, PRES-L03 ~ L06 · L08): a node that turns strict or seed on sends `strict: true` · `seed` (root
$schema · $id left out of the sent copy), passes them as keyword arguments and records them with the answering backend; a refusal
or a cut answer says why. A node without them (or with `strict: false` only) sends the 7f565ac body byte for byte, calls the
provider with the six positions and records what 7f565ac recorded (run EV-002 evidence/LS-0-characterization.json).
urllib.request.urlopen is replaced — no socket is opened."""
import io
import json
import shutil
import sys
from pathlib import Path

import pytest

from packrt.run import providers
from packrt.run.engine import Runtime

FIX = Path(__file__).resolve().parent / "fixtures" / "llm_min"
KEY = "fake-openai-key-ls2-0123456789"
ENV = {"OPENAI_API_KEY": KEY, "OPENAI_BASE_URL": "http://127.0.0.1:9/v1"}
NODE = "output: Answer, temperature: 0}"
TICKET = {"id": "T-1", "text": "결제가 두 번 됐어요"}
OK = json.dumps({"summary": "결제 두 번", "category": "billing", "confidence": 0.9}, ensure_ascii=False)
# taken with the 7f565ac code before this change (LS-0, tests/fixtures/llm_min_off.json)
OFF = json.loads((FIX.parent / "llm_min_off.json").read_text(encoding="utf-8"))
OFF_BODY, OFF_META, OFF_ERRORS = OFF["body"], OFF["meta"], OFF["errors"]


class Recorder:
    def __init__(self):
        self.events, self.payloads = [], []

    def put_payload(self, value, *, role, retention_class):
        self.payloads.append({"role": role, "value": value, "retention_class": retention_class})
        return {"ref": "sha256:" + f"{len(self.payloads):064x}", "size": 0, "media_type": "application/json"}

    def emit(self, event):
        self.events.append(event)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def reply(content, finish="stop", refusal=None):
    return {"id": "chatcmpl-ls0", "object": "chat.completion", "model": "gpt-4.1-mini-2025-04-14", "system_fingerprint": "fp_ls0",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content, "refusal": refusal}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}


def runtime(tmp_path, params=None):
    work = tmp_path / "p"
    shutil.copytree(FIX, work)
    if params:
        text = (work / "pack.yaml").read_text(encoding="utf-8")
        (work / "pack.yaml").write_text(text.replace(NODE, NODE[:-1] + ", " + params + "}"), encoding="utf-8")
    roots = tmp_path / "releases"
    roots.mkdir()
    (work / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: ask\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{roots}"]\nstores: {{}}\nmodels: {{}}\n', encoding="utf-8")
    return Runtime(work, env=ENV)


@pytest.fixture
def http(monkeypatch):
    seen, answers = [], []

    def fake_urlopen(req, timeout=None):
        seen.append(req.data.decode("utf-8"))
        return Resp(json.dumps(answers.pop(0) if answers else reply(OK)).encode())
    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    return seen, answers


def run(rt, provider=None):
    rt.provider = provider or providers.HttpProvider(rt.models, ENV)
    rec = Recorder()
    res = rt.run(TICKET, sink=rec, review={"policy": "auto_approve"})
    meta = [e for e in rec.events if e["type"] == "node_ended" and e["node_id"] == "draft"]
    return res, rec, (meta[0]["body"]["meta"] if meta else None)


@pytest.mark.parametrize("params", [None, "strict: false"])
def test_off_body_is_byte_identical(tmp_path, http, params):
    res, rec, meta = run(runtime(tmp_path, params))
    assert res["status"] == "succeeded" and http[0] == [OFF_BODY]


@pytest.mark.parametrize("params", [None, "strict: false"])
def test_off_record_is_unchanged(tmp_path, http, params):
    _, _, meta = run(runtime(tmp_path, params))
    assert meta == OFF_META


@pytest.mark.parametrize("which, content, extra", [("run_not_json", "요약: 결제 두 번", {}),
                                                   ("run_schema", json.dumps({"summary": "", "category": "billing", "confidence": 0.9}), {}),
                                                   ("run_refusal", None, {"refusal": "I'm sorry, I cannot assist with that request."}),
                                                   ("run_length", '{"summary": "결제', {"finish": "length"})])
@pytest.mark.parametrize("params", [None, "strict: false"])
def test_off_failure_texts_are_unchanged(tmp_path, http, which, content, extra, params):
    http[1].append(reply(content, **extra))
    res, _, _ = run(runtime(tmp_path, params))
    assert res["status"] == "failed" and res["error"] == OFF_ERRORS[which]


class FixedChat:
    """The fixed six-position shape the receiving fakes have (llmops · deploy · task10 tests)."""

    def __init__(self):
        self.calls = []

    def chat(self, provider, model, messages, schema=None, temperature=None, max_tokens=None):
        self.calls.append((provider, model, schema is not None, temperature, max_tokens))
        return OK, {"prompt_tokens": 1, "completion_tokens": 1}


@pytest.mark.parametrize("params", [None, "strict: false"])
def test_off_node_calls_a_fixed_shape_provider(tmp_path, params):
    fake = FixedChat()
    res, _, _ = run(runtime(tmp_path, params), fake)
    assert res["status"] == "succeeded" and fake.calls == [("openai", "gpt-4.1-mini", True, 0, None)]


def test_on_node_passes_keywords_only(tmp_path):
    calls = []

    class Kw:
        def chat(self, *a, **kw):
            calls.append((len(a), kw))
            return OK, {}
    res, _, _ = run(runtime(tmp_path, "strict: true, seed: 7"), Kw())
    assert res["status"] == "succeeded" and calls == [(6, {"strict": True, "seed": 7})]


def test_on_body_is_strict_with_seed_and_no_root_schema_id(tmp_path, http):
    rt = runtime(tmp_path, "strict: true, seed: 7")
    original = json.dumps(rt.schemas["Answer"], sort_keys=True)
    res, rec, meta = run(rt)
    body = json.loads(http[0][0])
    sent = body["response_format"]["json_schema"]
    assert res["status"] == "succeeded" and sent["strict"] is True and body["seed"] == 7 and list(body)[-1] == "seed"
    assert "$schema" not in sent["schema"] and "$id" not in sent["schema"] and sent["schema"]["title"] == "Answer"
    assert {k: v for k, v in rt.schemas["Answer"].items() if k not in ("$schema", "$id")} == sent["schema"]
    assert json.dumps(rt.schemas["Answer"], sort_keys=True) == original   # the Pack's dict is not changed


def test_seed_alone_keeps_strict_false_and_the_whole_schema(tmp_path, http):
    run(runtime(tmp_path, "seed: 3"))
    body = json.loads(http[0][0])
    assert body["response_format"]["json_schema"]["strict"] is False and "$schema" in body["response_format"]["json_schema"]["schema"]
    assert body["seed"] == 3


def test_on_record_has_the_values_and_the_backend(tmp_path, http):
    _, rec, meta = run(runtime(tmp_path, "strict: true, seed: 7"))
    assert meta["request_config"] == {"temperature": 0, "max_tokens": None, "strict": True, "seed": 7}
    assert meta["system_fingerprint"] == "fp_ls0" and meta["response_model"] == "gpt-4.1-mini-2025-04-14"
    assert {k: v for k, v in meta.items() if k not in ("request_config", "system_fingerprint", "response_model")} == \
        {k: v for k, v in OFF_META.items() if k != "request_config"}


def test_seed_alone_records_only_the_seed(tmp_path, http):
    _, _, meta = run(runtime(tmp_path, "seed: 3"))
    assert meta["request_config"] == {"temperature": 0, "max_tokens": None, "seed": 3} and meta["system_fingerprint"] == "fp_ls0"


def test_on_record_without_a_fingerprint_has_no_new_key(tmp_path, http):
    r = reply(OK)
    del r["system_fingerprint"]
    http[1].append(r)
    _, _, meta = run(runtime(tmp_path, "strict: true"))
    assert "system_fingerprint" not in meta and meta["response_model"] == "gpt-4.1-mini-2025-04-14"


def test_a_strict_refusal_says_so(tmp_path, http):
    http[1].append(reply(None, refusal="I'm sorry, I cannot assist with that request."))
    res, _, _ = run(runtime(tmp_path, "strict: true"))
    assert res["error"]["code"] == "output_refused" and res["error"]["message"] == "모델이 답을 거부했습니다: I'm sorry, I cannot assist with that request."


def test_a_strict_empty_answer_without_refusal(tmp_path, http):
    http[1].append(reply(None))
    res, _, _ = run(runtime(tmp_path, "strict: true"))
    assert (res["error"]["code"], res["error"]["message"]) == ("output_not_json", "모델 출력이 비었습니다")


def test_a_strict_cut_answer_says_why(tmp_path, http):
    http[1].append(reply('{"summary": "결제', finish="length"))
    res, _, _ = run(runtime(tmp_path, "strict: true"))
    assert res["error"]["code"] == "output_not_json"
    assert res["error"]["message"] == OFF_ERRORS["run_length"]["message"] + " (finish_reason length — max_tokens에 닿음)"


def test_a_strict_schema_miss_still_checked_by_packrt(tmp_path, http):
    """strict keeps the shape; value limits like minLength are still checked against the Pack's own schema."""
    http[1].append(reply(json.dumps({"summary": "", "category": "billing", "confidence": 0.9}), finish="length"))
    res, _, _ = run(runtime(tmp_path, "strict: true"))
    assert res["error"]["message"] == OFF_ERRORS["run_schema"]["message"] + " (finish_reason length — max_tokens에 닿음)"


def test_seed_only_node_keeps_the_old_failure_texts(tmp_path, http):
    http[1].append(reply('{"summary": "결제', finish="length"))
    res, _, _ = run(runtime(tmp_path, "seed: 3"))
    assert res["error"] == OFF_ERRORS["run_length"]


def test_on_node_records_no_key(tmp_path, http):
    res, rec, _ = run(runtime(tmp_path, "strict: true, seed: 7"))
    dump = json.dumps(rec.events, ensure_ascii=False, default=str) + json.dumps(rec.payloads, ensure_ascii=False, default=str)
    assert res["status"] == "succeeded" and KEY not in dump and KEY[-4:] not in dump


def test_http_chat_without_the_new_keywords_is_the_old_body(monkeypatch):
    """HttpProvider.chat itself: no keyword → the 0.10.0 body (strict false, no seed)."""
    seen = []
    monkeypatch.setattr(providers.urllib.request, "urlopen", lambda req, timeout=None: (seen.append(json.loads(req.data)), Resp(json.dumps(reply(OK)).encode()))[1])
    p = providers.HttpProvider({"providers": {"openai": {"env": "OPENAI_API_KEY", "base_url_env": "OPENAI_BASE_URL"}}}, ENV)
    p.chat("openai", "m", [{"role": "user", "content": "x"}], {"$schema": "s", "type": "object"}, 0, 5)
    assert seen[0] == {"model": "m", "messages": [{"role": "user", "content": "x"}],
                       "response_format": {"type": "json_schema", "json_schema": {"name": "output", "schema": {"$schema": "s", "type": "object"}, "strict": False}},
                       "temperature": 0, "max_tokens": 5}


def test_a_strict_empty_cut_answer_says_why(tmp_path, http):
    http[1].append(reply(None, finish="length"))
    res, _, _ = run(runtime(tmp_path, "strict: true"))
    assert res["error"]["message"] == "모델 출력이 비었습니다 (finish_reason length — max_tokens에 닿음)"


@pytest.mark.parametrize("raw", [{"choices": []}, {"choices": [None]}, {"choices": [{"message": None}]}, {"choices": "x"}, "x", None])
def test_a_strict_odd_raw_answer_still_fails_cleanly(tmp_path, raw):
    class Odd:
        def chat(self, *a, **kw):
            return None, {}

        def last_response(self):
            return raw
    res, _, _ = run(runtime(tmp_path, "strict: true"), Odd())
    assert (res["error"]["code"], res["error"]["message"]) == ("output_not_json", "모델 출력이 비었습니다")


def test_a_retry_keeps_only_its_own_fingerprint(tmp_path, http):
    first = reply("요약: 결제 두 번")
    second = reply(OK)
    del second["system_fingerprint"]
    http[1].extend([first, second])
    rt = runtime(tmp_path, "strict: true")
    pack = rt.dir / "pack.yaml"
    pack.write_text(pack.read_text(encoding="utf-8").replace(
        "reads: [ticket], writes: [answer]}", "reads: [ticket], writes: [answer], on_error: {retry: 1, then: fail}}"), encoding="utf-8")
    res, _, meta = run(Runtime(rt.dir, env=ENV))
    assert len(http[0]) == 2   # the first answer is not JSON, the retry succeeds
    assert res["status"] == "succeeded" and "system_fingerprint" not in meta and meta["response_model"] == "gpt-4.1-mini-2025-04-14"
