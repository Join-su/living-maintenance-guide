"""VPS R12 배선(VFE EV-005 D-325 W-1 · W-3) — scripts/vps/deploy-check r12 가짜 경계 시험.
- 호스트 쪽: 가짜 compose(부른 명령 기록 · config JSON · restart가 가짜 서버 drs를 바꿈) · 가짜 R12 runner(정한 출력) · 가짜 HTTP 서버(/info drs)
  — 새 편지 없음 = 손 0 · 바뀜 → restart 한 번 · drs 맞음 / 다름 → r12-revert → 다시 restart · 안쪽 실패 → restart 0 · 받은 편지 · 적재 비밀 없음 → 멈춤.
- 안쪽: `deploy-check --inner r12`가 실제 r12-update(시험 Pack · 견본 index_h0)를 부르고 `R12 결과:` 한 줄을 냄 · `r12-revert`가 되돌림.
실제 docker · 공유 DB · 비밀 0."""
import http.server
import json
import os
import subprocess
import sys
import threading

import pytest

from conftest import ROOT
from test_r12_update import BASE_V, UPDATE, _config, _inbox, _live_release, _pack_repo, _state, needs_index

SCRIPT = ROOT / "scripts" / "vps" / "deploy-check"


class Info(http.server.BaseHTTPRequestHandler):
    drs_file = None

    def do_GET(self):
        if self.path.startswith("/runs/"):
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"data_release_id": self.drs_file.read_text().strip()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def host(tmp_path, monkeypatch):
    log, drs_file = tmp_path / "calls.log", tmp_path / "server-drs.txt"
    drs_file.write_text("drs:old\n")
    Info.drs_file = drs_file
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Info)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("docker",):                                        # 실제 docker 호출 0(덫)
        f = bin_dir / name
        f.write_text(f"#!/bin/sh\necho {name} >> {tmp_path / 'trap.log'}\nexit 99\n")
        f.chmod(0o755)
    compose = bin_dir / "fake-compose"
    compose.write_text(f"""#!{sys.executable}
import json, os, sys, pathlib
a = sys.argv[1:]
open({str(log)!r}, "a").write(json.dumps({{"who": "compose", "argv": a}}) + "\\n")
if a[:1] == ["config"]:
    svc = a[-1]
    print(json.dumps({{"services": {{svc: {{"volumes": [{{"type": "volume", "source": "deploy-data-c1a15-test", "target": "/var/lib/deploy/data"}}],
                                         "ports": [{{"host_ip": "127.0.0.1", "published": "{port}"}}]}}}}}}))
if a[:1] == ["restart"]:
    nxt = pathlib.Path({str(tmp_path)!r}) / "next-drs.txt"
    if nxt.exists():                                            # 다시 뜬 서버가 읽은 drs(차례로 한 줄씩)
        lines = nxt.read_text().splitlines()
        pathlib.Path({str(drs_file)!r}).write_text(lines[0] + "\\n")
        nxt.write_text("\\n".join(lines[1:]))
""")
    compose.chmod(0o755)
    runner = bin_dir / "fake-r12"
    runner.write_text(f"""#!{sys.executable}
import json, os, sys, pathlib
a = sys.argv[1:]
open({str(log)!r}, "a").write(json.dumps({{"who": "r12", "argv": a, "env": {{k: v for k, v in os.environ.items() if k.startswith("R12_")}}}}) + "\\n")
f = pathlib.Path({str(tmp_path)!r}) / ("out-" + a[0] + ".txt")
print(f.read_text() if f.exists() else "", end="")
sys.exit(int(os.environ.get("FAKE_R12_RC_" + a[0].replace("-", "_").upper(), "0")))
""")
    runner.chmod(0o755)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("DEPLOY_CHECK_COMPOSE", str(compose))
    monkeypatch.setenv("DEPLOY_CHECK_R12_TOOLS", str(runner))

    class H:
        pass
    h = H()
    h.tmp, h.log, h.inbox, h.port, h.drs_file = tmp_path, log, inbox, port, drs_file
    yield h
    srv.shutdown()
    assert not (tmp_path / "trap.log").exists(), "실제 docker가 불림"


