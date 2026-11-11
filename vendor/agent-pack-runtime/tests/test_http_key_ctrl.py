"""V-KEY-CTRL (EV-001 CHG-108, PRES-106): a key holding a control character (0-31, 127) stops decide, chat and embed before any
request is built, with only the env name in the text; valid keys behave as before. urlopen is replaced — no socket is opened."""
import io
import json

import pytest

from packrt.run import providers
from packrt.run.providers import HttpProvider, ProviderError

GOOD = "fake-key-abcdefghij0123456789"
MODELS = {"providers": {
    "openai": {"env": "OPENAI_API_KEY", "base_url_env": "OPENAI_BASE_URL", "models": {"chat": ["gpt-4.1-mini"], "embedding": ["text-embedding-3-small"]}},
    "jev": {"env": "JEV_API_KEY", "base_url_env": "JEV_BASE_URL", "models": {"decision": ["jev-1.13.0"]}}}}
BASES = {"OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "JEV_BASE_URL": "http://127.0.0.1:9"}
Q = {"ok": {"type": "noul", "criteria": {"true": "예", "false": "아니오"}}}
PATHS = {
    "decide": ("JEV_API_KEY", lambda p: p.decide("jev", "jev-1.13.0", {}, Q),
               {"model": "jev-1.13.0", "answers": {"ok": {"type": "noul", "noul": 0.8}}, "usage": {"input_tokens": 1, "output_tokens": 1}}),
    "chat": ("OPENAI_API_KEY", lambda p: p.chat("openai", "gpt-4.1-mini", [{"role": "user", "content": "x"}]),
             {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
    "embed": ("OPENAI_API_KEY", lambda p: p.embed("openai", "text-embedding-3-small", ["x"]),
              {"data": [{"index": 0, "embedding": [0.0, 1.0]}], "usage": {"prompt_tokens": 1}}),
}
CTRL = ["\n", "\r", "\x00", "\x1f", "\x7f", "\t"]


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Calls(list):
    answer = None


@pytest.fixture
def seen(monkeypatch):
    calls = Calls()

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        return Resp(json.dumps(calls.answer).encode())
    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    return calls


def bad_key(ch):
    return GOOD + ch + "tail"   # the control character sits after the first 8 characters, so 'no KEY[:8]' means something


@pytest.mark.parametrize("path", list(PATHS))
@pytest.mark.parametrize("ch", CTRL, ids=[hex(ord(c)) for c in CTRL])
def test_a_control_character_stops_the_call_before_any_request(seen, path, ch):
    env_name, call, _ = PATHS[path]
    key = bad_key(ch)
    with pytest.raises(ProviderError) as e:
        call(HttpProvider(MODELS, dict(BASES, **{env_name: key})))
    provider = "jev" if path == "decide" else "openai"
    # the whole text is fixed: only the env name — no value, no fragment (head or tail), no position
    assert str(e.value) == f"provider '{provider}'의 키 환경변수 {env_name} 값에 줄바꿈 · 제어 문자가 있습니다 — 값을 고쳐 주세요"
    assert seen == []


@pytest.mark.parametrize("path", list(PATHS))
def test_a_control_character_inside_an_env_file_value_is_refused_too(seen, tmp_path, path):
    env_name, call, _ = PATHS[path]
    f = tmp_path / ".env"
    f.write_text(f"{env_name}={GOOD}\x01tail\n", encoding="utf-8")
    with pytest.raises(ProviderError, match=env_name):
        call(HttpProvider(MODELS, dict(BASES, PACKRT_ENV_FILE=str(f))))
    assert seen == []


@pytest.mark.parametrize("path", list(PATHS))
def test_a_valid_key_is_sent_as_before_even_from_a_crlf_env_file(seen, tmp_path, path):
    env_name, call, answer = PATHS[path]
    seen.answer = answer
    f = tmp_path / ".env"
    f.write_bytes(f"{env_name}={GOOD}\r\n".encode())   # line ends are stripped when the file is read
    call(HttpProvider(MODELS, dict(BASES, PACKRT_ENV_FILE=str(f))))
    assert len(seen) == 1 and seen[0].get_header("Authorization") == f"Bearer {GOOD}"


def test_the_openai_format_refusal_comes_first(seen):
    """PRES-107: the provider-format check runs before the key check (N18)."""
    with pytest.raises(ProviderError, match="OpenAI 형식 provider만"):
        HttpProvider(MODELS, dict(BASES, JEV_API_KEY=bad_key("\n"))).chat("jev", "jev-1.13.0", [])


@pytest.mark.parametrize("path", list(PATHS))
def test_the_address_check_comes_before_the_key_check(seen, path):
    """N18: the key check runs after every existing check (provider, format, address)."""
    env_name, call, _ = PATHS[path]
    with pytest.raises(ProviderError, match="주소 환경변수"):
        call(HttpProvider(MODELS, {env_name: bad_key("\n")}))
