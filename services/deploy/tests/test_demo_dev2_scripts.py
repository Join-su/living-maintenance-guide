"""시연 대본 demo/dev2(D-586) — 저장소에 올라가는 꼴: 절대 경로 · 키 0 · 셸 문법 · 시연 설정이 읽히고(가짜 + 질의 임베딩 칸) provider 바꿈 규칙이 설정 규칙과 맞음."""
import re
import subprocess
from pathlib import Path

import pytest

from deploy_server.settings import SettingsError, load_settings

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "demo" / "dev2"
CFG = ROOT / "config" / "c1a15-dev2-demo.yaml"


@pytest.mark.parametrize("f", sorted(p.name for p in D.iterdir() if p.is_file()))
def test_no_absolute_home_path_or_secret(f):
    t = (D / f).read_text(encoding="utf-8")
    assert "/Users/" not in t and not re.search(r"sk-[A-Za-z0-9]{20}|postgres(ql)?://[^ ]*:[^ ]*@", t)


@pytest.mark.parametrize("f", sorted(p.name for p in D.glob("*.sh")))
def test_shell_syntax(f):
    assert subprocess.run(["bash", "-n", str(D / f)]).returncode == 0


def test_demo_config_is_fake_with_query_embedding_and_relative_paths():
    t = CFG.read_text(encoding="utf-8")
    assert "/Users/" not in t and re.search(r"^provider: fake", t, re.M) and "\nembedding:" in t
    s = load_settings(CFG)
    assert s.provider == "fake" and s.embedding["provider"] == "http" and s.pack_ref == "d580-g1"


def test_http_without_embedding_block_is_the_rule(tmp_path):   # provider.sh http가 embedding 칸을 빼는 까닭
    t = CFG.read_text(encoding="utf-8")
    bad = tmp_path / "c.yaml"
    bad.write_text(re.sub(r"^provider: fake", "provider: http", t, flags=re.M))
    (tmp_path / "x").mkdir()
    with pytest.raises(SettingsError, match="embedding"):
        load_settings(bad)


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_env_finds_its_place_when_sourced_from_bash_and_zsh(shell, tmp_path):   # 감시를 켜는 셸이 zsh일 때 BASH_SOURCE가 비는 것(11-06 B)
    r = subprocess.run([shell, "-c", f'cd {tmp_path} && . "{D}/env.sh" && echo "$DEMO_W|$DEPLOY_VENV"'], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    w, venv = r.stdout.strip().split("|")
    assert Path(w) == ROOT and venv.endswith("/llmopsagent-deploy/.venv") and not venv.startswith("/llmopsagent-deploy")
