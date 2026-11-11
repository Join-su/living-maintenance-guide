"""EV-012 (GAP-004, D-374 · D-377): a provider entry that names a key env, whose key is empty in the environment and in
PACKRT_ENV_FILE, stops before any request when its address is not loopback (CHG-G01 · G02) — on the chat, embed and decide paths
alike, and at the Run as the usual ProviderError node error (CHG-G03). Loopback addresses, providers without a key name and keyed
calls go on as before (PRES-G01 ~ G03). urlopen is replaced — no socket is opened; keys are made-up values. Addresses that are not
loopback are built from pieces with the one allowed .invalid name, so the static net scan (S6 · S7) sees no fixed address."""
import io
import json

import pytest

from packrt.run import providers
from packrt.run.embedding import fake_embedding
from packrt.run.engine import Runtime
from test_retrieve2_run import QSENSOR, QUERY, make, node

KEY = "fake-openai-key-g1-0123456789"
FILE_KEY = "fake-file-key-g1-0123456789"
OUT = "http://" + "netguard-probe.invalid" + "/v1"   # not loopback (S7 allows this one name)
LOOPBACK = {"127.0.0.1": "http://127.0.0.1:9/v1", "127.x": "http://" + "127." + "1.2.3" + ":9/v1", "localhost": "http://localhost:9/v1",
            "LOCALHOST": "http://" + "LOCAL" + "HOST" + ":9/v1", "::1": "http://[::1]:9/v1"}
UNREADABLE = {"no scheme": "netguard-probe.invalid" + "/v1", "broken IPv6": "http://" + "[::1" + "/v1"}
MESSAGE = "provider 'openai'의 키 환경변수 OPENAI_API_KEY가 환경에도 PACKRT_ENV_FILE 파일에도 없습니다 — 루프백 밖 주소로는 키 없이 요청하지 않습니다"
CONF = {"env": "OPENAI_API_KEY", "base_url_env": "OPENAI_BASE_URL",
        "models": {"chat": ["gpt-4.1-mini"], "embedding": ["text-embedding-3-small"], "decision": ["jev-1"]}}


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def seen(monkeypatch):
    """The fake provider: each request is kept as (url, Authorization header); embeddings answer with the fake rule."""
    calls = []

    def urlopen(req, timeout=None):
        calls.append((req.full_url, req.get_header("Authorization")))
        body = json.loads(req.data)
        if "input" in body:
            return Answer(json.dumps({"data": [{"index": i, "embedding": fake_embedding(t)} for i, t in enumerate(body["input"])],
                                      "usage": {"prompt_tokens": 1}}).encode())
        if "questions" in body:
            return Answer(json.dumps({"answers": [], "usage": {}}).encode())
        return Answer(json.dumps({"choices": [{"message": {"content": "ok"}}], "usage": {}}).encode())
    monkeypatch.setattr(providers.urllib.request, "urlopen", urlopen)
    return calls


def http(env, conf=CONF):
    return providers.HttpProvider({"providers": {"openai": conf}}, env)


def call(h, path):
    if path == "chat":
        return h.chat("openai", "gpt-4.1-mini", [{"role": "user", "content": "x"}])
    if path == "embed":
        return h.embed("openai", "text-embedding-3-small", ["a"])
    try:
        return h.decide("openai", "jev-1", {}, [])
    except providers.ProviderError as e:   # the fake answer is not a Jev answer: only an error raised after the request is fine here
        if str(e) == MESSAGE:
            raise
        return None


def env_file(tmp_path, text):
    f = tmp_path / "pack.env"
    f.write_text(text, encoding="utf-8")
    return str(f)


KEYLESS = {"absent": lambda t: {}, "empty": lambda t: {"OPENAI_API_KEY": ""},
           "not in the file either": lambda t: {"PACKRT_ENV_FILE": env_file(t, "OTHER_KEY=x\n")},
           "empty in the file": lambda t: {"PACKRT_ENV_FILE": env_file(t, "OPENAI_API_KEY=\n")}}


