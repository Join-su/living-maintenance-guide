"""V-HTTP-DECIDE (EV-001 CHG-103, PRES-106 · PRES-107): HttpProvider.decide builds the Jev request (POST <base>/v1/systemone, Bearer
key, {"model", "state", "questions"}), maps usage, tries 429/529 again and stops on other errors, never puts the key in an error;
chat stays OpenAI-format only. urllib.request.urlopen is replaced — no socket is opened."""
import io
import json
import urllib.error

import pytest

from packrt.run import providers
from packrt.run.providers import HttpProvider, ProviderError

KEY = "fake-jev-key-0123456789"
MODELS = {"providers": {"jev": {"env": "JEV_API_KEY", "base_url_env": "JEV_BASE_URL", "models": {"decision": ["jev-1.13.0"]}},
                        "openai": {"env": "OPENAI_API_KEY", "base_url_env": "OPENAI_BASE_URL", "models": {"chat": ["gpt-4.1-mini"]}}}}
ENV = {"JEV_API_KEY": KEY, "JEV_BASE_URL": "http://127.0.0.1:9/", "OPENAI_BASE_URL": "http://127.0.0.1:9/v1"}
ANSWER = {"model": "jev-1.13.0", "answers": {"ok": {"type": "noul", "noul": 0.8}}, "usage": {"input_tokens": 12, "output_tokens": 1}}
Q = {"ok": {"type": "noul", "criteria": {"true": "예", "false": "아니오"}}}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body=b"{}"):
    return urllib.error.HTTPError("http://127.0.0.1:9/v1/systemone", code, "err", {}, io.BytesIO(body))


@pytest.fixture
def calls(monkeypatch):
    seen, plan, waits = [], [], []

    def fake_urlopen(req, timeout=None):
        req.timeout_given = timeout
        seen.append(req)
        step = plan.pop(0) if plan else ANSWER
        if isinstance(step, Exception):
            raise step
        return Resp(json.dumps(step).encode())
    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(providers, "_sleep", waits.append)
    return seen, plan, waits


def test_request_shape_and_usage(calls):
    seen, plan, waits = calls
    p = HttpProvider(MODELS, ENV)
    raw, usage = p.decide("jev", "jev-1.13.0", {"text": "결제"}, Q)
    req = seen[0]
    assert req.full_url == "http://127.0.0.1:9/v1/systemone" and req.get_method() == "POST"
    assert req.get_header("Authorization") == f"Bearer {KEY}"
    assert json.loads(req.data) == {"model": "jev-1.13.0", "state": {"text": "결제"}, "questions": Q}
    assert raw == ANSWER and usage == {"input_tokens": 12, "output_tokens": 1} and p.last_response() == ANSWER
    assert p.usage == {"tokens_in": 12, "tokens_out": 1} and waits == []


@pytest.mark.parametrize("codes, tries, waited", [((429,), 2, [1]), ((529, 429), 3, [1, 2])])
def test_rate_limit_and_overload_are_tried_again(calls, codes, tries, waited):
    seen, plan, waits = calls
    plan.extend(http_error(c) for c in codes)
    raw, _ = HttpProvider(MODELS, ENV).decide("jev", "jev-1.13.0", {}, Q)
    assert raw == ANSWER and len(seen) == tries and waits == waited


def test_it_stops_after_the_last_retry(calls):
    seen, plan, waits = calls
    plan.extend(http_error(529) for _ in range(3))
    with pytest.raises(ProviderError, match="HTTP 529"):
        HttpProvider(MODELS, ENV).decide("jev", "jev-1.13.0", {}, Q)
    assert len(seen) == 3 and waits == [1, 2]


@pytest.mark.parametrize("code", [401, 422])
def test_other_errors_end_at_once_without_the_key(calls, code):
    seen, plan, waits = calls
    plan.append(http_error(code, body=f'{{"error": "bad key {KEY}"}}'.encode()))
    with pytest.raises(ProviderError) as e:
        HttpProvider(MODELS, ENV).decide("jev", "jev-1.13.0", {}, Q)
    assert f"HTTP {code}" in str(e.value) and KEY not in str(e.value) and len(seen) == 1 and waits == []


def test_a_provider_without_decision_models_is_refused(calls):
    with pytest.raises(ProviderError, match="decision 모델이 없습니다"):
        HttpProvider(MODELS, ENV).decide("openai", "gpt-4.1-mini", {}, Q)


def test_no_address_is_built_in(calls):
    env = {k: v for k, v in ENV.items() if k != "JEV_BASE_URL"}
    with pytest.raises(ProviderError, match="JEV_BASE_URL"):
        HttpProvider(MODELS, env).decide("jev", "jev-1.13.0", {}, Q)


def test_chat_still_takes_openai_only(calls):
    with pytest.raises(ProviderError, match="OpenAI 형식 provider만"):
        HttpProvider(MODELS, ENV).chat("jev", "jev-1.13.0", [{"role": "user", "content": "x"}])


def test_the_call_has_a_timeout(calls):
    seen, plan, waits = calls
    HttpProvider(MODELS, ENV).decide("jev", "jev-1.13.0", {}, Q)
    assert seen[0].timeout_given == 60


def test_no_part_of_the_key_is_left_where_the_error_body_is_cut(calls):
    seen, plan, waits = calls
    body = ("x" * 286 + KEY + "y" * 50).encode()   # the key straddles the 300-byte cut
    plan.append(http_error(401, body=body))
    with pytest.raises(ProviderError) as e:
        HttpProvider(MODELS, ENV).decide("jev", "jev-1.13.0", {}, Q)
    assert KEY[:8] not in str(e.value) and "[가림]" in str(e.value)
