import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deploy_server.app import Deployment, create_app
from deploy_server.settings import load_settings
from fake_writer import FakeWriter

ROOT = Path(__file__).resolve().parents[1]
TASK10 = ROOT.parent / "task10-pack"
V0_REVISION = "sha256:877b0ed5e60724bc0b0b82fc77925be4f8240ba2a9249aef6090c42689ce60fa"

if not (TASK10 / ".git").exists():
    pytest.skip(f"task10-pack 저장소가 없습니다: {TASK10}", allow_module_level=True)


@pytest.fixture(scope="session")
def packs_dir(tmp_path_factory):
    """Pack 사본은 commit마다 한 번만 꺼낸다(시험 사이에 나눠 씀)."""
    return tmp_path_factory.mktemp("packs")


@pytest.fixture
def make_deployment(tmp_path, packs_dir):
    """기본 기록기는 DB 없는 FakeWriter(가림은 서버 설정의 비밀 값 환경 변수로). logDB 시험은 tests/test_logdb.py."""
    def make(data_dir=None, trace_writer=None, provider=None, config="dev-local", **overrides):
        s = load_settings(ROOT / "config" / f"{config}.yaml",
                          overrides={"data_dir": str(data_dir or tmp_path / "data"), "packs_dir": str(packs_dir), **overrides})
        if trace_writer is None:
            trace_writer = FakeWriter(s.data_dir / "trace", secrets=lambda: [os.environ[n] for n in s.secret_env_names if os.environ.get(n)])
        return Deployment(s, trace_writer=trace_writer, provider=provider)
    return make


@pytest.fixture
def client_for():
    def make(dep):
        return TestClient(create_app(deployment=dep), raise_server_exceptions=False)
    return make


@pytest.fixture
def cases(make_deployment):
    dep = make_deployment()
    rows = [json.loads(l) for l in (dep.copy.pack_dir / "evals" / "task10-holdout" / "dataset.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return {r["case_id"]: r["input"] for r in rows}