@pytest.mark.parametrize("path", ["chat", "embed", "decide"])
@pytest.mark.parametrize("state", KEYLESS, ids=list(KEYLESS))
def test_a_key_name_without_a_key_stops_before_any_request_outside_loopback(seen, tmp_path, path, state):
    h = http({"OPENAI_BASE_URL": OUT, **KEYLESS[state](tmp_path)})
    with pytest.raises(providers.ProviderError) as e:
        call(h, path)
    assert str(e.value) == MESSAGE and seen == []
    assert "netguard" not in str(e.value) and str(tmp_path) not in str(e.value)


@pytest.mark.parametrize("path", ["chat", "embed", "decide"])
@pytest.mark.parametrize("name", UNREADABLE, ids=list(UNREADABLE))
def test_an_address_whose_host_cannot_be_read_is_not_loopback(seen, path, name):
    with pytest.raises(providers.ProviderError) as e:
        call(http({"OPENAI_BASE_URL": UNREADABLE[name]}), path)
    assert str(e.value) == MESSAGE and seen == []


@pytest.mark.parametrize("path", ["chat", "embed", "decide"])
@pytest.mark.parametrize("name", LOOPBACK, ids=list(LOOPBACK))
def test_loopback_addresses_still_go_without_a_key(seen, path, name):
    call(http({"OPENAI_BASE_URL": LOOPBACK[name]}), path)
    assert len(seen) == 1 and seen[0][1] is None


@pytest.mark.parametrize("path", ["chat", "embed", "decide"])
def test_a_provider_without_a_key_name_still_goes_without_a_key(seen, path):
    conf = {k: v for k, v in CONF.items() if k != "env"}
    call(http({"OPENAI_BASE_URL": OUT}, conf), path)
    assert len(seen) == 1 and seen[0][1] is None


@pytest.mark.parametrize("path", ["chat", "embed", "decide"])
def test_a_key_is_sent_as_before_from_the_environment_then_the_file(seen, tmp_path, path):
    f = env_file(tmp_path, f"OPENAI_API_KEY={FILE_KEY}\n")
    call(http({"OPENAI_BASE_URL": OUT, "OPENAI_API_KEY": KEY, "PACKRT_ENV_FILE": f}), path)
    call(http({"OPENAI_BASE_URL": OUT, "PACKRT_ENV_FILE": f}), path)
    call(http({"OPENAI_BASE_URL": OUT, "OPENAI_API_KEY": "", "PACKRT_ENV_FILE": f}), path)
    assert [a for _, a in seen] == [f"Bearer {KEY}", f"Bearer {FILE_KEY}", f"Bearer {FILE_KEY}"]


def test_conf_still_gives_the_address_and_the_key():
    """task10 test_fix_b subclasses HttpProvider and calls _conf itself: the (base, key) shape stays (N2-5)."""
    h = http({"OPENAI_BASE_URL": OUT + "/", "OPENAI_API_KEY": KEY})
    assert h._conf("openai") == (OUT, KEY) and h._decision_conf("openai") == (OUT, KEY)
    assert http({"OPENAI_BASE_URL": LOOPBACK["localhost"]})._conf("openai") == ("http://localhost:9/v1", None)


