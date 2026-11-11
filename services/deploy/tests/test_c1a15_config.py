"""갈래 k KS-2(D-265 · D-261 (3)): C1-a15 맥 격리 배포 설정 config/c1a15-v1-local.yaml.
격리 = 공유 DB에 쓰지 않음(기록 env 파일은 var/ 아래 전용 파일, 장부 대조 없음) · store 없음 · Pack 쪽 pack/execution/deploy.yaml(태그 v1)과 같은 값."""
import subprocess

import pytest
import yaml

from deploy_server.execution import check_execution
from deploy_server.settings import load_settings

from conftest import ROOT

CONF = ROOT / "config" / "c1a15-v1-local.yaml"
PACK = ROOT.parent / "c1a15-pack"


def test_c1a15_isolated_config_never_points_at_the_shared_db():
    s = load_settings(CONF)
    assert s.deployment_id == "c1a15@v1-local" and s.pack_ref == "v3" and s.uses_postgres is False
    assert s.registration == {"mode": "none"}
    assert s.trace["env_file"] == (ROOT / "var" / "c1a15-v1-local" / "trace.env").resolve()   # 배포단 .env(공유 DB)가 아님
    assert s.trace["env_file"] != (ROOT / ".env").resolve()
    assert check_execution(s.execution) is False and s.execution["stores"] == {}
    assert s.grounding_keys == []   # D-278: 약속 v2 출력 칸은 result.output으로(state 칸 아님)


@pytest.mark.skipif(not (PACK / ".git").exists(), reason=f"c1a15-pack 저장소가 없습니다: {PACK}")
def test_c1a15_config_matches_the_pack_deploy_yaml():
    out = subprocess.run(["git", "-C", str(PACK), "show", "v3:pack/execution/deploy.yaml"], capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("c1a15-pack 태그 v3가 없습니다")
    pack = yaml.safe_load(out.stdout)
    s = load_settings(CONF)
    assert s.execution["entrypoint"] == pack["entrypoint"]
    assert s.execution["models"] == pack["models"]


def test_c1a15_shared_config_is_the_same_pack_with_the_ledger_check():
    """공유 DB 1건 설정(D-261 (3) · D-280 배포 id c1a15@dev-local): 격리 설정과 같은 Pack · 실행 설정, 장부 대조 llmops_pg, 기록 = 배포단 .env, 원문 = 맥 LLMOps가 읽는 폴더."""
    iso, sh = load_settings(CONF), load_settings(ROOT / "config" / "c1a15-dev-local.yaml")
    assert sh.deployment_id == "c1a15@dev-local" and sh.retention_class == "dev" and sh.registration == {"mode": "llmops_pg"}   # D-280
    assert (sh.execution["models"], sh.grounding_keys) == (iso.execution["models"], iso.grounding_keys)   # 모델 · grounding은 격리와 같음
    # 공유 설정은 llmops 장부 줄(c1a15@dev-local v12 · decision 14 · drs:57c80b829770c0c1, D-442)에 묶여 태그 v12 · deploy-real — 질의 임베딩만 실제(183-01 (가))
    assert (sh.pack_ref, sh.expect_revision) == ("v12", "sha256:4c8c0f3d11a7fb707523c7e725b31fd0243df9df4842d67aa79a99e85a43fbab")
    assert {k: v["release"] for k, v in sh.execution["stores"].items()} == {"history": "ir-history-16d297d8", "manuals": "ir-manuals-3ae4dcdf"}
    assert sh.provider == "fake" and sh.embedding["provider"] == "http" and sh.embedding["base_url"] == "https://api.openai.com/v1"
    assert sh.embedding["env_file"] == (ROOT.parent / "c1a15-pack" / ".env").resolve()   # 키는 경로만 — 배포단 .env 아님
    assert sh.trace["env_file"] == (ROOT / ".env").resolve()
    assert sh.trace["payload_root"] == (ROOT / "var" / "v1-pg-local" / "trace-payloads").resolve()
