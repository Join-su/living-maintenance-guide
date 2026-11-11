"""실행 설정 받기(지시 006b): 설정 execution이 PostgreSQL backend 칸(connection·embedding)을 받고 검사한다, 실행용 DSN은 하나만·값은 남기지 않는다.
DB·네트워크 없음. PostgreSQL에서 실제로 읽는지는 tests/test_logdb.py(packrt 0.8.0 뒤)."""
import os
from types import SimpleNamespace

import pytest
import yaml

from conftest import ROOT
from deploy_server.execution import RUNTIME_DSN_ENV, ExecutionError, check_execution, dsn_secrets, runtime_dsn, scrub_db_env
from deploy_server.pack_source import write_execution_config
from deploy_server.settings import SettingsError, load_settings

FAKE_DSN = "postgresql://runtime_reader:fake-pass-123@127.0.0.1:1/none"
PG_STORES = {
    "history": {"backend": "postgres-table", "release": "hc-ai4i2020-s7-v2", "connection": {"env": RUNTIME_DSN_ENV}},
    "manuals": {"backend": "pgvector", "release": "ir-manuals-v3-fake", "connection": {"env": RUNTIME_DSN_ENV}, "embedding": {"mode": "fake"}},
}


@pytest.mark.parametrize("name, pg", [("dev-local", False), ("v0-local", False), ("v0-pg-local", True), ("v1-pg-local", True)])
def test_config_files_load(name, pg):
    s = load_settings(ROOT / "config" / f"{name}.yaml")
    assert s.deployment_id == f"task10@{name}" and s.uses_postgres is pg


@pytest.mark.parametrize("name", ["dev-local", "v0-local", "v0-pg-local", "v1-pg-local"])
def test_config_secrets_come_from_own_env(name):
    """서버 비밀 출처는 배포단 자체 .env 하나(EV-004 CHG-01, V-24). llmops db/.env를 직접 가리키지 않는다."""
    s = load_settings(ROOT / "config" / f"{name}.yaml")
    own = (ROOT / ".env").resolve()
    assert s.trace["env_file"] == own
    if s.uses_postgres:
        assert s.runtime_db["env_file"] == own


def test_pg_config_matches_pack_postgres_fake():
    """v0-pg-local의 store는 task10-pack pack/execution/postgres-fake.yaml과 같다(파일은 읽기만)."""
    s = load_settings(ROOT / "config" / "v0-pg-local.yaml")
    pack_cfg = yaml.safe_load((ROOT.parent / "task10-pack" / "pack" / "execution" / "postgres-fake.yaml").read_text(encoding="utf-8"))
    assert s.execution["stores"] == pack_cfg["stores"] == PG_STORES
    assert (s.pack_ref, s.retention_class) == ("v0", "standard")
    assert s.runtime_db["env_file"] == (ROOT / ".env").resolve()   # 배포단 자체 .env(EV-004 CHG-01)


def test_written_execution_config_keeps_pg_fields(tmp_path):
    s = load_settings(ROOT / "config" / "v0-pg-local.yaml")
    copy = SimpleNamespace(releases_dir=tmp_path / "releases")
    text = write_execution_config(tmp_path / "execution-config.yaml", copy, s.execution).read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert doc["stores"] == PG_STORES and doc["entrypoint"] == "respond" and doc["config_version"] == 1
    assert "postgresql://" not in text and "password" not in text.lower()


@pytest.mark.parametrize("stores, provider, msg", [
    ({"h": {"backend": "postgres-table", "release": "r"}}, "fake", "connection: {env: LLMOPSAGENT_RUNTIME_DSN}"),
    ({"h": {"backend": "postgres-table", "release": "r", "connection": {"env": "LLMOPSAGENT_OWNER_DSN"}}}, "fake", "LLMOPSAGENT_RUNTIME_DSN만"),
    ({"h": {"backend": "postgres-table", "release": "r", "connection": {"env": RUNTIME_DSN_ENV, "dsn": "x"}}}, "fake", "connection"),
    ({"h": {"backend": "postgres-table", "release": "r", "connection": {"env": RUNTIME_DSN_ENV}, "embedding": {"mode": "fake"}}}, "fake", "받지 않는 항목"),
    ({"h": {"backend": "sqlite-file", "release": "r", "connection": {"env": RUNTIME_DSN_ENV}}}, "fake", "받지 않는 항목"),
    ({"m": {"backend": "pgvector", "release": "r", "connection": {"env": RUNTIME_DSN_ENV}}}, "fake", "embedding.mode: fake만"),
    ({"m": {"backend": "pgvector", "release": "r", "connection": {"env": RUNTIME_DSN_ENV}, "embedding": {"mode": "real"}}}, "http", "key_env"),
    ({"m": {"backend": "pgvector", "release": "r", "connection": {"env": RUNTIME_DSN_ENV}, "embedding": {"mode": "x"}}}, "fake", "real 또는 fake"),
    ({"m": {"backend": "mysql", "release": "r"}}, "fake", "받지 않습니다"),
    ({"m": {"backend": "pgvector", "connection": {"env": RUNTIME_DSN_ENV}, "embedding": {"mode": "fake"}}}, "fake", "release가 없습니다"),
    ({"h": {"backend": "sqlite-file", "release": FAKE_DSN}}, "fake", "접속 정보 값"),
])
def test_execution_rejects(stores, provider, msg):
    with pytest.raises(ExecutionError) as e:
        check_execution({"entrypoint": "respond", "stores": stores, "models": {}}, provider=provider)
    assert msg in str(e.value) and "fake-pass" not in str(e.value)


