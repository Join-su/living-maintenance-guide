"""D-403 덧(답장 183-01 (가)): provider fake 배포에서 질의 임베딩만 실제 — 설정 `embedding: {provider: http, env_file, base_url}`.
키는 packrt HttpProvider가 PACKRT_ENV_FILE 파일에서 메모리로만 읽고(배포단은 경로만 넘김), 주소는 설정에서. chat은 가짜 그대로.
실제 호출 0 — 임베딩은 127.0.0.1 가짜 HTTP 서버(1536차원)로만."""
import http.server
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import ROOT
from deploy_server.execution import ExecutionError, check_execution
from deploy_server.fake_provider import FakeProvider
from deploy_server.settings import SettingsError, load_settings

KEY = "sk-test-embed-0001-not-real"
MODELS = {"providers": {"openai": {"env": "OPENAI_API_KEY", "env_fallback": "OPENAI_API_KEY_2", "base_url_env": "OPENAI_BASE_URL",
                                   "models": {"chat": ["gpt-4.1-mini-2025-04-14"], "embedding": ["text-embedding-3-small"]}}}}
REAL = {"entrypoint": "run", "models": {}, "stores": {"history": {"backend": "flat-local", "release": "ir-x", "embedding": {"mode": "real", "key_env": "OPENAI_API_KEY"}}}}


def _cfg(tmp_path, embedding=None, provider="fake", key_file=True):
    doc = yaml.safe_load((ROOT / "config" / "c1a15-dev-local.yaml").read_text())
    for k in ("embedding", "release_roots_extra"):   # 맥 설정 v7(184-01)의 실제 칸은 빼고 시작 — 시험마다 넣음
        doc.pop(k, None)
    doc["execution"]["stores"] = {}
    doc["pack"]["repo"] = str(ROOT.parent / "c1a15-pack")
    doc["data_dir"], doc["packs_dir"] = str(tmp_path / "data"), str(tmp_path / "packs")
    doc["provider"] = provider
    doc["registration"] = {"mode": "none"}
    doc["runtime_db"] = {"env_file": str(tmp_path / "no.env")}
    doc["trace"]["env_file"] = str(tmp_path / "no.env")
    keyf = tmp_path / "keys.env"
    if key_file:
        keyf.write_text(f"OPENAI_API_KEY={KEY}\n\nOPENAI_API_KEY_2=sk-second\n")
        keyf.chmod(0o600)
    if embedding is not None:
        doc["embedding"] = embedding
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True))
    return p, keyf


def test_settings_embedding_off_by_default_and_parsed_when_given(tmp_path):
    p, _ = _cfg(tmp_path)
    assert load_settings(p).embedding is None                                         # 칸 없음 = 지금과 같음
    p, keyf = _cfg(tmp_path, {"provider": "http", "env_file": "keys.env", "base_url": "https://api.openai.com/v1/"})
    s = load_settings(p)
    assert s.embedding == {"provider": "http", "env_file": keyf.resolve(), "base_url": "https://api.openai.com/v1"}   # 설정 기준 상대 경로 → 절대


@pytest.mark.parametrize("emb, provider, key_file, says", [
    ({"provider": "http", "env_file": "keys.env"}, "fake", True, "셋을 받습니다"),
    ({"provider": "real", "env_file": "keys.env", "base_url": "https://x"}, "fake", True, "http만"),
    ({"provider": "http", "env_file": "keys.env", "base_url": "https://x"}, "http", True, "provider: fake 배포에서만"),
    ({"provider": "http", "env_file": "keys.env", "base_url": "http://example.com"}, "fake", True, "https://"),
])
def test_settings_embedding_refusals_say_no_path_or_value(tmp_path, emb, provider, key_file, says):
    p, keyf = _cfg(tmp_path, emb, provider=provider, key_file=key_file)
    with pytest.raises(SettingsError) as e:
        load_settings(p)
    assert says in str(e.value) and str(keyf) not in str(e.value) and KEY not in str(e.value)


def test_check_execution_real_store_needs_the_embedding_field_under_fake_provider():
    with pytest.raises(ExecutionError) as e:
        check_execution(REAL, provider="fake")                                         # 지금처럼 거절
    assert "embedding 칸" in str(e.value)
    assert check_execution(REAL, provider="fake", embedding={"provider": "http"}) is False   # 칸이 있으면 허용(D-403 덧)
    assert check_execution(REAL, provider="http") is False