def calls(h, who):
    return [json.loads(l) for l in h.log.read_text().splitlines() if json.loads(l)["who"] == who] if h.log.exists() else []


def r12(h, *extra, env=None):
    args = ["r12", "--service", "deploy-c1a15", "--config", "config-live/c1a15-v1-pg-vps.yaml", "--graph", "run", "--node", "retrieve_history",
            "--inbox-dir", str(h.inbox), "--wait", "5", *extra]
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env={**os.environ, **(env or {})}, timeout=120)


def result(h, **res):
    (h.tmp / "out-r12.txt").write_text("corpus built: …\nR12 결과: " + json.dumps(res, ensure_ascii=False) + "\n")


def test_no_news_touches_no_service(host):
    (host.tmp / "out-r12.txt").write_text("받은 편지에 새 보고서 없음 — 그대로\nR12 결과: {\"status\": \"no_news\"}\n")
    r = r12(host)
    assert r.returncode == 0 and "새 보고서 없음 — 서비스 그대로" in r.stdout, r.stdout + r.stderr
    assert not [c for c in calls(host, "compose") if c["argv"][:1] == ["restart"]]
    env = calls(host, "r12")[0]["env"]
    assert env == {"R12_DATA_VOLUME": "deploy-data-c1a15-test", "R12_INBOX_DIR": str(host.inbox), "R12_LOADER_ENV": "/dev/null"}
    assert calls(host, "r12")[0]["argv"][:3] == ["r12", "--config", "config-live/c1a15-v1-pg-vps.yaml"] and "--with-loader" not in calls(host, "r12")[0]["argv"]


def test_changed_config_restarts_once_and_checks_drs(host):
    result(host, status="restart_needed", drs="drs:new", version="ir-history-new")
    (host.tmp / "next-drs.txt").write_text("drs:new\n")
    lf = host.tmp / "index-loader.env"
    lf.write_text("LLMOPSAGENT_INDEX_LOADER_DSN=postgresql://index_loader:x@postgres/db\n")
    r = r12(host, "--loader-env", str(lf))
    assert r.returncode == 0 and "결과: R12 반영(deploy-c1a15 · ir-history-new)" in r.stdout, r.stdout + r.stderr
    assert [c["argv"] for c in calls(host, "compose") if c["argv"][:1] == ["restart"]] == [["restart", "deploy-c1a15"]]
    first = calls(host, "r12")[0]
    assert "--with-loader" in first["argv"] and first["env"]["R12_LOADER_ENV"] == str(lf)
    assert not [c for c in calls(host, "r12") if c["argv"][0] == "r12-revert"]


def test_drs_mismatch_reverts_and_restarts_again(host):
    result(host, status="restart_needed", drs="drs:new", version="ir-history-new", ledger_drs="drs:new")   # 장부 줄을 먼저 씀(G-2)
    (host.tmp / "next-drs.txt").write_text("drs:wrong\ndrs:old\n")
    (host.tmp / "out-r12-revert.txt").write_text("운영 설정 되돌림: …\n")
    r = r12(host)
    assert r.returncode == 1 and "drs 다름" in r.stdout and "옛 설정으로 되돌려 다시 띄움" in r.stdout, r.stdout + r.stderr
    assert "주의: 장부에 쓴 R12 줄(drs drs:new)이 돌지 않음" in r.stdout
    rev = [c for c in calls(host, "r12") if c["argv"][0] == "r12-revert"]
    assert len(rev) == 1 and rev[0]["argv"][1:3] == ["--config", "config-live/c1a15-v1-pg-vps.yaml"] and "drs 다름" in rev[0]["argv"][-1]
    assert len([c for c in calls(host, "compose") if c["argv"][:1] == ["restart"]]) == 2


