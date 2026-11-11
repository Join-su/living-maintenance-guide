"""import 경계 시험(지시 006 1단계 3, 결정 D-13): 배포단은 llmops(서버·화면 패키지)를 import하지 않는다.
이번 주 배포단은 llmops-lite-v2 `.venv`를 함께 쓰므로 `import llmops`가 실제로 될 수 있다. 이 시험이 경계다.
허용: 표준 라이브러리, 웹 틀(fastapi·starlette·uvicorn), yaml, packrt·공유 기록기(tracewriter)와 그 DB 드라이버(psycopg)."""
import ast
import json
import os
import subprocess
import sys

from conftest import ROOT

FORBIDDEN = "llmops"
ALLOWED_THIRD_PARTY = {"fastapi", "starlette", "uvicorn", "yaml", "packrt", "tracewriter", "psycopg"}
CODE_DIRS = ("deploy_server",)


def _code_files():
    return sorted(f for d in CODE_DIRS for f in (ROOT / d).rglob("*.py"))


def _imports(tree):
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                yield n.lineno, a.name
        elif isinstance(n, ast.ImportFrom) and n.level == 0:
            yield n.lineno, n.module or ""


def test_code_imports_only_allowed_packages():
    bad = []
    for f in _code_files():
        for line, name in _imports(ast.parse(f.read_text(encoding="utf-8"))):
            root = name.split(".")[0]
            if root == FORBIDDEN or (root not in sys.stdlib_module_names and root not in ALLOWED_THIRD_PARTY):
                bad.append(f"{f.relative_to(ROOT)}:{line} import {name}")
    assert not bad, "배포단 코드가 허용 밖 패키지를 import합니다:\n" + "\n".join(bad)


def test_no_dynamic_import():
    """문자열로 모듈을 불러오면 위 시험을 우회하므로 막는다(importlib·__import__)."""
    bad = []
    for f in _code_files():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for line, name in _imports(tree):
            if name.split(".")[0] == "importlib":
                bad.append(f"{f.relative_to(ROOT)}:{line} import {name}")
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and n.id == "__import__":
                bad.append(f"{f.relative_to(ROOT)}:{n.lineno} __import__")
    assert not bad, "\n".join(bad)


PROBE = r"""
import json, sys
from pathlib import Path
from fastapi.testclient import TestClient
from deploy_server.app import Deployment, create_app
from deploy_server.settings import load_settings
from fake_writer import FakeWriter   # DB에 쓰지 않는다(이 시험은 import만 본다)
root, data, packs = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
case = json.loads((root.parent / "task10-pack" / "pack" / "evals" / "task10-holdout" / "dataset.jsonl").read_text(encoding="utf-8").splitlines()[0])["input"]
s = load_settings(root / "config" / "v0-local.yaml", overrides={"data_dir": data, "packs_dir": packs})
c = TestClient(create_app(deployment=Deployment(s, trace_writer=FakeWriter(Path(data) / "trace"))))
r = c.post("/runs", content=json.dumps(case).encode(), headers={"Idempotency-Key": "boundary-1"})
print(json.dumps({"status": r.status_code, "modules": sorted({m.split(".")[0] for m in sys.modules})}))
"""


def test_server_run_loads_no_llmops_module(tmp_path, packs_dir):
    """새 프로세스에서 서버를 띄우고 요청 한 건을 처리한 뒤, 불러온 모듈에 llmops가 없음을 본다(packrt가 간접으로 불러와도 잡힘)."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": f"{ROOT}{os.pathsep}{ROOT / 'tests'}"}
    r = subprocess.run([sys.executable, "-c", PROBE, str(ROOT), str(tmp_path / "data"), str(packs_dir)],
                       capture_output=True, text=True, timeout=120, env=env, cwd=tmp_path)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["status"] == 201
    assert FORBIDDEN not in out["modules"]
    assert "packrt" in out["modules"]