class _Emb(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        _Emb.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "model": body.get("model"), "n": len(body.get("input") or [])})
        out = {"data": [{"index": i, "embedding": [0.001 * (i + 1)] * 1536} for i, _ in enumerate(body["input"])], "usage": {"prompt_tokens": 3}}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(out).encode())


@pytest.fixture
def emb_server():
    _Emb.seen = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Emb)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def _deployment(tmp_path, base_url, key_file=True):
    from deploy_server.app import Deployment
    keyf = tmp_path / "keys.env"
    if key_file:
        keyf.write_text(f"OPENAI_API_KEY={KEY}\n")
    else:
        keyf.write_text("OTHER=1\n")
    d = Deployment.__new__(Deployment)
    d.settings = SimpleNamespace(secret_env_names=["OPENAI_API_KEY"], embedding={"provider": "http", "env_file": keyf, "base_url": base_url},
                                 provider="fake")
    d._runtime_dsn = d._ledger_dsn = None
    rt = SimpleNamespace(models=MODELS, provider=FakeProvider())
    return d, rt, keyf


def test_embed_goes_to_http_with_the_file_key_and_chat_stays_fake(tmp_path, emb_server, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    d, rt, keyf = _deployment(tmp_path, emb_server)
    d._attach_embedder(rt)
    assert set(rt.provider.embedder.env) == {"PACKRT_ENV_FILE", "OPENAI_BASE_URL"}         # 프로세스 env를 넘기지 않음 — 경로 · 주소 둘뿐
    vec = rt.provider.embed("openai", "text-embedding-3-small", ["질의 하나"])
    assert len(vec) == 1 and len(vec[0]) == 1536
    assert _Emb.seen == [{"path": "/v1/embeddings", "auth": f"Bearer {KEY}", "model": "text-embedding-3-small", "n": 1}]   # 키는 머리로만
    out, _ = rt.provider.chat("openai", "gpt-4.1-mini-2025-04-14", [{"role": "user", "content": "보고서"}])
    assert len(_Emb.seen) == 1 and [c[0] for c in rt.provider.calls] == ["embed", "chat"]   # chat은 가짜(HTTP 0)
    assert not hasattr(rt.provider, "last_response") or rt.provider.last_response() is None   # 임베딩 응답을 provider_raw로 넘기지 않음
    d.runner = SimpleNamespace(rt=rt)
    assert KEY in d.secrets()                                                              # 가림에 그 키 이름의 값만(메모리)
    assert "sk-second" not in d.secrets()
    d.provider = rt.provider
    assert d.provider_info()["embedding"] == "http" and d.provider_info()["mode"] == "fake"   # /info: 값 · 경로 0


def test_missing_key_name_in_the_file_refuses_at_start_without_path_or_value(tmp_path, emb_server):
    d, rt, keyf = _deployment(tmp_path, emb_server, key_file=False)
    with pytest.raises(RuntimeError) as e:
        d._attach_embedder(rt)
    assert "OPENAI_API_KEY가 없습니다" in str(e.value) and str(keyf) not in str(e.value)
    assert rt.provider.embedder is None and _Emb.seen == []


def test_secrets_tolerate_no_embedder_yet(tmp_path):
    d, rt, _ = _deployment(tmp_path, "https://api.openai.com/v1")
    assert KEY not in d.secrets()                                                          # runner 전(오류 처리 길) — None을 견딤


def test_dev_local_v7_config_shape_when_switched(tmp_path):
    """E-3 모양(리드 등록 뒤 같은 창): deploy-real store + embedding 칸이 설정 검사를 지남."""
    p, keyf = _cfg(tmp_path, {"provider": "http", "env_file": "keys.env", "base_url": "https://api.openai.com/v1"})
    doc = yaml.safe_load(p.read_text())
    doc["execution"]["stores"] = {"history": {"backend": "flat-local", "release": "ir-history-16d297d8", "embedding": {"mode": "real", "key_env": "OPENAI_API_KEY"}},
                                  "manuals": {"backend": "flat-local", "release": "ir-manuals-3ae4dcdf", "embedding": {"mode": "real", "key_env": "OPENAI_API_KEY"}}}
    p.write_text(yaml.safe_dump(doc, allow_unicode=True))
    s = load_settings(p)
    assert s.embedding and s.provider == "fake" and s.uses_postgres is False


V7_REV = "sha256:8322a1048f776f1158ef6b00cf04acfb1d3bbe1de6af82c45fb13466b8866e77"
INGEST = ROOT.parent / "agent-pack-runtime-ingest" / "releases-c1a15"


def test_server_v7_deploy_real_start_run_embeds_over_http_only(tmp_path, emb_server, packs_dir, monkeypatch):
    """끝-끝(실제 호출 0): c1a15-pack v7 + deploy-real(실제 DB 1536차원) · provider fake · embedding 칸 → start Run 201 ·
    질의 임베딩 = 127.0.0.1 가짜로만(키 = 파일) · chat 가짜 · 응답에 키 0. Pack custom 모듈이 이 시험 프로세스에 남지 않게 자식 프로세스에서."""
    import subprocess
    import sys
    if subprocess.run(["git", "-C", str(ROOT.parent / "c1a15-pack"), "rev-parse", "--verify", "--quiet", "v7^{commit}"], capture_output=True).returncode:
        pytest.skip("c1a15-pack 태그 v7이 없습니다")
    if not (INGEST / "indexes" / "ir-history-16d297d8").is_dir():
        pytest.skip("실제 DB 폴더가 없습니다")
    keyf = tmp_path / "keys.env"
    keyf.write_text(f"OPENAI_API_KEY={KEY}\n")
    real = {"mode": "real", "key_env": "OPENAI_API_KEY"}
    over = {"data_dir": str(tmp_path / "data"), "packs_dir": str(packs_dir), "registration": {"mode": "none"},
            "pack": {"repo": str(ROOT.parent / "c1a15-pack"), "ref": "v7", "pack_subdir": "pack", "releases_subdir": "releases", "expect_revision": V7_REV},
            "execution": {"entrypoint": "run", "models": {"failure_classifier": {"release": "mr-ai4i-lgbm-large-v1"}},
                          "stores": {"history": {"backend": "flat-local", "release": "ir-history-16d297d8", "embedding": real},
                                     "manuals": {"backend": "flat-local", "release": "ir-manuals-3ae4dcdf", "embedding": real}}},
            "release_roots_extra": [str(tmp_path / "rel"), str(INGEST)],
            "embedding": {"provider": "http", "env_file": str(keyf), "base_url": emb_server}}
    script = f"""
import json, subprocess, sys
sys.path[:0] = [{str(ROOT)!r}, {str(ROOT / 'tests')!r}]
from fastapi.testclient import TestClient
from deploy_server.app import Deployment, create_app
from deploy_server.settings import load_settings
from fake_writer import FakeWriter
s = load_settings({str(ROOT / 'config' / 'c1a15-dev-local.yaml')!r}, overrides=json.loads({json.dumps(over)!r}))
dep = Deployment(s, trace_writer=FakeWriter(s.data_dir / "trace"))
body = json.loads(subprocess.run(["git", "-C", {str(ROOT.parent / 'c1a15-pack')!r}, "show", "v7:contracts/v4/examples/start.request.json"],
                                 capture_output=True, text=True, check=True).stdout)
r = TestClient(create_app(deployment=dep), raise_server_exceptions=False).post("/runs", content=json.dumps(body).encode(), headers={{"Idempotency-Key": "embed-only-1"}})
print(json.dumps({{"code": r.status_code, "embeds": [c[0] for c in dep.provider.calls].count("embed"), "info": dep.provider_info(), "key_in_body": {KEY!r} in r.text}}))
"""
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("OPENAI_API_KEY", "OPENAI_BASE_URL")}
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["code"] == 201 and out["info"]["embedding"] == "http" and out["key_in_body"] is False, out
    assert _Emb.seen and all(s["path"] == "/v1/embeddings" and s["auth"] == f"Bearer {KEY}" and s["model"] == "text-embedding-3-small" for s in _Emb.seen)
    assert len(_Emb.seen) == out["embeds"] <= 2                                            # Run당 질의 임베딩 ≤ 2(history · manuals)


def test_missing_key_file_refuses_at_start_not_at_settings(tmp_path, emb_server):
    """D-416 ③: 설정 읽기는 키 파일 없이도 됨(deploy-tools 묶기 대조 — V-40, 키를 붙이지 않음) · 서버 시작이 거절(경로 · 값 글 0)."""
    p, keyf = _cfg(tmp_path, {"provider": "http", "env_file": "keys.env", "base_url": "https://api.openai.com/v1"}, key_file=False)
    assert load_settings(p).embedding["env_file"] == keyf.resolve() and not keyf.exists()
    (tmp_path / "d").mkdir()
    d, rt, keyf2 = _deployment(tmp_path / "d", emb_server, key_file=True)
    keyf2.unlink()
    with pytest.raises(RuntimeError) as e:
        d._attach_embedder(rt)
    assert "키 파일이 없습니다" in str(e.value) and str(keyf2) not in str(e.value) and rt.provider.embedder is None

