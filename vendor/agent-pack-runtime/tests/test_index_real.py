"""EV-006 S4b · V-REAL-FAKE (CHG-R08, contract C · D-250 (5)): the real-embedding path of `packrt.index build` against a fake provider —
urllib.request.urlopen is replaced, no socket is opened and no real provider is called. The store's provider · model through the
Pack's models.yaml, the key from the variable --key-env names, batches, calls and tokens (usage.prompt_tokens) for the LLMOps cap, the
vector size from the answer, `exists` without asking again, a provider error as its type only, and the key in no output."""
import io
import json
import os
import subprocess
import sys
import urllib.error

import pytest
from test_index_build import corpus, pack

from packrt.index import real
from packrt.index.build import build
from packrt.index.errors import IndexBuildError
from packrt.index.real import real_embedder
from packrt.run import providers

KEY = "fake-embedding-key-0123456789abcdef"
ENV = {"MY_EMB_KEY": KEY, "OPENAI_BASE_URL": "http://127.0.0.1:9/v1"}
MODELS = "providers:\n  openai: {env: OPENAI_API_KEY, base_url_env: OPENAI_BASE_URL, models: {embedding: [text-embedding-3-small]}}\n"
DIM = 8


def with_models(tmp_path, **kw):
    p = pack(tmp_path, **kw)
    (p / "models.yaml").write_text(MODELS, encoding="utf-8")
    return p


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def provider(monkeypatch):
    seen = []

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data)
        seen.append({"url": req.full_url, "auth": req.get_header("Authorization"), "input": body["input"], "model": body["model"]})
        data = [{"index": i, "embedding": [round((len(t) % 7 + j) / 10, 3) for j in range(DIM)]} for i, t in enumerate(body["input"])]
        return Answer(json.dumps({"data": data, "usage": {"prompt_tokens": 5 * len(body["input"]), "total_tokens": 5 * len(body["input"])}}).encode())
    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    return seen


def real_build(p, out, node="find_manuals", corp="mc-h0-manuals-v1", env=ENV):
    embedder, dim = real_embedder(p, "start", node, "MY_EMB_KEY", env=env)
    return build(p, "start", node, corpus(corp), out, embedding="real", embedder=embedder, model_dim=dim)


def test_real_build_batches_counts_and_takes_the_size_from_the_answer(tmp_path, provider, monkeypatch):
    monkeypatch.setattr(real, "BATCH", 3)
    out = tmp_path / "out"
    r = real_build(with_models(tmp_path, manual_index={"chunk_size": 40, "overlap": 8}), out)
    assert (r["status"], r["chunks"], r["embed_calls"], r["embed_tokens"]) == ("built", 7, 3, 35)   # 7 pieces in batches of 3
    assert [len(c["input"]) for c in provider] == [3, 3, 1]
    assert {c["url"] for c in provider} == {"http://127.0.0.1:9/v1/embeddings"} and {c["auth"] for c in provider} == {f"Bearer {KEY}"}
    assert {c["model"] for c in provider} == {"text-embedding-3-small"}
    m = json.loads((out / r["release_id"] / "manifest.json").read_text(encoding="utf-8"))
    assert m["embedding"] == {"provider": "openai", "model": "text-embedding-3-small", "dim": DIM, "mode": "real"}
    assert r["release_id"].startswith("ir-manuals-") and r["release_id"] != "ir-manuals-7dd35828"   # another embedding: another version
    for f in (out / r["release_id"]).iterdir():
        assert KEY not in f.read_text(encoding="utf-8")
    assert KEY not in json.dumps(r)


def test_the_same_real_build_again_is_exists_without_asking_the_provider(tmp_path, provider):
    out, p = tmp_path / "out", with_models(tmp_path)
    first = real_build(p, out)
    n = len(provider)
    again = real_build(p, out)
    assert (again["status"], again["release_id"], again["embed_calls"], again["embed_tokens"]) == ("exists", first["release_id"], 0, 0)
    assert len(provider) == n


def test_a_fake_version_is_not_taken_for_a_real_one(tmp_path, provider):
    out, p = tmp_path / "out", with_models(tmp_path)
    build(p, "start", "find_manuals", corpus("mc-h0-manuals-v1"), out)   # fake
    r = real_build(p, out)
    assert r["status"] == "built" and len(provider) == 1 and len(list(out.iterdir())) == 2


