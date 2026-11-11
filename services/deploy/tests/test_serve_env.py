"""serve.sh 서비스 env(D-311 · D-315): 배포 서버는 늘 ORT_DISABLE_TELEMETRY=1로 뜸(밖에서 다른 값을 줘도 1)."""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_serve_sets_onnxruntime_telemetry_off(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "serve.sh", tmp_path / "scripts" / "serve.sh")
    py = tmp_path / "scripts" / "py"
    py.write_text('#!/bin/sh\necho "ORT=$ORT_DISABLE_TELEMETRY CONFIG=$DEPLOY_CONFIG"\n')
    py.chmod(0o755)
    env = {**os.environ, "ORT_DISABLE_TELEMETRY": "0", "DEPLOY_CONFIG": "config/x.yaml"}
    out = subprocess.run(["sh", str(tmp_path / "scripts" / "serve.sh")], capture_output=True, text=True, env=env)
    assert out.returncode == 0 and out.stdout.strip() == "ORT=1 CONFIG=config/x.yaml"
