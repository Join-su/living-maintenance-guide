"""EV-013 (D-396 (4) · D-404): two OpenAI keys. A provider entry may name a second key, `env_fallback`; when the first key gets
HTTP 401 · 403 · 429 on the chat · embed path and the second key has a value, the same request goes once more with it, and the
rest of that Run uses it first; every Run starts on the first key again (CHG-F01 ~ F03). Only the key slot is recorded, in the
node meta (runner line and node_ended), for providers that name a second key (CHG-F04). Other errors, an empty first key (GAP-004),
decide and providers without `env_fallback` stay as they were (PRES-F01 ~ F07). urlopen is replaced — no socket is opened; keys
are made-up values; addresses that are not loopback are built from pieces (static net scan S6 · S7)."""
import io
import json
import shutil
import sys
import threading
import urllib.error
from pathlib import Path

import pytest

from packrt.index.errors import IndexBuildError
from packrt.index.real import real_embedder
from packrt.run import providers
from packrt.run.embedding import fake_embedding
from packrt.run.engine import Runtime
from test_index_build import pack as build_pack
from test_provider_key_records import TICKET, Recorder
from test_provider_key_records import pack as chat_pack
from test_provider_key_required import CTX, QSENSOR, QUERY, real_pack

K1 = "fake-key-one-f1-0123456789"
K2 = "fake-key-two-f1-0123456789"
OUT = "http://" + "netguard-probe.invalid" + "/v1"
CONF = {"env": "OPENAI_API_KEY", "env_fallback": "OPENAI_API_KEY_2", "base_url_env": "OPENAI_BASE_URL",
        "models": {"chat": ["gpt-4.1-mini"], "embedding": ["text-embedding-3-small"], "decision": ["jev-1"]}}
ENV = {"OPENAI_BASE_URL": OUT, "OPENAI_API_KEY": K1, "OPENAI_API_KEY_2": K2}
REFUSE = {401: "invalid_api_key", 403: "unsupported_country_region_territory", 429: "insufficient_quota"}


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Fake:
    """The fake provider: `answer[key name]` = HTTP status for that key (200 answers). Keeps (key name, path) per request."""

    def __init__(self, answer, code=None):
        self.answer, self.code, self.seen = answer, code, []

    def __call__(self, req, timeout=None):
        auth = req.get_header("Authorization")
        who = {f"Bearer {K1}": "1", f"Bearer {K2}": "2"}.get(auth, auth)
        self.seen.append((who, req.full_url.rsplit("/", 1)[-1]))
        status = self.answer.get(who, 200)
        if status == "timeout":
            raise urllib.error.URLError("timed out")
        if status != 200:
            body = json.dumps({"error": {"message": f"bad {K1} {K2}", "code": self.code or REFUSE.get(status, "x")}}).encode()
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(body))
        data = json.loads(req.data)
        if "input" in data:
            return Answer(json.dumps({"data": [{"index": i, "embedding": fake_embedding(t)} for i, t in enumerate(data["input"])],
                                      "usage": {"prompt_tokens": 1}}).encode())
        if "questions" in data:
            return Answer(json.dumps({"answers": [], "usage": {}}).encode())
        return Answer(json.dumps({"choices": [{"message": {"content": "\"ok\""}}], "usage": {"prompt_tokens": 2, "completion_tokens": 1}}).encode())


@pytest.fixture
def fake(monkeypatch):
    def make(answer, code=None):
        f = Fake(answer, code)
        monkeypatch.setattr(providers.urllib.request, "urlopen", f)
        return f
    return make


def http(env=ENV, conf=CONF):
    return providers.HttpProvider({"providers": {"openai": conf}}, dict(env))


def call(h, path):
    if path == "chat":
        return h.chat("openai", "gpt-4.1-mini", [{"role": "user", "content": "x"}])
    return h.embed("openai", "text-embedding-3-small", ["a"])


def no_keys(text):
    return K1 not in text and K2 not in text and "OPENAI_API_KEY" not in text


# ---------------------------------------------------------------- CHG-F02: switch once, then the second key first in that Run
@pytest.mark.parametrize("path", ["chat", "embed"])
@pytest.mark.parametrize("status,code", [(401, None), (403, None), (429, "insufficient_quota"), (429, "rate_limit_exceeded")],
                         ids=["401", "403", "429-quota", "429-rate"])
