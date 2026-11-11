"""R12 R-4 · R-5 진짜 서버 + 임시 PostgreSQL trace(답장 112 4 — 손 확인 r4 격리를 자동 시험으로).
`r12-update serve`(이 작업이 띄운 serve.sh 서버) → 첫 Run → 받은 편지 → `r12-update update`(내 서버 → 내렸다 다시 띄움) → 다음 첫 Run →
`stop` → 임시 DB trace.run의 두 Run drs · 묶음이 바뀜. 공유 DB 쓰기 0(시험마다 새 DB를 만들고 지움 — test_logdb와 같은 준비), 설치 0.
DEPLOY_PG=1(scripts/test-db.sh)이고 packrt 0.12.0 이상(packrt.index, D-416으로 .venv에 0.12.5 설치 · D-480 덧 4 repin 0.15.0)일 때만 돈다."""
import json
import os
import shutil
import socket
import urllib.request
import uuid

import pytest
import yaml

from conftest import ROOT
from test_logdb import _dsn, fresh_db, pg_gate, rows  # noqa: F401 — 시험 DB 준비 · 정리 그대로
from test_r12_update import BASE_V, _config, _inbox, _pack_repo, _run_script, needs_index

pytestmark = [pytest.mark.pg, needs_index]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(port, text):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/runs", method="POST", data=json.dumps({"text": text}, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json", "Idempotency-Key": f"r12pg-{uuid.uuid4().hex[:12]}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        assert r.status == 201
        return json.loads(r.read())


def test_r4_real_server_restart_changes_the_trace_drs(fresh_db, tmp_path, monkeypatch):
    monkeypatch.delenv("LLMOPSAGENT_TRACE_DSN", raising=False)
    adm, env, name = fresh_db
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    envf = tmp_path / "trace.env"
    envf.write_text(f"LLMOPSAGENT_TRACE_DSN={_dsn(adm, env, 'trace_writer', name)}\n", encoding="utf-8")
    envf.chmod(0o600)
    doc = yaml.safe_load(conf.read_text())
    doc["trace"] = {"dsn_env": "LLMOPSAGENT_TRACE_DSN", "env_file": str(envf)}
    conf.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
    (root / "scripts").mkdir(exist_ok=True)
    shutil.copy(ROOT / "scripts" / "py", root / "scripts" / "py")
    os.symlink(ROOT / ".venv", root / ".venv")
    os.symlink(ROOT / "deploy_server", root / "deploy_server")
    port = str(_free_port())
    started = False
    try:
        r = _run_script(root, "serve", "--config", str(conf), "--port", port)
        assert r.returncode == 0, r.stdout + r.stderr
        started = True
        a = _post(port, "공구 마모")
        new = _inbox(tmp_path)
        r = _run_script(root, "update", "--config", str(conf), "--graph", "g", "--node", "find_similar", "--port", port)
        assert r.returncode == 0 and "서버 다시 시작" in r.stdout, r.stdout + r.stderr
        b = _post(port, new["text"])
    finally:
        if started:
            r = _run_script(root, "stop", "--config", str(conf))
            assert r.returncode == 0, r.stdout + r.stderr
    assert not (tmp_path / "data" / "r12-server.pid").exists()
    state = json.loads((tmp_path / "data" / "r12-state.json").read_text())
    last = state["history"][-1]
    assert last["status"] == "switched"
    got = dict(((rid, (drs, binds)) for rid, drs, binds in
                rows(fresh_db, "SELECT run_id, data_release_id, release_bindings FROM trace.run WHERE run_id = ANY(%s)", ([a["run_id"], b["run_id"]],))))
    assert set(got) == {a["run_id"], b["run_id"]}   # 두 Run 다 임시 DB trace에
    (drs_a, bind_a), (drs_b, bind_b) = got[a["run_id"]], got[b["run_id"]]
    assert drs_a != drs_b and drs_b == last["drs"]
    assert BASE_V in json.dumps(bind_a) and last["version"] in json.dumps(bind_b) and BASE_V not in json.dumps(bind_b)
    assert len(b["result"]["grounding"]["similar"]) == 4   # 새 문서까지 찾음