def test_execution_accepts_real_embedding_with_http_provider():
    stores = {"m": {**PG_STORES["manuals"], "embedding": {"mode": "real", "key_env": "OPENAI_API_KEY"}}}
    assert check_execution({"entrypoint": "respond", "stores": stores}, provider="http") is True
    assert check_execution({"entrypoint": "respond", "stores": {"h": {"backend": "sqlite-file", "release": "r"}}}) is False


def test_bad_execution_is_settings_error():
    with pytest.raises(SettingsError, match="v0-pg-local.yaml"):
        load_settings(ROOT / "config" / "v0-pg-local.yaml",
                      overrides={"execution": {"entrypoint": "respond", "stores": {"h": {"backend": "postgres-table", "release": "r"}}}})
    with pytest.raises(SettingsError, match="env_file만"):
        load_settings(ROOT / "config" / "v0-pg-local.yaml", overrides={"runtime_db": {"env_file": "x", "dsn_env": "OTHER"}})


def test_runtime_dsn_from_env_file_one_line(tmp_path, monkeypatch):
    monkeypatch.delenv(RUNTIME_DSN_ENV, raising=False)
    f = tmp_path / "db.env"
    f.write_text(f"LLMOPSAGENT_OWNER_DSN=postgresql://db_owner:other@127.0.0.1:1/x\n{RUNTIME_DSN_ENV}='{FAKE_DSN}'\n", encoding="utf-8")
    assert runtime_dsn(f) == FAKE_DSN
    monkeypatch.setenv(RUNTIME_DSN_ENV, "postgresql://runtime_reader:from-env@127.0.0.1:1/none")   # 환경 변수가 먼저
    assert "from-env" in runtime_dsn(f)
    monkeypatch.delenv(RUNTIME_DSN_ENV)
    with pytest.raises(ExecutionError, match=RUNTIME_DSN_ENV):
        runtime_dsn(tmp_path / "missing.env")


def test_dsn_secrets_cover_password():
    assert dsn_secrets(FAKE_DSN) == [FAKE_DSN, "fake-pass-123"]
    assert "p%w" in dsn_secrets("postgresql://u:p%25w@h:1/d")
    assert "s3cret" in dsn_secrets("host=127.0.0.1 user=runtime_reader password=s3cret dbname=x")


def test_scrub_keeps_only_named_db_vars(monkeypatch):
    for n in ("LLMOPSAGENT_OWNER_DSN", "LLMOPSAGENT_SUPERUSER_DSN", "TASK10_LOADER_DSN", "DB_OWNER_PASSWORD", RUNTIME_DSN_ENV, "LLMOPSAGENT_TRACE_DSN"):
        monkeypatch.setenv(n, "fake")
    gone = scrub_db_env(keep={RUNTIME_DSN_ENV, "LLMOPSAGENT_TRACE_DSN"})
    assert gone == ["DB_OWNER_PASSWORD", "LLMOPSAGENT_OWNER_DSN", "LLMOPSAGENT_SUPERUSER_DSN", "TASK10_LOADER_DSN"]
    assert os.environ.get(RUNTIME_DSN_ENV) == "fake" and os.environ.get("LLMOPSAGENT_TRACE_DSN") == "fake"


def test_pg_deployment_without_runtime_dsn_does_not_start(make_deployment, tmp_path, monkeypatch):
    monkeypatch.delenv(RUNTIME_DSN_ENV, raising=False)
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ExecutionError, match=f"'{RUNTIME_DSN_ENV}'이 없습니다"):
        make_deployment(config="v0-pg-local", runtime_db={"env_file": str(empty)})


def test_pg_deployment_load_error_hides_dsn(make_deployment, monkeypatch):
    """Pack을 못 불러와도(지금 packrt면 backend를 모름, 0.8.0이면 닿지 않는 DB) 오류 문구에 DSN·비밀번호가 없다.
    서버 프로세스 환경: 실행용 DSN은 넣고, 다른 DB 비밀은 뺀다."""
    monkeypatch.setenv(RUNTIME_DSN_ENV, FAKE_DSN)
    monkeypatch.setenv("LLMOPSAGENT_OWNER_DSN", "postgresql://db_owner:fake-owner@127.0.0.1:1/x")
    with pytest.raises(RuntimeError) as e:
        make_deployment(config="v0-pg-local")
    assert "Pack을 불러올 수 없습니다" in str(e.value)
    assert "fake-pass-123" not in str(e.value) and "postgresql://" not in str(e.value)
    assert os.environ.get(RUNTIME_DSN_ENV) == FAKE_DSN and "LLMOPSAGENT_OWNER_DSN" not in os.environ