def test_a_refused_first_key_goes_once_more_with_the_second(fake, path, status, code):
    f = fake({"1": status}, code)
    h = http()
    call(h, path)
    assert [w for w, _ in f.seen] == ["1", "2"] and h.last_key_slot() == 2
    call(h, path)
    assert [w for w, _ in f.seen] == ["1", "2", "2"] and h.last_key_slot() == 2   # the rest of the Run: the second key first


def test_the_second_key_refused_too_ends_with_both_slots_named_and_no_key(fake):
    f = fake({"1": 401, "2": 429})
    with pytest.raises(providers.ProviderError) as e:
        call(http(), "chat")
    assert [w for w, _ in f.seen] == ["1", "2"]
    assert str(e.value).startswith("openai /chat/completions HTTP 401(키 자리 1) → 키 자리 2 HTTP 429: ") and no_keys(str(e.value))
    assert "[가림]" in str(e.value)


def test_after_the_switch_the_second_key_alone_and_no_way_back(fake):
    f = fake({"1": 401})
    h = http()
    call(h, "chat")
    f.answer = {"1": 200, "2": 401}
    with pytest.raises(providers.ProviderError) as e:
        call(h, "chat")
    assert [w for w, _ in f.seen] == ["1", "2", "2"]
    assert str(e.value).startswith("openai /chat/completions HTTP 401(키 자리 2): ") and no_keys(str(e.value))


def test_on_the_second_key_a_non_switch_error_names_slot_2(fake):
    """N2-5 (1): the Run already on the second key, a 500 from it → 'HTTP 500(키 자리 2): …', no try with the first key."""
    f = fake({"1": 403})
    h = http()
    call(h, "embed")
    f.answer = {"2": 500}
    with pytest.raises(providers.ProviderError) as e:
        call(h, "embed")
    assert [w for w, _ in f.seen] == ["1", "2", "2"]
    assert str(e.value).startswith("openai /embeddings HTTP 500(키 자리 2): ") and no_keys(str(e.value))