def _ledger_fake(h, rc):
    f = h.tmp / "bin" / "fake-ledger"
    f.write_text(f"""#!{sys.executable}
import json, sys
open({str(h.log)!r}, "a").write(json.dumps({{"who": "ledger", "argv": sys.argv[1:], "stdin": sys.stdin.read()}}) + "\\n")
print("data-release-auto 가짜 끝")
sys.exit({rc})
""")
    f.chmod(0o755)
    return f


def test_ledger_at_host_writes_the_ledger_then_finishes(host):
    ev = {"kind": "data_release_auto", "drs": "drs:new", "manifest_sha256": "m" * 64, "config_sha256": "c" * 64, "model_release_id": None}
    result(host, status="awaiting_ledger", deployment_id="c1a15@v5-pg-vps", pack_revision="sha256:" + "a" * 64, drs="drs:new", evidence=ev)
    (host.tmp / "out-r12-after-ledger.txt").write_text("운영 설정 바꿈 …\nR12 결과: " + json.dumps(
        {"status": "restart_needed", "drs": "drs:new", "version": "ir-history-new", "ledger_drs": "drs:new"}) + "\n")
    (host.tmp / "next-drs.txt").write_text("drs:new\n")
    lf = _ledger_fake(host, 0)
    r = r12(host, "--ledger-at-host", env={"DEPLOY_CHECK_LEDGER": f"{lf} --deployment-id {{deployment_id}} --pack-revision {{pack_revision}} --drs {{drs}} --evidence -"})
    assert r.returncode == 0 and "[1b/3] 장부 요청" in r.stdout and "결과: R12 반영" in r.stdout, r.stdout + r.stderr
    led = calls(host, "ledger")
    assert len(led) == 1 and json.loads(led[0]["stdin"]) == ev and led[0]["argv"][:2] == ["--deployment-id", "c1a15@v5-pg-vps"]
    assert "--ledger-at-host" in calls(host, "r12")[0]["argv"] and [c["argv"][0] for c in calls(host, "r12")] == ["r12", "r12-after-ledger"]


def test_ledger_failure_aborts_and_keeps_the_service(host):
    result(host, status="awaiting_ledger", deployment_id="c1a15@v5-pg-vps", pack_revision="sha256:" + "a" * 64, drs="drs:new", evidence={"drs": "drs:new"})
    (host.tmp / "out-r12-abort-ledger.txt").write_text("장부 요청 실패로 멈춤 …\n")
    lf = _ledger_fake(host, 1)
    r = r12(host, "--ledger-at-host", env={"DEPLOY_CHECK_LEDGER": f"{lf} --evidence -"})
    assert r.returncode == 1 and "장부 요청 실패" in r.stdout, r.stdout
    assert [c["argv"][0] for c in calls(host, "r12")] == ["r12", "r12-abort-ledger"]
    assert not [c for c in calls(host, "compose") if c["argv"][:1] == ["restart"]]


def test_inner_failure_keeps_the_service(host):
    result(host, status="failed", reason="packrt.index load 실패: load_refused …")
    r = r12(host, env={"FAKE_R12_RC_R12": "1"})
    assert r.returncode == 1 and "운영 설정 그대로(failed: packrt.index load 실패: load_refused" in r.stdout, r.stdout
    assert not [c for c in calls(host, "compose") if c["argv"][:1] == ["restart"]]


def test_no_result_line_stops(host):
    (host.tmp / "out-r12.txt").write_text("Traceback …\n")
    r = r12(host, env={"FAKE_R12_RC_R12": "1"})
    assert r.returncode == 1 and "결과를 내지 못함" in r.stdout
    assert not [c for c in calls(host, "compose") if c["argv"][:1] == ["restart"]]


def test_missing_inbox_or_loader_file_stops_before_anything(host):
    r = r12(host, "--inbox-dir", str(host.tmp / "없음"))
    assert r.returncode == 1 and "받은 편지 폴더" in r.stdout and calls(host, "r12") == []
    r = r12(host, "--loader-env", str(host.tmp / "없는.env"))
    assert r.returncode == 1 and "적재 비밀 파일" in r.stdout and calls(host, "r12") == []