def test_a_provider_error_is_embedding_failed_as_its_type_only(tmp_path, monkeypatch):
    def refuse(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "err", {}, io.BytesIO(f'{{"error": "bad key {KEY}"}}'.encode()))
    monkeypatch.setattr(providers.urllib.request, "urlopen", refuse)
    out = tmp_path / "out"
    with pytest.raises(IndexBuildError) as e:
        real_build(with_models(tmp_path), out)
    assert e.value.code == "embedding_failed" and str(e.value) == "임베딩 실패: ProviderError"
    assert not out.exists() or list(out.iterdir()) == []


def test_a_wrong_answer_size_is_embedding_failed(tmp_path, monkeypatch):
    def short(req, timeout=None):
        body = json.loads(req.data)
        return Answer(json.dumps({"data": [{"index": 0, "embedding": [0.1] * DIM}], "usage": {"prompt_tokens": 1}}).encode())
    monkeypatch.setattr(providers.urllib.request, "urlopen", short)
    with pytest.raises(IndexBuildError) as e:
        real_build(with_models(tmp_path), tmp_path / "out")
    assert e.value.code == "embedding_failed"


def test_no_key_or_no_models_file_stops_before_any_request(tmp_path, monkeypatch):
    monkeypatch.setattr(providers.urllib.request, "urlopen", lambda *a, **k: pytest.fail("a request was made"))
    with pytest.raises(IndexBuildError, match="^키 환경 변수 MY_EMB_KEY가 없습니다$") as e:
        real_embedder(with_models(tmp_path), "start", "find_manuals", "MY_EMB_KEY", env={"OPENAI_BASE_URL": "http://127.0.0.1:9/v1"})
    assert e.value.code == "embedding_failed"
    with pytest.raises(IndexBuildError, match="models.yaml") as e:
        real_embedder(pack(tmp_path / "b"), "start", "find_manuals", "MY_EMB_KEY", env=ENV)
    assert e.value.code == "embedding_failed"


