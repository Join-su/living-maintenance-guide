"""V-HTTP-CHAT (EV-001 CHG-107, PRES-106 · PRES-108): chat and embed error bodies are masked before they are cut, like decide —
no key and no key fragment at the cut; the error prefix, ProviderError and a key-free ASCII body stay as in 0.9.0.
urllib.request.urlopen is replaced — no socket is opened."""
import io
import json
import urllib.error

import pytest

from packrt.run import providers
from packrt.run.providers import HttpProvider, ProviderError

KEY = "fake-openai-key-0123456789abcdef"
MODELS = {"providers": {"openai": {"env": "OPENAI_API_KEY", "base_url_env": "OPENAI_BASE_URL",
                                   "models": {"chat": ["gpt-4.1-mini"], "embedding": ["text-embedding-3-small"]}}}}
ENV = {"OPENAI_API_KEY": KEY, "OPENAI_BASE_URL": "http://127.0.0.1:9/v1"}
CALLS = {"chat": ("/chat/completions", lambda p: p.chat("openai", "gpt-4.1-mini", [{"role": "user", "content": "x"}])),
         "embed": ("/embeddings", lambda p: p.embed("openai", "text-embedding-3-small", ["x"]))}


def http_error(code, body):
    return urllib.error.HTTPError("http://127.0.0.1:9/v1", code, "err", {}, io.BytesIO(body))


@pytest.fixture
def plan(monkeypatch):
    steps, seen = [], []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        raise steps.pop(0)
    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    return steps, seen


def message(plan, which, body, code=401, env=ENV):
    steps, seen = plan
    steps.append(http_error(code, body))
    with pytest.raises(ProviderError) as e:
        CALLS[which][1](HttpProvider(MODELS, env))
    assert len(seen) == 1   # no retry on chat · embed
    return str(e.value)


@pytest.mark.parametrize("which", ["chat", "embed"])
def test_the_whole_key_is_masked(plan, which):
    m = message(plan, which, f'{{"error": {{"message": "bad key {KEY}"}}}}'.encode())
    assert m.startswith(f"openai {CALLS[which][0]} HTTP 401: ") and KEY not in m and "[가림]" in m


@pytest.mark.parametrize("which", ["chat", "embed"])
@pytest.mark.parametrize("start", [280, 286, 292])
def test_no_key_fragment_is_left_at_the_cut(plan, which, start):
    """The key starts just before the 300 cut, so cutting before masking would leave its first part (N11)."""
    m = message(plan, which, ("x" * start + KEY + "y" * 50).encode())
    assert KEY[:8] not in m and "[가림]" in m


@pytest.mark.parametrize("which", ["chat", "embed"])
def test_a_key_free_ascii_body_is_the_same_text_as_before(plan, which):
    body = ('{"error": {"message": "' + "z" * 500 + '"}}').encode()
    old = f"openai {CALLS[which][0]} HTTP 500: {body[:300].decode('utf-8', 'replace')}"   # the 0.9.0 text
    assert message(plan, which, body, code=500) == old


def test_the_prefix_the_llmops_trace_test_reads_is_kept(plan):
    assert message(plan, "chat", b"{}", code=500).startswith("openai /chat/completions HTTP 500")


def test_without_a_key_the_body_is_only_cut(plan):
    env = {k: v for k, v in ENV.items() if k != "OPENAI_API_KEY"}
    body = ("a" * 400).encode()
    assert message(plan, "chat", body, env=env) == "openai /chat/completions HTTP 401: " + "a" * 300
    assert plan[1][0].get_header("Authorization") is None


def test_a_multibyte_body_is_cut_at_300_characters(plan):
    m = message(plan, "chat", ("가" * 400).encode())
    assert m == "openai /chat/completions HTTP 401: " + "가" * 300


@pytest.mark.parametrize("which", ["chat", "embed"])
def test_the_providers_half_masked_key_leaves_no_visible_end(plan, which):
    """GAP-003 (instruction 014): OpenAI's own 401 text shows the key half-masked ('sk-proj-****…NNNN'), which is not the key
    itself, so the key mask left its end; that shape becomes 'sk-***' before the cut."""
    end = "Qz7W"
    body = ('{"error": {"message": "Incorrect API key provided: sk-proj-' + "*" * 40 + end + '.", "code": "invalid_api_key"}}').encode()
    m = message(plan, which, body)
    assert end not in m and "Incorrect API key provided: sk-***." in m