def test_host_paths_for_config_are_refused(host):
    r = subprocess.run([sys.executable, str(SCRIPT), "r12", "--service", "s", "--config", "/srv/llmopsagent/deploy-config/x.yaml", "--graph", "g",
                        "--node", "n"], capture_output=True, text=True)
    assert r.returncode == 2 and "호스트 경로" in r.stderr


# ------------------------------------------------------------------ 안쪽(실제 r12-update)
def _inner(root, *args):
    env = {**os.environ, "DEPLOY_CHECK_R12": f"{sys.executable} {root / 'scripts' / 'r12-update'}",
           "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, str(SCRIPT), "--inner", *args], capture_output=True, text=True, env=env, timeout=300)


@needs_index
def test_inner_r12_reports_one_result_line_and_revert_undoes_it(tmp_path):
    import shutil
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    (root / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "r12-update", root / "scripts" / "r12-update")
    shutil.copy(ROOT / "scripts" / "serve.sh", root / "scripts" / "serve.sh")
    r = _inner(root, "r12", "--config", str(conf), *UPDATE)
    assert r.returncode == 0 and 'R12 결과: {"status": "no_news"}' in r.stdout, r.stdout + r.stderr
    _inbox(tmp_path)
    r = _inner(root, "r12", "--config", str(conf), *UPDATE)
    line = next(l for l in r.stdout.splitlines() if l.startswith("R12 결과: "))
    res = json.loads(line[len("R12 결과: "):])
    assert r.returncode == 0 and res["status"] == "restart_needed" and res["drs"].startswith("drs:") and res["reports"] == ["RPT-0001"], r.stdout
    assert _live_release(root, conf) == res["version"] != BASE_V
    r = _inner(root, "r12-revert", "--config", str(conf), "--reason", "시험")
    assert r.returncode == 0 and "운영 설정 되돌림" in r.stdout and _live_release(root, conf) == BASE_V
    assert _state(tmp_path)["history"][-1]["status"] == "reverted"


@needs_index
def test_inner_r12_failure_before_any_record_is_reported_as_failed(tmp_path):
    import shutil
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    (root / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "r12-update", root / "scripts" / "r12-update")
    box = tmp_path / "data" / "r12-inbox"
    box.mkdir(parents=True)
    (box / "bad.json").write_text(json.dumps({"report_id": "RPT-0009"}))   # document 없음 → 기록 전에 멈춤
    r = _inner(root, "r12", "--config", str(conf), *UPDATE)
    line = next(l for l in r.stdout.splitlines() if l.startswith("R12 결과: "))
    assert r.returncode == 1 and json.loads(line[len("R12 결과: "):])["status"] == "failed" and "모양" in line, r.stdout


@needs_index
def test_inner_ledger_at_host_then_after_ledger(tmp_path):
    import shutil
    repo = _pack_repo(tmp_path)
    root, conf = _config(tmp_path, repo)
    (root / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "r12-update", root / "scripts" / "r12-update")
    _inbox(tmp_path)
    r = _inner(root, "r12", "--config", str(conf), *UPDATE, "--ledger-at-host")
    res = json.loads(next(l for l in r.stdout.splitlines() if l.startswith("R12 결과: "))[len("R12 결과: "):])
    assert r.returncode == 0 and res["status"] == "awaiting_ledger" and res["evidence"]["drs"] == res["drs"], r.stdout + r.stderr
    assert "R12 대기: " not in r.stdout   # 증거는 결과 줄 하나로만
    r = _inner(root, "r12-after-ledger", "--config", str(conf))
    res2 = json.loads(next(l for l in r.stdout.splitlines() if l.startswith("R12 결과: "))[len("R12 결과: "):])
    assert r.returncode == 0 and res2["status"] == "restart_needed" and res2["ledger_drs"] == res["drs"], r.stdout