def test_cli_real_needs_key_env_and_reports_a_missing_key(tmp_path):
    p, out = with_models(tmp_path), tmp_path / "out"
    base = [sys.executable, "-B", "-m", "packrt.index", "build", "--pack", str(p), "--graph", "start", "--node", "find_manuals",
            "--corpus", str(corpus("mc-h0-manuals-v1")), "--out", str(out), "--embedding", "real", "--json"]
    env = {k: v for k, v in os.environ.items() if k not in ("MY_EMB_KEY", "OPENAI_BASE_URL")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    usage = subprocess.run(base, capture_output=True, text=True, env=env, cwd=tmp_path)
    assert usage.returncode == 2 and usage.stdout == ""
    missing = subprocess.run(base + ["--key-env", "MY_EMB_KEY"], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert missing.returncode == 1, missing.stderr
    assert json.loads(missing.stdout)["error"] == {"code": "embedding_failed", "message": "키 환경 변수 MY_EMB_KEY가 없습니다"}
    assert not out.exists()


# ---------------------------------------------------------------- EV-011 (D-342 (2) · D-345): the key from PACKRT_ENV_FILE as well
FILE_KEY = "fake-file-key-fedcba9876543210"
BASE = "OPENAI_BASE_URL=http://127.0.0.1:9/v1\n"


def env_file(tmp_path, text, name="pack.env"):
    f = tmp_path / name
    f.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    return f


def no_request(monkeypatch):
    monkeypatch.setattr(providers.urllib.request, "urlopen", lambda *a, **k: pytest.fail("a request was made"))


def release_files(folder):
    return {f.name: f.read_bytes() for f in sorted(folder.iterdir())}


def cli(p, out, *extra, embedding="real"):
    return ["build", "--pack", str(p), "--graph", "start", "--node", "find_manuals", "--corpus",
            str(corpus("mc-h0-manuals-v1")), "--out", str(out), "--embedding", embedding, *extra, "--json"]


def test_a_key_only_in_the_env_file_is_found_as_the_runner_finds_it(tmp_path, provider):
    """CHG-K01 · K03: no key in the environment, the key and the address in the file PACKRT_ENV_FILE names — built, and the key goes
    only into the request header, never into the result or the version's files."""
    f = env_file(tmp_path, f"MY_EMB_KEY={FILE_KEY}\n{BASE}")
    out = tmp_path / "out"
    r = real_build(with_models(tmp_path), out, env={"PACKRT_ENV_FILE": str(f)})
    assert r["status"] == "built" and r["embed_calls"] == 1
    assert {c["auth"] for c in provider} == {f"Bearer {FILE_KEY}"}
    assert FILE_KEY not in json.dumps(r)
    for b in release_files(out / r["release_id"]).values():
        assert FILE_KEY.encode() not in b


@pytest.mark.parametrize("env_value, sent", [(KEY, KEY), ("", FILE_KEY)], ids=["key in env", "empty in env"])
def test_the_environment_comes_before_the_env_file(tmp_path, provider, env_value, sent):
    """CHG-K01 · PRES-K01: a key in the environment is used even when the file holds another; an empty one falls to the file (the
    rule of HttpProvider._conf)."""
    f = env_file(tmp_path, f"MY_EMB_KEY={FILE_KEY}\n")
    real_build(with_models(tmp_path), tmp_path / "out", env={**ENV, "MY_EMB_KEY": env_value, "PACKRT_ENV_FILE": str(f)})
    assert {c["auth"] for c in provider} == {f"Bearer {sent}"}


def test_a_key_in_the_environment_needs_no_env_file(tmp_path, provider):
    """CHG-K02 · PRES-K01: with the key in the environment a PACKRT_ENV_FILE that cannot be read is no error (as in the runner)."""
    r = real_build(with_models(tmp_path), tmp_path / "out", env={**ENV, "PACKRT_ENV_FILE": str(tmp_path / "no-such.env")})
    assert r["status"] == "built" and {c["auth"] for c in provider} == {f"Bearer {KEY}"}


def unreadable(tmp_path):
    f = env_file(tmp_path, f"MY_EMB_KEY={FILE_KEY}\n", "locked.env")
    f.chmod(0)
    return f


MISSING = [
    ("not given", None, "키 환경 변수 MY_EMB_KEY가 없습니다"),
    ("empty name", "", "키 환경 변수 MY_EMB_KEY가 없습니다"),
    ("no such file", lambda t: t / "no-such.env", "키 환경 변수 MY_EMB_KEY가 없고, PACKRT_ENV_FILE 파일을 읽을 수 없습니다"),
    ("a folder", lambda t: t, "키 환경 변수 MY_EMB_KEY가 없고, PACKRT_ENV_FILE 파일을 읽을 수 없습니다"),
    ("no permission", unreadable, "키 환경 변수 MY_EMB_KEY가 없고, PACKRT_ENV_FILE 파일을 읽을 수 없습니다"),
    ("not in the file", lambda t: env_file(t, f"OTHER_SECRET={FILE_KEY}\n{BASE}"), "키 환경 변수 MY_EMB_KEY가 환경에도 PACKRT_ENV_FILE 파일에도 없습니다"),
    ("empty in the file", lambda t: env_file(t, "MY_EMB_KEY=\n"), "키 환경 변수 MY_EMB_KEY가 환경에도 PACKRT_ENV_FILE 파일에도 없습니다")]


@pytest.mark.parametrize("case, where, text", MISSING, ids=[m[0] for m in MISSING])
def test_a_missing_key_says_where_it_was_looked_for_by_name_only(tmp_path, monkeypatch, case, where, text):
    """CHG-K02 · K03: before any request, embedding_failed, the exact text — no path, no line or value of the file."""
    if case == "no permission" and os.geteuid() == 0:
        pytest.skip("root reads a file without permission bits")
    no_request(monkeypatch)
    env = {"OPENAI_BASE_URL": "http://127.0.0.1:9/v1"}
    if where is not None:
        env["PACKRT_ENV_FILE"] = where if isinstance(where, str) else str(where(tmp_path))
    with pytest.raises(IndexBuildError) as e:
        real_embedder(with_models(tmp_path), "start", "find_manuals", "MY_EMB_KEY", env=env)
    assert (e.value.code, str(e.value)) == ("embedding_failed", text)
    assert str(tmp_path) not in str(e.value) and FILE_KEY not in str(e.value)


def test_the_cli_builds_with_the_key_in_the_env_file(tmp_path, provider, monkeypatch, capsys):
    """CHG-K01 · K03 through `python -m packrt.index build` — main() in this process, so the fake provider answers (no socket)."""
    from packrt.index.__main__ import main
    p = with_models(tmp_path)
    for name in ("MY_EMB_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PACKRT_ENV_FILE", str(env_file(tmp_path, f"MY_EMB_KEY={FILE_KEY}\n{BASE}")))
    assert main(cli(p, tmp_path / "out", "--key-env", "MY_EMB_KEY")) == 0
    got = capsys.readouterr()
    assert json.loads(got.out)["status"] == "built" and {c["auth"] for c in provider} == {f"Bearer {FILE_KEY}"}
    assert FILE_KEY not in got.out + got.err


@pytest.mark.parametrize("key_in_env", [False, True])
def test_an_env_file_that_is_not_utf8_is_a_json_failure(tmp_path, monkeypatch, capsys, key_in_env):
    """CHG-K02 4 (D-345 (3), the one named exception to PRES-K01): 0.12.1 ended in a traceback here when the key was in the
    environment; now exit 1 with the JSON error, whether or not the key is in the environment. No request."""
    from packrt.index.__main__ import main
    no_request(monkeypatch)
    p = with_models(tmp_path)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("PACKRT_ENV_FILE", str(env_file(tmp_path, b"MY_EMB_KEY=\xff\xfe\n")))
    if key_in_env:
        monkeypatch.setenv("MY_EMB_KEY", KEY)
    else:
        monkeypatch.delenv("MY_EMB_KEY", raising=False)
    assert main(cli(p, tmp_path / "out", "--key-env", "MY_EMB_KEY")) == 1
    got = capsys.readouterr()
    assert json.loads(got.out)["error"] == {"code": "embedding_failed", "message": "PACKRT_ENV_FILE 파일을 UTF-8로 읽을 수 없습니다"}
    assert KEY not in got.out + got.err and not (tmp_path / "out").exists()


def test_a_control_character_in_the_env_file_key_stops_before_any_request(tmp_path, monkeypatch):
    """CHG-K03: the EV-001 CHG-108 check holds for a key from the file — a control character in the middle of the value
    (load_env_file splits lines, so a newline cannot be in it) stops the call; the error is its type only."""
    no_request(monkeypatch)
    f = env_file(tmp_path, f"MY_EMB_KEY=abc\x01{FILE_KEY}\n{BASE}")
    with pytest.raises(IndexBuildError) as e:
        real_build(with_models(tmp_path), tmp_path / "out", env={"PACKRT_ENV_FILE": str(f)})
    assert (e.value.code, str(e.value)) == ("embedding_failed", "임베딩 실패: ProviderError")


def test_the_same_version_whether_the_key_comes_from_the_environment_or_the_file(tmp_path, provider):
    """CHG-K04: the key is in no hash — built with the key in the environment, asked again with the key in the file: exists, no call."""
    out, p = tmp_path / "out", with_models(tmp_path)
    first = real_build(p, out)
    n = len(provider)
    again = real_build(p, out, env={"PACKRT_ENV_FILE": str(env_file(tmp_path, f"MY_EMB_KEY={FILE_KEY}\n{BASE}"))})
    assert (again["status"], again["release_id"], again["build_sha256"], again["embed_calls"]) == ("exists", first["release_id"], first["build_sha256"], 0)
    assert len(provider) == n


def test_a_fake_build_does_not_look_at_the_env_file(tmp_path, monkeypatch, capsys):
    """PRES-K02: --embedding fake gives the same answer and the same files with PACKRT_ENV_FILE naming a missing file as without it."""
    from packrt.index.__main__ import main
    no_request(monkeypatch)
    p = pack(tmp_path)
    answers = []
    for name, value in (("a", None), ("b", str(tmp_path / "no-such.env"))):
        if value:
            monkeypatch.setenv("PACKRT_ENV_FILE", value)
        assert main(cli(p, tmp_path / name, embedding="fake")) == 0
        r = json.loads(capsys.readouterr().out)
        answers.append(({k: v for k, v in r.items() if k not in ("seconds", "location")}, release_files(tmp_path / name / r["release_id"])))
    assert answers[0] == answers[1]