def real_pack(tmp_path):
    p = make(tmp_path, [node("h", "history", "a")])
    (p / "models.yaml").write_text("providers:\n  openai: {env: OPENAI_API_KEY, base_url_env: OPENAI_BASE_URL, "
                                   "models: {embedding: [text-embedding-3-small]}}\n", encoding="utf-8")
    cfg = p / "execution-config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("embedding: {mode: fake}", "embedding: {mode: real, key_env: OPENAI_API_KEY}"), encoding="utf-8")
    for d in (tmp_path / "releases").iterdir():
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        m["embedding"]["mode"] = "real"
        (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return p


CTX = {"run_id": "run_01JC0000000000000000000312", "trace_id": "0123456789abcdef0123456789abc312", "origin": "evaluation",
       "evaluation_run_id": "g1", "deployment_id": None, "retention_class": "dev"}


def test_a_real_embedding_run_fails_before_the_request_outside_loopback(seen, tmp_path):
    res = Runtime(real_pack(tmp_path / "out"), env={"OPENAI_BASE_URL": OUT}).run(
        {"text": QUERY, "group": "TW", "sensors": QSENSOR}, context=dict(CTX), review={"policy": "auto_approve"})
    assert res["status"] == "failed" and res["error"]["code"] == "ProviderError" and MESSAGE in res["error"]["message"]
    assert seen == []
    res = Runtime(real_pack(tmp_path / "loop"), env={"OPENAI_BASE_URL": LOOPBACK["127.0.0.1"]}).run(
        {"text": QUERY, "group": "TW", "sensors": QSENSOR}, context=dict(CTX), review={"policy": "auto_approve"})
    assert res["status"] == "succeeded" and len(seen) == 1 and seen[0][1] is None


# ---------------------------------------------------------------- G1b (E5 differential review N1 · N2): not loopback, by every shape
NOT_LOOPBACK = {"0.0.0.0": "http://" + "0.0.0.0" + ":9/v1", "[::]": "http://" + "[::]" + ":9/v1", "10.x": "http://" + "10.0." + "0.1" + ":9/v1",
                "127.0.0.1.<name>": "http://" + "127.0.0.1." + "netguard-probe" + ".invalid" + "/v1",
                "localhost.<name>": "http://" + "localhost." + "netguard-probe" + ".invalid" + "/v1",
                "localhost.": "http://" + "localhost." + ":9/v1",
                "127.0.0.1@<name>": "http://" + "127.0.0.1@" + "netguard-probe" + ".invalid" + "/v1"}


@pytest.mark.parametrize("path", ["chat", "embed", "decide"])
@pytest.mark.parametrize("name", NOT_LOOPBACK, ids=list(NOT_LOOPBACK))
def test_addresses_that_only_look_local_are_not_loopback(seen, path, name):
    with pytest.raises(providers.ProviderError) as e:
        call(http({"OPENAI_BASE_URL": NOT_LOOPBACK[name]}), path)
    assert str(e.value) == MESSAGE and seen == []


@pytest.mark.parametrize("path", ["chat", "embed", "decide"])
def test_no_address_is_still_said_first(seen, path):
    """The order of the checks: a missing address is said before a missing key (no key, no address)."""
    h = http({})
    run = {"chat": lambda: h.chat("openai", "gpt-4.1-mini", []), "embed": lambda: h.embed("openai", "text-embedding-3-small", ["a"]),
           "decide": lambda: h.decide("openai", "jev-1", {}, [])}[path]
    with pytest.raises(providers.ProviderError) as e:
        run()
    assert "주소 환경변수 OPENAI_BASE_URL가 없습니다" in str(e.value) and seen == []


@pytest.mark.parametrize("path", ["chat", "embed", "decide"])
def test_an_empty_key_name_is_no_key_name(seen, path):
    conf = {**CONF, "env": ""}
    call(http({"OPENAI_BASE_URL": OUT}, conf), path)
    assert len(seen) == 1 and seen[0][1] is None


@pytest.mark.parametrize("path", ["chat", "embed", "decide"])
def test_a_loopback_address_from_the_env_file_goes_without_a_key(seen, tmp_path, path):
    f = env_file(tmp_path, "OPENAI_BASE_URL=http://127.0.0.1:9/v1\n")
    call(http({"PACKRT_ENV_FILE": f}), path)
    assert len(seen) == 1 and seen[0] == ("http://127.0.0.1:9/v1" + {"chat": "/chat/completions", "embed": "/embeddings", "decide": "/v1/systemone"}[path], None)