def test_parallel_requests_still_go_out_together(monkeypatch):
    """N2-2: the slot lock is never held while a request is out — two requests of one provider are in flight at the same time."""
    meet = threading.Barrier(2, timeout=5)

    def urlopen(req, timeout=None):
        meet.wait()   # breaks (BrokenBarrierError) if the second request cannot start before the first one ends
        return Answer(json.dumps({"choices": [{"message": {"content": "\"ok\""}}], "usage": {}}).encode())
    monkeypatch.setattr(providers.urllib.request, "urlopen", urlopen)
    h, errors = http(), []

    def node():
        try:
            call(h, "chat")
        except Exception as e:   # noqa: BLE001 — collected for the assertion below
            errors.append(e)
    ts = [threading.Thread(target=node) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == []


def test_the_second_key_with_a_non_switch_error_says_its_own_status(fake):
    f = fake({"1": 429, "2": 500})
    with pytest.raises(providers.ProviderError) as e:
        call(http(), "embed")
    assert [w for w, _ in f.seen] == ["1", "2"]
    assert str(e.value).startswith("openai /embeddings HTTP 429(키 자리 1) → 키 자리 2 HTTP 500: ") and no_keys(str(e.value))


# ---------------------------------------------------------------- CHG-F02 · Q-5 (가): a Run starts on the first key again
def test_start_run_puts_the_provider_back_on_the_first_key(fake):
    f = fake({"1": 429})
    h = http()
    call(h, "chat")
    h.start_run()
    f.answer = {}
    call(h, "chat")
    assert [w for w, _ in f.seen] == ["1", "2", "1"] and h.last_key_slot() == 1


def test_parallel_nodes_of_one_run_share_the_slot(fake):
    """Two threads in a fixed order (N2-3): the first moves the Run to the second key, the second thread then starts there."""
    f = fake({"1": 429})
    h = http()
    slots = {}

    def node(name):
        call(h, "chat")
        slots[name] = h.last_key_slot()
    a = threading.Thread(target=node, args=("a",))
    a.start(); a.join()
    b = threading.Thread(target=node, args=("b",))
    b.start(); b.join()
    assert [w for w, _ in f.seen] == ["1", "2", "2"] and slots == {"a": 2, "b": 2}


def test_runs_on_one_runtime_each_start_on_the_first_key(fake, tmp_path):
    f = fake({"1": 429})
    rt = Runtime(chat_pack(tmp_path, "chat"))
    rt.provider = providers.HttpProvider({"providers": {"openai": CONF}}, ENV)
    assert rt.run(TICKET, review={"policy": "auto_approve"})["status"] == "succeeded"
    f.answer = {}
    assert rt.run(TICKET, review={"policy": "auto_approve"})["status"] == "succeeded"
    assert [w for w, _ in f.seen] == ["1", "2", "1"]


def test_a_provider_without_start_run_runs_as_before(fake, tmp_path):
    class P:
        def chat(self, *a, **k):
            return "\"ok\"", {"prompt_tokens": 1, "completion_tokens": 1}
    rt = Runtime(chat_pack(tmp_path, "chat"))
    rt.provider = P()
    res = rt.run(TICKET, review={"policy": "auto_approve"})
    assert res["status"] == "succeeded"


# ---------------------------------------------------------------- CHG-F03 · PRES-F02 · F03: no switch
@pytest.mark.parametrize("status", [400, 404, 500, "timeout"], ids=["400", "404", "500", "timeout"])
def test_other_errors_never_switch(fake, status):
    f = fake({"1": status})
    with pytest.raises((providers.ProviderError, urllib.error.URLError)) as e:
        call(http(), "chat")
    assert [w for w, _ in f.seen] == ["1"]
    if status != "timeout":
        assert str(e.value).startswith(f"openai /chat/completions HTTP {status}: ") and "키 자리" not in str(e.value) and no_keys(str(e.value))


def test_no_second_value_means_no_switch(fake):
    f = fake({"1": 401})
    with pytest.raises(providers.ProviderError) as e:
        call(http({k: v for k, v in ENV.items() if k != "OPENAI_API_KEY_2"}), "chat")
    assert [w for w, _ in f.seen] == ["1"] and str(e.value).startswith("openai /chat/completions HTTP 401: ")


def test_an_empty_first_key_still_stops_before_any_request(fake):
    """GAP-004 (EV-012): the first key empty, the address not loopback — no request, even with a second key."""
    f = fake({})
    with pytest.raises(providers.ProviderError) as e:
        call(http({"OPENAI_BASE_URL": OUT, "OPENAI_API_KEY_2": K2}), "chat")
    assert f.seen == [] and "루프백 밖 주소로는 키 없이 요청하지 않습니다" in str(e.value)


def test_a_second_key_with_a_control_character_is_never_sent(fake):
    f = fake({"1": 401})
    with pytest.raises(providers.ProviderError) as e:
        call(http({**ENV, "OPENAI_API_KEY_2": K2 + "\nx"}), "chat")
    assert [w for w, _ in f.seen] == ["1"]
    assert str(e.value).startswith("openai /chat/completions HTTP 401(키 자리 1): ")   # N2-5 (2): the first key's text, its slot named
    assert str(e.value).endswith("→ 키 자리 2 값에 줄바꿈 · 제어 문자가 있어 보내지 않음")
    assert K1 not in str(e.value) and K2 + "\nx" not in str(e.value)


def test_decide_never_switches(fake):
    f = fake({"1": 401})
    with pytest.raises(providers.ProviderError):
        http().decide("openai", "jev-1", {}, [])
    assert [w for w, _ in f.seen] == ["1"]


# ---------------------------------------------------------------- CHG-F01 · PRES-F01: the second name, read like the first
@pytest.mark.parametrize("fallback", [None, ""], ids=["no name", "empty name"])
def test_without_a_second_name_nothing_changes(fake, fallback):
    f = fake({"1": 401})
    conf = {k: v for k, v in CONF.items() if k != "env_fallback"}
    if fallback is not None:
        conf["env_fallback"] = fallback
    h = http(conf=conf)
    with pytest.raises(providers.ProviderError) as e:
        call(h, "chat")
    assert [w for w, _ in f.seen] == ["1"] and str(e.value).startswith("openai /chat/completions HTTP 401: ")
    f.answer = {}
    call(h, "chat")
    assert h.last_key_slot() is None


def test_the_second_key_comes_from_the_env_file_too(fake, tmp_path):
    f = fake({"1": 401})
    envf = tmp_path / "pack.env"
    envf.write_text(f"OPENAI_API_KEY_2={K2}\n", encoding="utf-8")
    h = http({"OPENAI_BASE_URL": OUT, "OPENAI_API_KEY": K1, "PACKRT_ENV_FILE": str(envf)})
    call(h, "chat")
    assert [w for w, _ in f.seen] == ["1", "2"]


def test_an_index_build_switches_from_its_key_env_to_the_second_name(fake, tmp_path):
    """N-3: build — key 1 = --key-env (here MY_EMB_KEY), key 2 = the provider's env_fallback in models.yaml."""
    f = fake({})
    p = build_pack(tmp_path / "b")
    (p / "models.yaml").write_text("providers:\n  openai: {env: OPENAI_API_KEY, env_fallback: OPENAI_API_KEY_2, base_url_env: OPENAI_BASE_URL, "
                                   "models: {embedding: [text-embedding-3-small]}}\n", encoding="utf-8")
    emb, _ = real_embedder(p, "start", "find_manuals", "MY_EMB_KEY", env={"OPENAI_BASE_URL": OUT, "MY_EMB_KEY": K1, "OPENAI_API_KEY_2": K2})
    f.answer = {"1": 429}
    emb(["a"])
    assert [w for w, _ in f.seen] == ["1", "2"]
    with pytest.raises(IndexBuildError):   # EV-011 pre-check looks at key 1 only
        real_embedder(p, "start", "find_manuals", "MY_EMB_KEY", env={"OPENAI_BASE_URL": OUT, "OPENAI_API_KEY_2": K2})


# ---------------------------------------------------------------- CHG-F04: the slot in the runner line and node_ended
def node_ended(rec, node):
    return [e["body"]["meta"] for e in rec.events if e["type"] == "node_ended" and e["node_id"] == node]


def test_an_llm_node_records_the_slot_in_both_places(fake, tmp_path):
    fake({"1": 429})
    rt = Runtime(chat_pack(tmp_path, "chat"))
    rt.provider = providers.HttpProvider({"providers": {"openai": CONF}}, ENV)
    rec, lines = Recorder(), []
    res = rt.run(TICKET, sink=rec, review={"policy": "auto_approve"}, on_debug=lines.append)
    assert res["status"] == "succeeded"
    assert node_ended(rec, "draft")[0]["key_slot"] == 2
    metas = [((ev.get("payload", {}).get("result") or {}).get("_meta") or {}).get("draft") for ev in lines if ev.get("type") == "task_result"]
    assert any(m and m.get("key_slot") == 2 for m in metas)
    assert no_keys(json.dumps([rec.events, rec.payloads], ensure_ascii=False, default=str))


def test_a_real_query_embedding_records_the_slot(fake, tmp_path):
    fake({"1": 401})
    p = real_pack(tmp_path / "r")
    (p / "models.yaml").write_text("providers:\n  openai: {env: OPENAI_API_KEY, env_fallback: OPENAI_API_KEY_2, base_url_env: OPENAI_BASE_URL, "
                                   "models: {embedding: [text-embedding-3-small]}}\n", encoding="utf-8")
    rec = Recorder()
    res = Runtime(p, env=ENV).run({"text": QUERY, "group": "TW", "sensors": QSENSOR}, context=dict(CTX), sink=rec, review={"policy": "auto_approve"})
    assert res["status"] == "succeeded" and node_ended(rec, "h")[0]["key_slot"] == 2


def test_no_second_name_no_slot_in_the_records(fake, tmp_path):
    fake({})
    rt = Runtime(chat_pack(tmp_path, "chat"))
    rt.provider = providers.HttpProvider({"providers": {"openai": {k: v for k, v in CONF.items() if k != "env_fallback"}}}, ENV)
    rec = Recorder()
    assert rt.run(TICKET, sink=rec, review={"policy": "auto_approve"})["status"] == "succeeded"
    assert "key_slot" not in node_ended(rec, "draft")[0]


def test_conf_keeps_its_shape():
    """task10's test subclass calls _conf: (base, first key), as before (N2-5 of EV-012)."""
    assert http()._conf("openai") == (OUT, K1)


# ---------------------------------------------------------------- F1c (D-404 덧 · 106-01, E5 differential review N1 · N2 · N3 · N4)
class Script:
    """urlopen answering in a fixed order: 'ok:<content>' answers, a number is that HTTP status. Keeps the key name per request."""

    def __init__(self, steps):
        self.steps, self.seen = list(steps), []

    def __call__(self, req, timeout=None):
        auth = req.get_header("Authorization")
        self.seen.append({f"Bearer {K1}": "1", f"Bearer {K2}": "2"}.get(auth, auth))
        s = self.steps.pop(0)
        if isinstance(s, str):
            return Answer(json.dumps({"choices": [{"message": {"content": s[3:]}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode())
        raise urllib.error.HTTPError(req.full_url, s, "err", {}, io.BytesIO(f"bad {K1} {K2}".encode()))


@pytest.mark.parametrize("steps,slots,last", [
    (["ok:not-json", 429, 429], ["1", "1", "2"], 2),    # attempt 1 answered on key 1, attempt 2 refused on both keys
    (["ok:not-json", 500], ["1", "1"], 1),              # attempt 2: a non-switch error on key 1
], ids=["both-refused", "key1-500"])
def test_a_retry_keeps_the_slot_of_its_own_last_request(monkeypatch, tmp_path, steps, slots, last):
    """E5-N1: node meta key_slot = the slot of the last request of the last attempt, also when that attempt failed at the provider."""
    s = Script(steps)
    monkeypatch.setattr(providers.urllib.request, "urlopen", s)
    rt = Runtime(chat_pack(tmp_path, "chat"))
    rt.provider = providers.HttpProvider({"providers": {"openai": CONF}}, ENV)
    n = dict(next(x for x in rt.graph_doc["nodes"] if x["id"] == "draft"), on_error={"retry": 1, "then": "route:END"})
    rt.provider.start_run()
    m = rt._wrap(n, None)({"ticket": TICKET})["_meta"]["draft"]
    assert s.seen == slots and m["key_slot"] == last and m["error"]["attempt"] == 2


def test_a_call_that_stops_before_any_request_records_no_slot(fake):
    """E5-N1: after a call on the second key, a call stopped before any request (GAP-004) leaves no slot from the earlier call."""
    fake({"1": 401})
    h = http()
    call(h, "chat")
    assert h.last_key_slot() == 2
    del h.env["OPENAI_API_KEY"]
    with pytest.raises(providers.ProviderError):
        call(h, "chat")
    assert h.last_key_slot() is None


@pytest.mark.parametrize("answer,slot", [({"1": 500}, 1), ({"1": 401, "2": 403}, 2), ({"1": "timeout"}, 1)], ids=["500", "both", "timeout"])
def test_a_failed_call_still_says_its_slot(fake, answer, slot):
    fake(answer)
    h = http()
    with pytest.raises((providers.ProviderError, urllib.error.URLError)):
        call(h, "embed")
    assert h.last_key_slot() == slot


def test_one_key_inside_the_other_is_masked_whole(monkeypatch):
    """E5-N2: the first key is a part of the second — the longer one is masked first, no tail of it is left."""
    tail = "-zq" + "x7tail"
    k2, seen = K1 + tail, []

    def urlopen(req, timeout=None):
        seen.append(req.get_header("Authorization"))
        raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(f"bad {K1} and {k2}".encode()))
    monkeypatch.setattr(providers.urllib.request, "urlopen", urlopen)
    for status, sent in ((500, 1), (401, 3)):   # key 1 masked with key 2 as the other (no switch) · both refused
        with pytest.raises(providers.ProviderError) as e:
            call(http({**ENV, "OPENAI_API_KEY_2": k2}), "chat")
        assert len(seen) == sent and tail not in str(e.value) and K1 not in str(e.value)


@pytest.mark.parametrize("ch", ["\n", "\r", "\t", "\x00", "\x1f", "\x7f"], ids=["lf", "cr", "tab", "nul", "us", "del"])
def test_every_control_character_in_the_second_key_stops_it(fake, ch):
    """E5-N3: 0 ~ 31 and 127, not only line breaks."""
    f = fake({"1": 401})
    with pytest.raises(providers.ProviderError):
        call(http({**ENV, "OPENAI_API_KEY_2": "fake-two" + ch + "x"}), "chat")
    assert [w for w, _ in f.seen] == ["1"]


def test_the_second_key_is_read_from_the_environment_before_the_file(fake, tmp_path):
    """E5-N3: environment first, then PACKRT_ENV_FILE — and an empty environment value falls through to the file."""
    envf = tmp_path / "pack.env"
    envf.write_text("OPENAI_API_KEY_2=fake-key-from-the-file-000\n", encoding="utf-8")
    seen = []
    for env2 in (K2, ""):
        f = fake({"1": 401})
        call(http({**ENV, "OPENAI_API_KEY_2": env2, "PACKRT_ENV_FILE": str(envf)}), "chat")
        seen.append(f.seen[1][0])
    assert seen == ["2", "Bearer fake-key-from-the-file-000"]


def test_a_one_key_provider_sends_the_same_headers(monkeypatch):
    """E5-N4 · PRES-F01: a provider without env_fallback sends exactly the 0.12.2 headers."""
    got = []

    def urlopen(req, timeout=None):
        got.append(sorted(req.header_items()))
        return Answer(json.dumps({"choices": [{"message": {"content": "\"ok\""}}], "usage": {}}).encode())
    monkeypatch.setattr(providers.urllib.request, "urlopen", urlopen)
    call(http(conf={k: v for k, v in CONF.items() if k != "env_fallback"}), "chat")
    call(http(), "chat")
    one = [("Authorization", f"Bearer {K1}"), ("Content-type", "application/json")]
    assert got == [one, one]


def test_runs_with_a_context_each_start_on_the_first_key(fake, tmp_path):
    """E5-N4: deploy and LLMOps always pass a context — start_run is called then too."""
    f = fake({"1": 429})
    rt = Runtime(chat_pack(tmp_path, "chat"))
    rt.provider = providers.HttpProvider({"providers": {"openai": CONF}}, ENV)
    assert rt.run(TICKET, context=dict(CTX), review={"policy": "auto_approve"})["status"] == "succeeded"
    f.answer = {}
    assert rt.run(TICKET, context=dict(CTX), review={"policy": "auto_approve"})["status"] == "succeeded"
    assert [w for w, _ in f.seen] == ["1", "2", "1"]


def test_a_real_retrieve1_query_embedding_records_the_slot(fake, tmp_path):
    """E5-N4: retrieve@1 with a real query embedding through a provider that names a second key."""
    fix = Path(__file__).resolve().parent / "fixtures" / "retrieve_min"
    work, roots = tmp_path / "p", tmp_path / "releases"
    shutil.copytree(fix, work, ignore=shutil.ignore_patterns("releases", "expected-*.json"))
    shutil.copytree(fix / "releases", roots)
    man = roots / "indexes" / "ir-docs-v1" / "manifest.json"
    m = json.loads(man.read_text(encoding="utf-8"))
    m["embedding"]["mode"] = "real"
    man.write_text(json.dumps(m, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    (work / "models.yaml").write_text("providers:\n  openai: {env: OPENAI_API_KEY, env_fallback: OPENAI_API_KEY_2, base_url_env: OPENAI_BASE_URL, "
                                      "models: {embedding: [text-embedding-3-small]}}\n", encoding="utf-8")
    (work / "execution-config.yaml").write_text(
        "config_version: 1\nentrypoint: probe\n"
        f'environment: {{kind: local-python, python: "{sys.executable}"}}\nrelease_roots: ["{roots}"]\n'
        "stores:\n  docs: {backend: flat-local, release: ir-docs-v1, embedding: {mode: real, key_env: OPENAI_API_KEY}}\n"
        "  rows: {backend: sqlite-file, release: tr-rows-v1}\nmodels: {}\n", encoding="utf-8")
    fake({"1": 401})
    rec = Recorder()
    res = Runtime(work, env=ENV).run({"text": "공구 마모 점검 전에 전원을 내린다", "group": "TW", "groups": ["TW", "PW"], "rare": "XX"},
                                     context=dict(CTX), sink=rec, review={"policy": "auto_approve"})
    assert res["status"] == "succeeded", res
    slots = [e["body"]["meta"].get("key_slot") for e in rec.events if e["type"] == "node_ended" and e["body"]["meta"].get("kind") == "retrieve@1"
             and e["body"]["meta"].get("embed_tokens") is not None]
    assert slots and set(slots) == {2}
