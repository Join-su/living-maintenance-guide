"""VPS 확인 스크립트 scripts/vps/deploy-check 가짜 경계 시험(VFE EV-005 ES-B, V-31 · V-32 · ES-G0 V-40 · ES-R V-41 운영 되돌리기).
- 가짜 원격 = task10-pack을 시험 폴더에 bare로 복제(원본은 읽기만), Pack 캐시 = 그 bare에서 clone
- 장부 = 시험 폴더 SQLite, 상태 폴더 = 시험 폴더, 설정 = 시험 폴더에 복사한 v1-pg-local(배포단 .env · PG DSN을 읽지 않음)
- compose = 부른 명령을 기록하는 가짜, C01 = 가짜 HTTP 서버, PATH 맨 앞의 덫(docker · rsync · pg_dump)이 실제 명령 호출 0을 증명"""
import http.server
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

from conftest import ROOT, TASK10

SCRIPT = ROOT / "scripts" / "vps" / "deploy-check"
DRS = "drs:54e2537743f7fa9c"


def _git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture(scope="module")
def remote(tmp_path_factory):
    if subprocess.run(["git", "-C", str(TASK10), "rev-parse", "--verify", "--quiet", "v1^{commit}"], capture_output=True).returncode:
        pytest.skip("task10-pack에 태그 v1이 없음")
    d = tmp_path_factory.mktemp("remote")
    bare = d / "task10-pack.git"
    _git("clone", "--bare", "--no-hardlinks", "--quiet", str(TASK10), str(bare))
    return bare, _git("-C", str(TASK10), "rev-parse", "v1^{commit}")


@pytest.fixture
def env(tmp_path, remote, monkeypatch):
    bare, commit = remote
    cache = tmp_path / "pack-cache" / "task10-pack"
    _git("clone", "--quiet", str(bare), str(cache))
    rev = yaml.safe_load((ROOT / "config" / "v1-pg-local.yaml").read_text())["pack"]["expect_revision"]
    doc = yaml.safe_load((ROOT / "config" / "v1-pg-local.yaml").read_text())
    doc["pack"].pop("expect_revision")
    doc["pack"]["repo"] = str(cache)
    doc["data_dir"], doc["packs_dir"] = str(tmp_path / "data"), str(tmp_path / "packs")
    doc["runtime_db"]["env_file"] = doc["trace"]["env_file"] = str(tmp_path / "no-such.env")
    ledger = tmp_path / "llmops.db"
    doc["registration"] = {"mode": "llmops_db", "path": str(ledger)}   # 시험 장부 = 시험 폴더 SQLite(저장소 설정은 A5 뒤 llmops_pg — 답장 031-01)
    cfg = tmp_path / "v1-test-vps.yaml"
    text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False).replace("  pack_subdir: pack\n", "  pack_subdir: pack\n  # expect_revision: 알림 값을 채움\n")
    cfg.write_text(text)
    status = tmp_path / "status" / "deployments"
    status.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trap = tmp_path / "trap.log"
    for name in ("docker", "rsync", "pg_dump"):           # 실제 명령이 불리면 기록(N2)
        f = bin_dir / name
        f.write_text(f"#!/bin/sh\necho {name} >> {trap}\nexit 99\n")
        f.chmod(0o755)
    calls = tmp_path / "compose.log"
    fake = bin_dir / "fake-compose"
    fake.write_text(f"""#!{sys.executable}
import json, os, sys, pathlib
st = pathlib.Path({str(status)!r}) / (os.environ.get("FAKE_STAGE_ID", "CHG-T1") + ".json")
stage = json.loads(st.read_text())["stage"] if st.exists() else None
open({str(calls)!r}, "a").write(json.dumps({{"who": "compose", "argv": sys.argv[1:], "stage": stage}}) + "\\n")
if sys.argv[1] == "ps" and os.environ.get("FAKE_COMPOSE_RUNNING"):
    print("abc123")
fail = os.environ.get("FAKE_COMPOSE_FAIL", "")
sys.exit(1 if fail and sys.argv[1] == fail else 0)
""")
    fake.chmod(0o755)
    for who, skip in (("tools", 0), ("exec", 1)):              # 안쪽 부르기 가짜: 기록한 뒤 같은 스크립트를 --inner로(컨테이너 대신 로컬)
        f = bin_dir / f"fake-{who}"
        f.write_text(f"""#!{sys.executable}
import json, os, sys
a = sys.argv[1:]
open({str(calls)!r}, "a").write(json.dumps({{"who": {who!r}, "service": a[0] if {skip} else None, "argv": a[{skip}:]}}) + "\\n")
os.execv(sys.executable, [sys.executable, {str(SCRIPT)!r}, "--inner", *a[{skip}:]])
""")
        f.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("DEPLOY_CHECK_COMPOSE", str(fake))
    monkeypatch.setenv("DEPLOY_CHECK_TOOLS", str(bin_dir / "fake-tools"))
    monkeypatch.setenv("DEPLOY_CHECK_EXEC", f"{bin_dir / 'fake-exec'} {{service}}")
    monkeypatch.setenv("DEPLOY_TAG", "v0.0-test")
    monkeypatch.setenv("PACKRT_TAG", "packrt-test")
    monkeypatch.setenv("DEPLOY_COMMIT", "d" * 40)                # F-10 ②(빌드 인자 — 가짜 compose라 빌드는 없음)
    bind = bin_dir / "fake-bind"                                 # U-4 묶기 대조 가짜(기록 · FAKE_BIND_FAIL이면 멈춤) — 실제 대조는 test_r12_* 몫
    bind.write_text(f"""#!{sys.executable}
import json, os, sys
open({str(calls)!r}, "a").write(json.dumps({{"who": "bind", "argv": sys.argv[1:]}}) + "\\n")
print("묶기 대조 실패 — 가짜" if os.environ.get("FAKE_BIND_FAIL") else "묶기 대조 통과 — 가짜")
sys.exit(1 if os.environ.get("FAKE_BIND_FAIL") else 0)
""")
    bind.chmod(0o755)
    monkeypatch.setenv("DEPLOY_CHECK_BIND", str(bind))

    class E:
        pass
    e = E()
    e.tmp, e.cfg, e.status, e.ledger, e.commit, e.rev, e.calls, e.trap, e.cache = tmp_path, cfg, status, ledger, commit, rev, calls, trap, cache
    e.data = tmp_path / "data"
    yield e
    assert not trap.exists(), f"실제 명령이 불림: {trap.read_text()}"   # 실제 docker · rsync · pg_dump 호출 0


def ledger_rows(e, rows):
    con = sqlite3.connect(e.ledger)
    con.execute("CREATE TABLE deployment (deployment_id TEXT, pack_revision TEXT, data_release_id TEXT, pack_commit TEXT, pack_tag TEXT)")
    con.executemany("INSERT INTO deployment VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()


def run(*args, extra_env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env={**os.environ, **(extra_env or {})}, timeout=300)


def reg(e, **over):
    a = {"--id": "CHG-T1", "--version": "V1", "--tag": "v1", "--commit": e.commit, "--revision": e.rev, "--config": str(e.cfg), "--status-dir": str(e.status)}
    a.update(over)
    return run("register", *[x for kv in a.items() for x in kv])


def status(e):
    p = e.status / "CHG-T1.json"
    return json.loads(p.read_text()) if p.exists() else None


# ---------------------------------------------------------------- ③ 등록(V-31)
def test_register_ok(env):
    ledger_rows(env, [("task10@v1-pg-local", env.rev, DRS, env.commit, "v1")])
    before = (ROOT / "config" / "v1-pg-local.yaml").read_bytes()
    r = reg(env)
    assert r.returncode == 0, r.stdout + r.stderr
    st = status(env)
    assert (st["stage"], st["revision"], st["commit"], st["tag"]) == ("registered", env.rev, env.commit, "v1")
    assert f'expect_revision: "{env.rev}"' in env.cfg.read_text()                 # 시험 사본에만 채움
    assert (ROOT / "config" / "v1-pg-local.yaml").read_bytes() == before         # 저장소 설정은 그대로
    assert "[5/5]" in r.stdout and "registered" in r.stdout and "[0/5] 운영 설정 그대로(이미 있음)" in r.stdout
    assert "[3/5] 묶기 대조" in r.stdout and "묶기 대조 통과" in r.stdout


def test_register_binding_check_failure_writes_nothing(env):
    """U-4(D-291 덧 · D-323): V-17 다음 묶기 대조가 실패하면 상태 파일 · expect_revision 0, V-04 안 부름."""
    before = env.cfg.read_text()
    r = run("register", "--id", "CHG-T1", "--version", "V1", "--tag", "v1", "--commit", env.commit, "--revision", env.rev,
            "--config", str(env.cfg), "--status-dir", str(env.status), extra_env={"FAKE_BIND_FAIL": "1"})
    assert r.returncode == 1 and "멈춤: 묶기 대조 실패" in r.stdout and "[4/5]" not in r.stdout, r.stdout + r.stderr
    assert status(env) is None and env.cfg.read_text() == before
    binds = [json.loads(l) for l in env.calls.read_text().splitlines() if '"bind"' in l]
    assert binds and binds[-1]["argv"] == ["check", "--config", str(env.cfg)]


def test_register_fetch_failure_writes_nothing(env):
    _git("-C", str(env.cache), "remote", "set-url", "origin", str(env.tmp / "no-such-remote.git"))
    r = reg(env)
    assert r.returncode == 1 and status(env) is None and "③ 대기" in r.stdout


def test_register_v17_mismatch_writes_nothing(env):
    r = reg(env, **{"--revision": "sha256:" + "0" * 64})
    assert r.returncode == 1 and status(env) is None and "V-17 불일치" in r.stdout


def test_register_other_tag_is_v17_mismatch(env):
    r = reg(env, **{"--tag": "v2"})
    assert r.returncode == 1 and status(env) is None


@pytest.mark.parametrize("rows", [[], [("task10@v1-pg-local", "sha256:" + "1" * 64, DRS, None, "v1")]])
def test_register_ledger_mismatch_writes_step3_failed(env, rows):
    ledger_rows(env, rows)
    r = reg(env)
    st = status(env)
    assert r.returncode == 1 and st["stage"] == "failed"
    assert st["detail"].startswith("배포단 등록 실패 — ") and "전환" not in st["detail"]   # V-24 ③


@pytest.mark.parametrize("over", [{"--commit": "abc"}, {"--id": "bad id"}, {"--version": "v1"}, {"--tag": "1"}])
def test_register_usage_error_writes_nothing(env, over):
    r = reg(env, **over)
    assert r.returncode == 2 and status(env) is None and not seq(env)


# ---------------------------------------------------------------- ④ 전환(V-32)
class Fake(http.server.BaseHTTPRequestHandler):
    code = 201
    data = None
    rev = commit = None
    dep, tag, bodies = "task10@v1-pg-local", "v1", []
    infos = []   # GET /info 응답 차례(D-375 reconfigure 시험) — 하나 남으면 그 값을 계속

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/info" and Fake.infos:
            info = Fake.infos.pop(0) if len(Fake.infos) > 1 else Fake.infos[0]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(info).encode())
            return
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b'{"error":{"code":"run_not_found"}}')

    def do_POST(self):
        Fake.bodies.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null"))
        rid = "run_01FAKEC01"
        if Fake.code == 201:
            from deploy_server.stores import RunStore
            RunStore(Fake.data / "runs").save({"run_id": rid, "status": "succeeded", "deployment_id": Fake.dep,
                                               "pack": {"pack_id": "task10", "pack_revision": Fake.rev, "commit": Fake.commit, "tag": Fake.tag}})
        self.send_response(Fake.code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"run_id": rid} if Fake.code == 201 else {"error": {"code": "trace_unavailable"}}).encode())


@pytest.fixture
def server(env):
    Fake.code, Fake.data, Fake.rev, Fake.commit = 201, env.data, env.rev, env.commit
    Fake.dep, Fake.tag, Fake.bodies, Fake.infos = "task10@v1-pg-local", "v1", [], []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def registered(env):
    ledger_rows(env, [("task10@v1-pg-local", env.rev, DRS, env.commit, "v1")])
    assert reg(env).returncode == 0


def sw(env, url, **over):
    case = env.tmp / "c01.json"
    case.write_text(json.dumps({"event_id": "EVT-T"}))
    a = {"--id": "CHG-T1", "--version": "V1", "--config": str(env.cfg), "--status-dir": str(env.status), "--service": "deploy-new",
         "--old-service": "deploy-old", "--url": url, "--case-file": str(case), "--wait": "5"}
    a.update(over)
    return [x for kv in a.items() for x in kv]


PS = ["ps", "--status", "running", "--quiet", "deploy-new"]


def seq(env):
    return [json.loads(l) for l in env.calls.read_text().splitlines()] if env.calls.exists() else []


def calls(env):
    return [x for x in seq(env) if x["who"] == "compose"]


def test_switch_order_new_up_then_switched_then_old_down(env, server):
    registered(env)
    r = run("switch", *sw(env, server))
    assert r.returncode == 0, r.stdout + r.stderr
    c = calls(env)
    assert [x["argv"] for x in c] == [PS, ["build", "deploy-new"], ["up", "-d", "deploy-new"], ["stop", "deploy-old"]]
    assert c[-1]["stage"] == "switched"                                          # 옛 서비스는 switched 뒤에만 내림(Q-N5)
    st = status(env)
    assert st["stage"] == "switched" and "run_01FAKEC01" in st["detail"]


def test_switch_c01_not_201_fails_step4_and_keeps_old(env, server):
    registered(env)
    Fake.code = 503
    r = run("switch", *sw(env, server))
    st = status(env)
    assert r.returncode == 1 and st["stage"] == "failed" and st["detail"] == "운영 전환 실패 — C01 응답 503"
    assert [x["argv"] for x in calls(env)][-1] == ["stop", "deploy-new"]          # 새 서비스만 내림, 옛 서비스 그대로(답장 019-02)
    assert ["stop", "deploy-old"] not in [x["argv"] for x in calls(env)]


def test_switch_new_service_fails_to_start(env, server):
    registered(env)
    r = run("switch", *sw(env, server), extra_env={"FAKE_COMPOSE_FAIL": "up"})
    st = status(env)
    assert r.returncode == 1 and st["detail"].startswith("운영 전환 실패 — 새 서비스가 켜지지 않음")
    assert [x["argv"] for x in calls(env)] == [PS, ["build", "deploy-new"], ["up", "-d", "deploy-new"], ["stop", "deploy-new"]]


def test_switch_requires_registered(env, server):
    r = run("switch", *sw(env, server))
    assert r.returncode == 1 and status(env) is None and not calls(env)


def _module():
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("deploy_check", str(SCRIPT))
    spec = importlib.util.spec_from_loader("deploy_check", loader)
    m = importlib.util.module_from_spec(spec)
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True          # scripts/vps/__pycache__를 만들지 않음
    try:
        loader.exec_module(m)
    finally:
        sys.dont_write_bytecode = old
    return m


@pytest.mark.parametrize("which", ["v17_check.py", "es3_check.py"])
def test_register_tool_crash_is_not_reported_as_mismatch(env, which, capsys):
    m = _module()
    real = m.py
    crash = subprocess.CompletedProcess([], 1, "", "Traceback (most recent call last):\nModuleNotFoundError: No module named 'x'\n")
    m.py = lambda script, *a: crash if script == which else real(script, *a)
    ledger_rows(env, [("task10@v1-pg-local", env.rev, DRS, env.commit, "v1")])
    rc = m.main(["--inner", "register", "--id", "CHG-T1", "--version", "V1", "--tag", "v1", "--commit", env.commit, "--revision", env.rev,
                 "--config", str(env.cfg), "--status-dir", str(env.status)])
    o = capsys.readouterr().out
    assert rc == 1 and status(env) is None and "불일치" not in o and "도구 오류" in o and "ModuleNotFoundError" in o


def test_register_old_image_cannot_read_pack_says_rebuild(env, capsys):
    """넘김 H-12(038 1-1): 옛 packrt 이미지로 새 Pack을 register하면 v17_check가 이유를 찍고 종료 1 → '알림 값 확인'이 아니라 '다시 빌드'."""
    m = _module()
    real = m.py
    old_image = subprocess.CompletedProcess([], 1, "V-17 task10@v1-pg-local: 이 이미지의 packrt가 Pack을 읽지 못함 — 정의되지 않은 키 'seed'\n", "")
    m.py = lambda script, *a: old_image if script == "v17_check.py" else real(script, *a)
    ledger_rows(env, [("task10@v1-pg-local", env.rev, DRS, env.commit, "v1")])
    rc = m.main(["--inner", "register", "--id", "CHG-T1", "--version", "V1", "--tag", "v1", "--commit", env.commit, "--revision", env.rev,
                 "--config", str(env.cfg), "--status-dir", str(env.status)])
    o = capsys.readouterr().out
    assert rc == 1 and status(env) is None and "다시 빌드" in o and "정의되지 않은 키 'seed'" in o and "알림 값을 확인" not in o


def test_v17_check_stops_with_reason_when_pack_does_not_load(monkeypatch, capsys):
    import importlib.util
    import types
    spec = importlib.util.spec_from_file_location("v17_check", ROOT / "scripts" / "v17_check.py")
    v17 = importlib.util.module_from_spec(spec)
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(v17)
    finally:
        sys.dont_write_bytecode = old
    s = types.SimpleNamespace(deployment_id="task10@v4-pg-local", pack_repo=Path("/x/task10-pack"), pack_ref="v4", packs_dir=Path("/x"),
                              pack_subdir="pack", releases_subdir="releases", expect_revision=None)
    monkeypatch.setattr(v17, "load_settings", lambda c: s)
    monkeypatch.setattr(v17, "materialize", lambda *a: types.SimpleNamespace(pack_dir=Path("/x/p"), commit="0" * 40, tag="v4"))
    monkeypatch.setattr(v17, "load_pack", lambda d: types.SimpleNamespace(ok=False, revision=None, errors=[{"message": "min_runtime 0.11.0"}]))
    rc = v17.main(["--config", "c.yaml", "--commit", "0" * 40, "--revision", "sha256:" + "0" * 64, "--tag", "v4"])
    assert rc == 1 and "Pack을 읽지 못함 — min_runtime 0.11.0" in capsys.readouterr().out


def test_switch_c01_connection_error_fails_step4(env, server):
    registered(env)
    r = run("switch", *sw(env, server, **{"--case-file": str(env.tmp / "no-such-case.json")}))
    st = status(env)
    assert r.returncode == 1 and st["stage"] == "failed" and st["detail"].startswith("운영 전환 실패 — C01 요청 오류")
    assert ["stop", "deploy-old"] not in [x["argv"] for x in calls(env)] and ["stop", "deploy-new"] in [x["argv"] for x in calls(env)]


def test_switch_old_service_same_as_new_is_usage_error(env, server):
    registered(env)
    r = run("switch", *sw(env, server, **{"--old-service": "deploy-new"}))
    assert r.returncode == 2 and status(env)["stage"] == "registered" and not calls(env)


def test_register_after_switched_does_not_go_back(env, server):
    registered(env)
    assert run("switch", *sw(env, server)).returncode == 0
    r = reg(env)
    assert r.returncode == 1 and status(env)["stage"] == "switched" and "이미 switched" in r.stdout


def test_switch_failure_keeps_new_service_if_it_was_already_running(env, server):
    registered(env)
    Fake.code = 503
    r = run("switch", *sw(env, server), extra_env={"FAKE_COMPOSE_RUNNING": "1"})
    assert r.returncode == 1 and "이미 켜져 있음" in r.stdout
    assert not any(x["argv"][0] == "stop" for x in calls(env))


@pytest.mark.parametrize("unset", ["DEPLOY_TAG", "PACKRT_TAG", "DEPLOY_COMMIT"])
def test_switch_needs_build_tags(env, server, monkeypatch, unset):
    registered(env)
    monkeypatch.delenv(unset)
    r = run("switch", *sw(env, server))
    assert r.returncode == 2 and status(env)["stage"] == "registered" and not calls(env)


@pytest.mark.parametrize("value", ["d" * 12, "D" * 40, "g" * 40])
def test_short_or_bad_deploy_commit_stops_every_command(env, server, monkeypatch, value):
    """F-10 ②: DEPLOY_COMMIT이 40자 소문자 hex가 아니면 register · switch 모두 아무것도 하지 않음(빌드 전)."""
    registered(env)
    monkeypatch.setenv("DEPLOY_COMMIT", value)
    r = run("switch", *sw(env, server))
    assert r.returncode == 2 and "DEPLOY_COMMIT은 deploy 태그의 40자 commit" in r.stdout and not calls(env)
    r = reg(env, **{"--id": "CHG-T9"})
    assert r.returncode == 2 and not (env.status / "CHG-T9.json").exists()


def test_fill_expect_revision_is_atomic_and_leaves_no_temp(env):
    m = _module()
    assert m.fill_expect_revision(str(env.cfg), env.rev) == "채움"
    assert f'expect_revision: "{env.rev}"' in env.cfg.read_text()
    assert not [p for p in env.cfg.parent.iterdir() if p.name.startswith(f".{env.cfg.name}.tmp-")]
    assert m.fill_expect_revision(str(env.cfg), env.rev) == "그대로(이미 같음)"


def test_port_of_reads_compose_config_json(tmp_path, monkeypatch):
    f = tmp_path / "fake-compose-config"
    argv_log = tmp_path / "argv.txt"
    f.write_text(f"""#!{sys.executable}
import json, sys
open({str(argv_log)!r}, "a").write(" ".join(sys.argv[1:]) + "\\n")
print(json.dumps({{"services": {{
    "a": {{"ports": [{{"mode": "ingress", "host_ip": "127.0.0.1", "target": 8781, "published": "8781", "protocol": "tcp"}}]}},
    "b": {{"ports": ["127.0.0.1:8790:8783"]}},
    "c": {{"ports": [{{"target": 8781, "published": "8781"}}]}}}}}}))
""")
    f.chmod(0o755)
    monkeypatch.setenv("DEPLOY_CHECK_COMPOSE", str(f))
    m = _module()
    assert m.port_of("a") == 8781 and m.port_of("b") == 8790
    assert argv_log.read_text().splitlines()[0] == "config --format json a"          # 서비스 이름을 줌 — profile(standby) 서비스도 나옴
    with pytest.raises(SystemExit):
        m.port_of("c")                                                          # 127.0.0.1이 아니면 --url을 주라는 안내


# ---------------------------------------------------------------- V-40 실행 자리 · 운영 설정(ES-G0)
def test_register_copies_template_into_live_config_only(env):
    ledger_rows(env, [("task10@v1-pg-local", env.rev, DRS, env.commit, "v1")])
    template = env.tmp / "image-config" / "v1-test-vps.yaml"
    template.parent.mkdir()
    template.write_bytes(env.cfg.read_bytes())
    before = template.read_bytes()
    live = env.tmp / "config-live" / "v1-test-vps.yaml"
    r = reg(env, **{"--config": str(live), "--template": str(template)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[0/5] 운영 설정 만듦" in r.stdout and status(env)["stage"] == "registered"
    assert f'expect_revision: "{env.rev}"' in live.read_text()                      # 운영 설정에만 채움
    assert template.read_bytes() == before                                          # 이미지 설정은 그대로


def test_switch_precheck_stops_when_live_revision_differs(env, server):
    registered(env)
    env.cfg.write_text(env.cfg.read_text().replace(env.rev, "sha256:" + "2" * 64))   # 운영 설정이 상태 파일과 다름(N4)
    before = status(env)
    r = run("switch", *sw(env, server))
    assert r.returncode == 1 and "revision 대조 없이" in r.stdout
    assert not calls(env) and status(env) == before                                 # compose 호출 0, 상태 그대로


def test_switch_precheck_stops_without_live_config(env, server):
    registered(env)
    r = run("switch", *sw(env, server, **{"--config": str(env.tmp / "config-live" / "none.yaml")}))
    assert r.returncode == 1 and "register(또는 seed)" in r.stdout and not calls(env)


def test_switch_order_tools_compose_exec(env, server):
    registered(env)
    env.calls.unlink()
    a = sw(env, server)
    i = a.index("--case-file")
    del a[i:i + 2]                                                                  # C01 입력을 새 서비스 안(EXEC)에서 만듦
    r = run("switch", *a)
    assert r.returncode == 0, r.stdout + r.stderr
    got = [(x["who"], x["argv"][0]) for x in seq(env)]
    assert got == [("tools", "precheck"), ("compose", "ps"), ("compose", "build"), ("compose", "up"),
                   ("exec", "c01-input"), ("exec", "mark-switched"), ("compose", "stop")]
    assert all(x["service"] == "deploy-new" for x in seq(env) if x["who"] == "exec")
    assert seq(env)[-1]["argv"] == ["stop", "deploy-old"] and status(env)["stage"] == "switched"


def test_default_runners_build_tools_and_exec_into_new_service(monkeypatch):
    for n in ("DEPLOY_CHECK_TOOLS", "DEPLOY_CHECK_EXEC", "DEPLOY_CHECK_COMPOSE"):
        monkeypatch.delenv(n, raising=False)
    m = _module()
    tools, ex = " ".join(m.tools_runner()), " ".join(m.exec_runner("deploy-task10-v2-pg-local"))
    assert "docker compose -f" in tools and str(ROOT / "docker" / "compose.yaml") in tools
    assert "run --rm --no-deps --build deploy-tools python scripts/vps/deploy-check --inner" in tools     # --build: 그 태그 이미지(V-40 d)
    assert "exec -T deploy-task10-v2-pg-local python scripts/vps/deploy-check --inner" in ex


def test_host_side_runs_without_site_packages(env, server):
    """V-40 (e): 호스트 쪽을 python -I -S(site-packages 없음)로 끝까지 돌림 — 안쪽은 가짜 runner(로컬 --inner)."""
    registered(env)
    r = subprocess.run([sys.executable, "-I", "-S", str(SCRIPT), "switch", *sw(env, server)], capture_output=True, text=True,
                       env={**os.environ}, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert status(env)["stage"] == "switched"


def test_host_side_imports_only_stdlib_and_never_touches_sys_path():
    """V-40 (e) 정적 검사: inner_* 가 아닌 함수와 모듈 머리의 import는 표준 라이브러리뿐, sys.path 변경은 inner_main에서만."""
    import ast
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))

    def mods(node):
        for n in ast.walk(node):
            if isinstance(n, ast.Import):
                yield from (a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                yield (n.module or "").split(".")[0]
    host = [n for n in tree.body if not (isinstance(n, ast.FunctionDef) and n.name.startswith("inner_"))]
    used = {m for n in host for m in mods(n)}
    assert used and used <= set(sys.stdlib_module_names), used - set(sys.stdlib_module_names)
    touch = sorted({f.name for f in tree.body if isinstance(f, ast.FunctionDef)
                    for n in ast.walk(f) if isinstance(n, ast.Attribute) and n.attr == "path"
                    and isinstance(n.value, ast.Name) and n.value.id == "sys"})            # sys.path 참조 자체가 inner_main에만
    top = [n for n in tree.body if not isinstance(n, ast.FunctionDef)
           for x in ast.walk(n) if isinstance(x, ast.Attribute) and x.attr == "path" and isinstance(x.value, ast.Name) and x.value.id == "sys"]
    assert touch == ["inner_main"] and not top


def test_loading_host_module_leaves_sys_path_unchanged():
    before = list(sys.path)
    _module()
    assert sys.path == before                                                       # 호스트 경로는 저장소 뿌리를 넣지 않음(승인 조건)


# ---------------------------------------------------------------- seed(ES-G0 L0, 답장 019-04 질문 1 A)
def seed(env, live, template):
    return run("seed", "--config", str(live), "--template", str(template))


def test_seed_copies_filled_image_config_without_status(env):
    template = ROOT / "config" / "v0-pg-vps.yaml"                                    # 값(V0 revision)이 이미 든 이미지 설정
    live = env.tmp / "config-live" / "v0-pg-vps.yaml"
    r = seed(env, live, template)
    assert r.returncode == 0, r.stdout + r.stderr
    assert live.read_bytes() == template.read_bytes() and not any(env.status.iterdir())   # 값 그대로 복사, 상태 파일 쓰기 0
    assert [x["argv"][0] for x in seq(env)] == ["seed"]


def test_seed_never_overwrites_existing_live_config(env):
    live = env.tmp / "config-live" / "v1-pg-vps.yaml"
    live.parent.mkdir()
    live.write_text("# 운영 중 설정\n")
    r = seed(env, live, ROOT / "config" / "v1-pg-vps.yaml")
    assert r.returncode == 0 and "덮지 않음" in r.stdout and live.read_text() == "# 운영 중 설정\n"


def test_seed_refuses_template_without_revision(env):
    live = env.tmp / "config-live" / "x.yaml"
    r = seed(env, live, env.cfg)                                                    # expect_revision 주석 줄만 있는 설정
    assert r.returncode == 1 and "register로" in r.stdout and not live.exists() and not any(env.status.iterdir())



def test_switch_exec_failure_on_switched_writes_step4_failed(env, server):
    """독립 확인 F-3: switched 기록 자체가 실패(exec 127)하면 deploy-tools에서 failed를 쓰고 새 서비스를 내림."""
    registered(env)
    bad = env.tmp / "bin" / "fake-exec-broken"
    bad.write_text("#!/bin/sh\nexit 127\n")
    bad.chmod(0o755)
    r = run("switch", *sw(env, server), extra_env={"DEPLOY_CHECK_EXEC": f"{bad} {{service}}"})
    st = status(env)
    assert r.returncode == 1 and st["stage"] == "failed" and st["detail"] == "운영 전환 실패 — switched 기록 실패(종료 127)"
    assert [x["argv"] for x in calls(env)][-1] == ["stop", "deploy-new"] and ["stop", "deploy-old"] not in [x["argv"] for x in calls(env)]


def test_register_inner_error_is_shown(env):
    """독립 확인 F-4: 안쪽이 SystemExit 문자열로 멈추면 호스트가 그 줄을 보여 줌."""
    r = reg(env, **{"--config": str(env.tmp / "config-live" / "none.yaml")})       # 운영 설정도 --template도 없음
    assert r.returncode == 1 and "(안쪽 오류)" in r.stdout and "--template" in r.stdout and status(env) is None


@pytest.mark.parametrize("flag", ["--config", "--template"])
def test_host_paths_are_rejected(env, flag):
    r = reg(env, **{flag: "/srv/llmopsagent/deploy-config/v1-pg-vps.yaml"})
    assert r.returncode == 2 and "컨테이너 안 경로" in r.stderr and not seq(env)


def test_register_warns_when_existing_live_config_differs_from_template(env):
    ledger_rows(env, [("task10@v1-pg-local", env.rev, DRS, env.commit, "v1")])
    template = env.tmp / "image.yaml"
    template.write_text(env.cfg.read_text().replace("review_policy", "review_policy_x", 1) if "review_policy" in env.cfg.read_text() else env.cfg.read_text() + "# 다른 줄\n")
    r = reg(env, **{"--template": str(template)})
    assert "주의: 운영 설정이 이미지 설정과 다름" in r.stdout


def test_switch_exec_exit1_without_failed_write_is_recorded(env, server):
    """2회차 확인 N-2: switched 기록이 종료 1로 끝났는데 상태가 그대로 registered이면 deploy-tools에서 failed를 씀."""
    registered(env)
    bad = env.tmp / "bin" / "fake-exec-exit1"
    bad.write_text("#!/bin/sh\nexit 1\n")
    bad.chmod(0o755)
    r = run("switch", *sw(env, server), extra_env={"DEPLOY_CHECK_EXEC": f"{bad} {{service}}"})
    st = status(env)
    assert r.returncode == 1 and st["stage"] == "failed" and st["detail"].startswith("운영 전환 실패 — switched 기록 실패(종료 1")
    assert ["stop", "deploy-old"] not in [x["argv"] for x in calls(env)]


# ---------------------------------------------------------------- 운영 되돌리기(V-41, ES-R — D-140 안 A)
V1_ID = "CHG-task10-V1-3e315c04"


def status_file(e, sid, stage, dep, at, version="V1", rev=None, tag="v1"):
    (e.status / f"{sid}.json").write_text(json.dumps({"id": sid, "version": version, "revision": rev or e.rev, "stage": stage, "detail": "시험",
                                                     "commit": e.commit, "tag": tag, "deployment_id": dep, "updated_at": at}, ensure_ascii=False))


@pytest.fixture
def rb(env, server):
    """V0 운영 설정(seed로 채운 것과 같은 모양, expect_revision 든 것) + V1 switched 상태 파일(10-15 꼴, +09:00) + 장부 V0 · V1 줄(40자)."""
    doc = yaml.safe_load((ROOT / "config" / "v0-pg-vps.yaml").read_text())
    env.v0rev = doc["pack"]["expect_revision"]
    doc["pack"]["repo"] = str(env.cache)
    doc["data_dir"], doc["packs_dir"] = str(env.tmp / "data0"), str(env.tmp / "packs")
    doc["runtime_db"]["env_file"] = doc["trace"]["env_file"] = str(env.tmp / "no-such.env")
    doc["registration"] = {"mode": "llmops_db", "path": str(env.ledger)}
    env.v0cfg = env.tmp / "config-live" / "v0-pg-vps.yaml"
    env.v0cfg.parent.mkdir()
    env.v0cfg.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
    env.v0commit = _git("-C", str(env.cache), "rev-parse", "v0^{commit}")
    status_file(env, V1_ID, "switched", "task10@v1-pg-local", "2026-10-15T12:13:29+09:00")
    env.v1_status = (env.status / f"{V1_ID}.json").read_bytes()
    ledger_rows(env, [("task10@v0-pg-local", env.v0rev, DRS, env.v0commit, "v0"), ("task10@v1-pg-local", env.rev, DRS, env.commit, "v1")])
    Fake.data, Fake.rev, Fake.commit, Fake.dep, Fake.tag = env.tmp / "data0", env.v0rev, env.v0commit, "task10@v0-pg-local", "v0"
    env.v0cfg_bytes, env.ledger_bytes = env.v0cfg.read_bytes(), env.ledger.read_bytes()
    env.url = server
    os.environ["FAKE_STAGE_ID"] = "RB-T1"
    yield env
    os.environ.pop("FAKE_STAGE_ID", None)


def rbargs(e, case=True, **over):
    a = {"--id": "RB-T1", "--version": "V0", "--config": str(e.v0cfg), "--status-dir": str(e.status), "--service": "deploy-v0",
         "--old-service": "deploy-v1", "--url": e.url, "--wait": "5"}
    if case:
        c = e.tmp / "c01.json"
        c.write_text(json.dumps({"event_id": "EVT-T"}))
        a["--case-file"] = str(c)
    a.update(over)
    return [x for kv in a.items() if kv[1] is not None for x in kv]


def rbstatus(e, sid="RB-T1"):
    p = e.status / f"{sid}.json"
    return json.loads(p.read_text()) if p.exists() else None


def tools_argv(e):
    return [x["argv"][0] for x in seq(e) if x["who"] == "tools"]


def test_rollback_ok_registered_then_switch_then_old_down(rb):
    r = run("rollback", *rbargs(rb))
    assert r.returncode == 0, r.stdout + r.stderr
    st = rbstatus(rb)
    assert (st["stage"], st["version"], st["deployment_id"], st["revision"], st["tag"]) == ("switched", "V0", "task10@v0-pg-local", rb.v0rev, "v0")
    c = calls(rb)
    assert [x["argv"] for x in c] == [["ps", "--status", "running", "--quiet", "deploy-v0"], ["build", "deploy-v0"], ["up", "-d", "deploy-v0"], ["stop", "deploy-v1"]]
    assert [x["stage"] for x in c][:3] == ["registered"] * 3 and c[-1]["stage"] == "switched"     # registered 뒤에 시작, V1은 switched 뒤에만 내림
    assert tools_argv(rb)[:2] == ["restore-check", "precheck"] and "register" not in tools_argv(rb)
    assert "V-17" not in r.stdout and "fetch --tags" not in r.stdout and "태그 받기" not in r.stdout   # V-17 · fetch 없음
    assert (rb.status / f"{V1_ID}.json").read_bytes() == rb.v1_status                         # (j) 다른 상태 파일 그대로
    assert rb.v0cfg.read_bytes() == rb.v0cfg_bytes and rb.ledger.read_bytes() == rb.ledger_bytes  # (j) 운영 설정 · 장부 바이트 그대로


def test_rollback_does_not_fetch_or_call_v17(rb, monkeypatch):
    log = rb.tmp / "git.log"
    g = rb.tmp / "bin" / "fake-git"
    g.write_text(f"#!/bin/sh\necho \"$*\" >> {log}\nexec git \"$@\"\n")
    g.chmod(0o755)
    monkeypatch.setenv("DEPLOY_CHECK_GIT", str(g))
    m = _module()
    seen = []
    real = m.py
    m.py = lambda script, *a: (seen.append(script), real(script, *a))[1]
    monkeypatch.setattr(sys, "executable", sys.executable)
    assert m.inner_main(["restore-check", "--id", "RB-T1", "--version", "V0", "--config", str(rb.v0cfg), "--status-dir", str(rb.status)]) == 0
    subs = [l.split()[2] for l in log.read_text().splitlines()]                                 # "-C <캐시> <하위 명령> …"
    assert seen == ["es3_check.py"] and subs == ["rev-parse"]                                  # V-17 도구 · fetch 호출 0


@pytest.mark.parametrize("what", ["no_config", "no_revision", "no_tag"])
def test_rollback_missing_live_config_or_tag_writes_nothing(rb, what):
    if what == "no_config":
        rb.v0cfg.unlink()
    elif what == "no_revision":
        rb.v0cfg.write_text(rb.v0cfg.read_text().replace(f'expect_revision: {rb.v0rev}', "").replace(f"expect_revision: '{rb.v0rev}'", ""))
        assert rb.v0rev not in rb.v0cfg.read_text()
    else:
        _git("-C", str(rb.cache), "tag", "-d", "v0")
    r = run("rollback", *rbargs(rb))
    assert r.returncode == 1 and rbstatus(rb) is None and not calls(rb)
    assert "멈춤" in r.stdout


def test_rollback_already_operational_stops(rb):
    status_file(rb, "A-RB-OLD", "switched", "task10@v0-pg-local", "2026-10-20T10:00:00+09:00", version="V0", rev=rb.v0rev, tag="v0")
    os.utime(rb.status / "A-RB-OLD.json", (1_000_000_000, 1_000_000_000))                    # 이름 · mtime으로는 V1이 뒤 — 시각으로만 V0이 최근
    r = run("rollback", *rbargs(rb))
    assert r.returncode == 1 and "이미 운영 중" in r.stdout and rbstatus(rb) is None and not calls(rb)


@pytest.mark.parametrize("rows", [[], [("task10@v0-pg-local", None, "drs:other", None, "v0")]])   # 줄 없음 · drs 다름(revision은 V0)
def test_rollback_ledger_mismatch_writes_step3_failed(rb, rows):
    rb.ledger.unlink()
    ledger_rows(rb, [(d, rev or rb.v0rev, drs, c, t) for d, rev, drs, c, t in rows])
    r = run("rollback", *rbargs(rb))
    st = rbstatus(rb)
    assert r.returncode == 1 and st["stage"] == "failed" and st["detail"].startswith("배포단 등록 실패 — ") and "전환" not in st["detail"]
    assert not calls(rb)


def test_rollback_id_already_switched_stops(rb):
    status_file(rb, "RB-T1", "switched", "task10@v0-pg-local", "2026-10-01T00:00:00+00:00", version="V0", rev=rb.v0rev, tag="v0")
    before = (rb.status / "RB-T1.json").read_bytes()
    r = run("rollback", *rbargs(rb))
    assert r.returncode == 1 and (rb.status / "RB-T1.json").read_bytes() == before and not calls(rb)


def test_rollback_c01_failure_step4_keeps_v1(rb):
    Fake.code = 503
    r = run("rollback", *rbargs(rb))
    st = rbstatus(rb)
    assert r.returncode == 1 and st["stage"] == "failed" and st["detail"] == "운영 전환 실패 — C01 응답 503"
    argv = [x["argv"] for x in calls(rb)]
    assert argv[-1] == ["stop", "deploy-v0"] and ["stop", "deploy-v1"] not in argv


def test_rollback_reverse_v0_to_v1(rb):
    """반대쪽(V0 → V1 다시)도 같은 명령 — V0이 운영 중(가장 최근 switched)일 때 V1으로."""
    status_file(rb, "RB-OLD", "switched", "task10@v0-pg-local", "2026-10-20T10:00:00+09:00", version="V0", rev=rb.v0rev, tag="v0")
    rb.cfg.write_text(rb.cfg.read_text().replace("  # expect_revision: 알림 값을 채움\n", f'  expect_revision: "{rb.rev}"\n'))
    Fake.data, Fake.rev, Fake.commit, Fake.dep, Fake.tag = rb.data, rb.rev, rb.commit, "task10@v1-pg-local", "v1"
    os.environ["FAKE_STAGE_ID"] = "RB-T2"
    r = run("rollback", *rbargs(rb, **{"--id": "RB-T2", "--version": "V1", "--config": str(rb.cfg), "--service": "deploy-v1", "--old-service": "deploy-v0"}))
    assert r.returncode == 0, r.stdout + r.stderr
    st = rbstatus(rb, "RB-T2")
    assert (st["stage"], st["version"], st["deployment_id"]) == ("switched", "V1", "task10@v1-pg-local")
    assert [x["argv"] for x in calls(rb)][-1] == ["stop", "deploy-v0"]


@pytest.mark.parametrize("over", [{"--id": "bad id"}, {"--version": "v0"}, {"--old-service": "deploy-v0"}, {"--old-service": None}, {"--config": "/srv/llmopsagent/deploy-config/v0.yaml"}])
def test_rollback_usage_error_writes_nothing(rb, over):
    r = run("rollback", *rbargs(rb, **over))
    assert r.returncode == 2 and rbstatus(rb) is None and not seq(rb)


@pytest.mark.parametrize("commit", ["other40", "short7"])
def test_rollback_ledger_commit_differs_or_short_is_step3(rb, commit):
    """(k) 장부 commit이 태그 commit과 다름 · 지금 실제 장부처럼 7자(v0 장부 줄 꼴, GAP-009) → ③ 실패, 전환 없음."""
    value = "f" * 40 if commit == "other40" else rb.v0commit[:7]
    rb.ledger.unlink()
    ledger_rows(rb, [("task10@v0-pg-local", rb.v0rev, DRS, value, "v0")])
    r = run("rollback", *rbargs(rb))
    st = rbstatus(rb)
    assert r.returncode == 1 and st["stage"] == "failed" and st["detail"].startswith("배포단 등록 실패 — ") and "commit" in st["detail"]
    assert not calls(rb)


def test_rollback_c01_input_comes_from_target_tag(rb):
    """(l) --case-file 없이: C01 입력은 대상(V0) 서비스 안 태그 v0 트리에서. 시험 사본의 v1만 C01이 다른 commit으로 옮겨 구분."""
    work = rb.tmp / "work"
    _git("clone", "--quiet", str(rb.cache), str(work))
    _git("-C", str(work), "checkout", "--quiet", "v1")
    ds = work / "pack" / "evals" / "task10-holdout" / "dataset.jsonl"
    rows = [json.loads(l) for l in ds.read_text(encoding="utf-8").splitlines() if l.strip()]
    for row in rows:
        if row["case_id"] == "C01":
            row["input"] = {**row["input"], "event_id": "EVT-V1-ONLY"}
    ds.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
    _git("-C", str(work), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "--quiet", "-am", "v1 C01 다름(시험)")
    _git("-C", str(rb.cache), "fetch", "--quiet", str(work), "+HEAD:refs/heads/t-v1")
    _git("-C", str(rb.cache), "tag", "-f", "v1", "t-v1")
    v0_c01 = next(json.loads(l)["input"] for l in _git("-C", str(rb.cache), "show", "v0:pack/evals/task10-holdout/dataset.jsonl").splitlines() if '"C01"' in l)
    r = run("rollback", *rbargs(rb, case=False))
    assert r.returncode == 0, r.stdout + r.stderr
    assert Fake.bodies == [v0_c01] and Fake.bodies[0].get("event_id") != "EVT-V1-ONLY"
    assert [x["service"] for x in seq(rb) if x["who"] == "exec"][0] == "deploy-v0"


def later_first(rb, later, earlier):
    """시각이 늦은 파일을 이름 앞(A-)에 두고 먼저 쓰며 mtime도 더 이르게 — 이름순 · 쓴 순서 · mtime으로 고르는 구현은 틀림(F-1)."""
    (rb.status / f"{V1_ID}.json").unlink()
    status_file(rb, "A-later", *later)
    status_file(rb, "B-earlier", *earlier)
    os.utime(rb.status / "A-later.json", (1_000_000_000, 1_000_000_000))
    os.utime(rb.status / "B-earlier.json", (2_000_000_000, 2_000_000_000))


def test_rollback_latest_switched_by_time_not_text(rb):
    """(m) 글자 순서와 시각 순서가 반대인 짝: 01:00+00:00(V1)이 09:30+09:00(= 00:30Z, V0)보다 늦음 → 지금 운영 = V1 → V0 되돌리기 계속."""
    later_first(rb, ("switched", "task10@v1-pg-local", "2026-10-24T01:00:00+00:00"),
                ("switched", "task10@v0-pg-local", "2026-10-24T09:30:00+09:00", "V0", rb.v0rev, "v0"))
    r = run("rollback", *rbargs(rb))
    assert r.returncode == 0 and "지금 운영: task10@v1-pg-local" in r.stdout, r.stdout


def test_rollback_latest_switched_by_time_stops_when_target(rb):
    """(m) 같은 짝을 뒤집으면(늦은 쪽이 V0) 멈춤 — 글자 비교 · 이름순 · mtime 구현은 둘 중 하나에서 틀림."""
    later_first(rb, ("switched", "task10@v0-pg-local", "2026-10-24T01:00:00+00:00", "V0", rb.v0rev, "v0"),
                ("switched", "task10@v1-pg-local", "2026-10-24T09:30:00+09:00"))
    r = run("rollback", *rbargs(rb))
    assert r.returncode == 1 and "이미 운영 중" in r.stdout and rbstatus(rb) is None and not calls(rb)


@pytest.mark.parametrize("bad", ["not json", '{"stage": "switched", "updated_at": "2026-10-24T01:00:00"}', '{"stage": "registered"}', '{"stage": "switched", "updated_at": 123}', '[1]'])
def test_rollback_unreadable_status_file_stops(rb, bad):
    (rb.status / "X.json").write_text(bad)
    (rb.status / "notes.txt").write_text("상태 파일 아님")                      # *.json 말고는 보지 않음
    r = run("rollback", *rbargs(rb))
    assert r.returncode == 1 and "X.json" in r.stdout and rbstatus(rb) is None and not calls(rb)


def test_rollback_ignores_non_json_files(rb):
    """*.json만 본다 — 깨진 notes.txt · 시간대 없는 *.bak만 있으면 정상으로 끝까지."""
    (rb.status / "notes.txt").write_text("not json")
    (rb.status / "old.json.bak").write_text('{"stage": "switched", "updated_at": "2026-10-24T01:00:00"}')
    r = run("rollback", *rbargs(rb))
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("stage", ["registered", "failed"])
def test_rollback_other_deployment_registered_or_failed_file_is_not_overwritten(rb, stage):
    """다른 배포의 registered · failed 파일도 stage와 상관없이 덮지 않음(F-3)."""
    status_file(rb, "RB-T1", stage, "task10@v1-pg-local", "2026-10-24T00:00:00+00:00")
    before = (rb.status / "RB-T1.json").read_bytes()
    r = run("rollback", *rbargs(rb))
    assert r.returncode == 1 and "다른 배포의 상태 파일" in r.stdout and (rb.status / "RB-T1.json").read_bytes() == before and not calls(rb)


def test_rollback_other_deployment_id_file_is_not_overwritten(rb):
    before = (rb.status / f"{V1_ID}.json").read_bytes()
    r = run("rollback", *rbargs(rb, **{"--id": V1_ID}))
    assert r.returncode == 1 and "다른 배포의 상태 파일" in r.stdout and (rb.status / f"{V1_ID}.json").read_bytes() == before and not calls(rb)


def test_rollback_retries_after_same_deployment_failed(rb):
    """(m) 같은 배포 id의 failed 파일은 다시 시도로 registered → switched."""
    status_file(rb, "RB-T1", "failed", "task10@v0-pg-local", "2026-10-24T00:00:00+00:00", version="V0", rev=rb.v0rev, tag="v0")
    r = run("rollback", *rbargs(rb))
    assert r.returncode == 0, r.stdout + r.stderr
    assert rbstatus(rb)["stage"] == "switched"


def test_rollback_failed_restore_check_leaves_host_quiet(rb):
    """restore-check가 멈추면 호스트는 compose를 부르지 않고 종료 1(안쪽 멈춤이 바깥 사용 오류로 바뀌지 않음)."""
    rb.v0cfg.unlink()
    r = run("rollback", *rbargs(rb))
    assert r.returncode == 1 and tools_argv(rb) == ["restore-check"] and not calls(rb)


def test_rollback_missing_status_dir_stops(rb):
    """F-6(답장 019-14 L0): 상태 폴더가 없으면 판단할 수 없음 → 폴더를 만들지 않고 멈춤, compose 0."""
    missing = rb.tmp / "no-such" / "deployments"
    r = run("rollback", *rbargs(rb, **{"--status-dir": str(missing)}))
    assert r.returncode == 1 and "상태 폴더" in r.stdout and not missing.exists() and not calls(rb)


def test_rollback_equal_time_split_switched_stops(rb):
    """F-7(답장 019-14 L0): 같은 시각(시간대만 다름)의 switched가 배포 id로 갈리면 멈춤 — 이름순으로 고르지 않음."""
    (rb.status / f"{V1_ID}.json").unlink()
    status_file(rb, "A-V1", "switched", "task10@v1-pg-local", "2026-10-24T09:00:00+09:00")
    status_file(rb, "B-V0", "switched", "task10@v0-pg-local", "2026-10-24T00:00:00+00:00", version="V0", rev=rb.v0rev, tag="v0")
    r = run("rollback", *rbargs(rb))
    assert r.returncode == 1 and "같은 시각" in r.stdout and rbstatus(rb) is None and not calls(rb)


def test_rollback_equal_time_same_deployment_is_fine(rb):
    """같은 시각이라도 배포 id가 같으면 판단할 수 있음 → 그 배포가 지금 운영(V1) → V0 되돌리기 계속."""
    status_file(rb, "C-V1", "switched", "task10@v1-pg-local", "2026-10-15T03:13:29+00:00")     # 10-15 V1 파일과 같은 시각
    r = run("rollback", *rbargs(rb))
    assert r.returncode == 0 and "지금 운영: task10@v1-pg-local" in r.stdout, r.stdout



# ---------------------------------------------------------------- PG 장부(llmops_pg, D-146): deploy-tools가 runtime_reader DSN 하나로 V-04(시험 DB만)
# deploy-tools처럼 장부 DSN은 프로세스 밖(VPS: /srv/llmopsagent/env/deploy-tools.env → 설정 runtime_db.env_file 자리)에서 온다 — 여기서는 시험 폴더 env 파일.
# 시험 DB = test_logdb.fresh_db(llmopsagent_t_*, 0001 ~ 0006), 공유 DB에는 접속하지 않음. DEPLOY_PG=1(scripts/test-db.sh)에서만 돎.
from test_logdb import _dsn, fresh_db, pg_gate  # noqa: E402,F401
from test_registration import add_ledger_rows  # noqa: E402


def to_pg(cfg, envf):
    doc = yaml.safe_load(cfg.read_text())
    doc["registration"] = {"mode": "llmops_pg"}
    doc["runtime_db"]["env_file"] = str(envf)
    text = cfg.read_text()
    cfg.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False) if "# expect_revision" not in text else
                   yaml.safe_dump(doc, allow_unicode=True, sort_keys=False).replace("  pack_subdir: pack\n", "  pack_subdir: pack\n  # expect_revision: 알림 값을 채움\n"))


@pytest.fixture
def pg_ledger(fresh_db, env, monkeypatch):
    monkeypatch.delenv("LLMOPSAGENT_RUNTIME_DSN", raising=False)
    adm, e_, name = fresh_db
    envf = env.tmp / "deploy-tools.env"
    envf.write_text(f"LLMOPSAGENT_RUNTIME_DSN={_dsn(adm, e_, 'runtime_reader', name)}\n", encoding="utf-8")
    envf.chmod(0o600)
    env.envf, env.db = envf, fresh_db
    return env


@pytest.mark.parametrize("content", ["LLMOPSAGENT_RUNTIME_DSN=postgresql://runtime_reader:fake-a@127.0.0.1:1/x\nLLMOPSAGENT_TRACE_DSN=postgresql://trace_writer:fake-b@127.0.0.1:1/x\n",
                                     "LLMOPSAGENT_TRACE_DSN=postgresql://trace_writer:fake-b@127.0.0.1:1/x\n", None])
def test_ledger_env_with_other_names_or_folder_stops_register_and_rollback(rb, content):
    """deploy-tools 장부 접속 파일(D-146)은 runtime_reader 한 이름만 — 다른 이름이 있거나 파일 자리가 폴더(Docker가 만든 빈 폴더)면
    register · restore-check가 V-04 전에 멈추고 상태 파일을 쓰지 않음. 값은 출력하지 않음(DB 없음, 평소 시험)."""
    envf = rb.tmp / "deploy-tools.env"
    if content is None:
        envf.mkdir()
    else:
        envf.write_text(content)
    for cfg in (rb.cfg, rb.v0cfg):
        to_pg(cfg, envf)
    r = reg(rb)
    assert r.returncode == 1 and status(rb) is None and "멈춤: 장부 접속 파일" in r.stdout
    r2 = run("rollback", *rbargs(rb))
    assert r2.returncode == 1 and rbstatus(rb) is None and "멈춤: 장부 접속 파일" in r2.stdout
    assert not [x for x in calls(rb) if x["argv"][0] in ("build", "up", "stop")]
    assert "fake-a" not in r.stdout + r2.stdout and "fake-b" not in r.stdout + r2.stdout
    if content and "RUNTIME" in content:
        assert "LLMOPSAGENT_TRACE_DSN" in r.stdout                                             # 이름만 알림


def pg_row(dep, rev, commit, tag):
    return (dep, rev, DRS, "0.8.0", commit, tag, "e" * 64, "deploy_approved" if tag == "v1" else "deploy_bootstrap")


@pytest.mark.pg
def test_pg_register_ok_and_missing_row_is_step3(pg_ledger):
    e = pg_ledger
    to_pg(e.cfg, e.envf)
    r = reg(e)
    assert r.returncode == 1 and status(e)["stage"] == "failed" and status(e)["detail"].startswith("배포단 등록 실패 — 장부 줄 없음")   # 줄 없음 → ③
    assert "runtime_reader:" not in r.stdout + r.stderr + json.dumps(status(e), ensure_ascii=False)
    add_ledger_rows(e.db, [pg_row("task10@v1-pg-local", e.rev, e.commit, "v1")])
    (e.status / "CHG-T1.json").unlink()
    r = reg(e)
    assert r.returncode == 0, r.stdout + r.stderr
    st = status(e)
    assert (st["stage"], st["revision"], st["commit"], st["tag"]) == ("registered", e.rev, e.commit, "v1")
    assert "PG 장부(llmops.deployment) — 해당 없음" in r.stdout


@pytest.mark.pg
def test_pg_rollback_restore_check_reads_pg_ledger(rb, fresh_db, monkeypatch):
    monkeypatch.delenv("LLMOPSAGENT_RUNTIME_DSN", raising=False)
    adm, e_, name = fresh_db
    envf = rb.tmp / "deploy-tools.env"
    envf.write_text(f"LLMOPSAGENT_RUNTIME_DSN={_dsn(adm, e_, 'runtime_reader', name)}\n", encoding="utf-8")
    to_pg(rb.v0cfg, envf)
    rb.v0cfg_bytes = rb.v0cfg.read_bytes()
    r = run("rollback", *rbargs(rb))                                                         # PG 장부에 줄이 없으면 ③(SQLite 사본 줄은 쓰지 않음)
    assert r.returncode == 1 and rbstatus(rb)["stage"] == "failed" and "장부 줄 없음" in rbstatus(rb)["detail"]
    assert not [x for x in calls(rb) if x["argv"][0] in ("build", "up", "stop")]              # 서비스는 그대로
    (rb.status / "RB-T1.json").unlink()
    add_ledger_rows(fresh_db, [pg_row("task10@v0-pg-local", rb.v0rev, rb.v0commit, "v0"), pg_row("task10@v1-pg-local", rb.rev, rb.commit, "v1")])
    r = run("rollback", *rbargs(rb))
    assert r.returncode == 0, r.stdout + r.stderr
    st = rbstatus(rb)
    assert (st["stage"], st["deployment_id"], st["revision"], st["commit"], st["tag"]) == ("switched", "task10@v0-pg-local", rb.v0rev, rb.v0commit, "v0")
    assert rb.v0cfg.read_bytes() == rb.v0cfg_bytes and "runtime_reader:" not in r.stdout + r.stderr


# ---------------------------------------------------------------- V-4(D-366 (가)) Pack 캐시 이름 → 읽기 열쇠
def test_v4_pack_key_table_picks_key7_only_for_c1a15_pack(tmp_path, monkeypatch):
    m = _module()
    monkeypatch.setenv("DEPLOY_CHECK_KEY_DIR", str(tmp_path))
    assert m.pack_git_env(tmp_path / "pack-cache" / "task10-pack") == (None, None)              # 표 밖 = compose 열쇠 ② 그대로
    env, bad = m.pack_git_env(tmp_path / "pack-cache" / "c1a15-pack")
    assert env is None and "c1a15_pack_read" in bad and "없음" in bad                               # 열쇠 파일 없음 = 멈춤
    (tmp_path / "c1a15_pack_read").write_text("")
    env, bad = m.pack_git_env(tmp_path / "pack-cache" / "c1a15-pack")
    assert bad is None and env["GIT_SSH_COMMAND"] == (f"ssh -i {tmp_path / 'c1a15_pack_read'} -o IdentitiesOnly=yes "
                                                      "-o StrictHostKeyChecking=yes")             # 그 열쇠 하나만 · 호스트 키 확인 그대로


def _c1a15_cache(env, monkeypatch, key):
    """env 시험 틀의 캐시를 이름만 c1a15-pack으로(같은 원격) · fetch를 기록하는 가짜 git."""
    cache = env.tmp / "pack-cache" / "c1a15-pack"
    env.cache.rename(cache)
    doc = yaml.safe_load(env.cfg.read_text())
    doc["pack"]["repo"] = str(cache)
    env.cfg.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
    keys = env.tmp / "keys"
    keys.mkdir()
    if key:
        (keys / "c1a15_pack_read").write_text("")
    log = env.tmp / "git.log"
    g = env.tmp / "bin" / "fake-git"
    g.write_text(f"#!/bin/sh\necho \"$* | $GIT_SSH_COMMAND\" >> {log}\nexec git \"$@\"\n")
    g.chmod(0o755)
    monkeypatch.setenv("DEPLOY_CHECK_GIT", str(g))
    monkeypatch.setenv("DEPLOY_CHECK_KEY_DIR", str(keys))
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /run/keys/pack_read -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes")   # compose 값(열쇠 ②)
    return log, keys


def test_v4_register_fetches_c1a15_pack_with_key7(env, monkeypatch):
    log, keys = _c1a15_cache(env, monkeypatch, key=True)
    r = reg(env)
    assert "[1/5] 태그 받기: c1a15-pack fetch --tags(열쇠 c1a15_pack_read)" in r.stdout, r.stdout + r.stderr
    fetch = [l for l in log.read_text().splitlines() if " fetch --tags origin" in l]
    assert fetch and fetch[0].endswith(f"| ssh -i {keys / 'c1a15_pack_read'} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes")
    assert "pack_read -o" not in fetch[0].split("|")[1].replace("c1a15_pack_read", "")             # 열쇠 ②는 주지 않음(GitHub 첫 열쇠 문제)


def test_v4_register_without_key7_stops_before_fetch(env, monkeypatch):
    log, _ = _c1a15_cache(env, monkeypatch, key=False)
    r = reg(env)
    assert r.returncode == 1 and "멈춤: c1a15-pack 읽기 열쇠" in r.stdout and "[2/5]" not in r.stdout, r.stdout + r.stderr
    assert status(env) is None and not log.exists()                                             # 상태 파일 · fetch 0


def test_v4_task10_fetch_keeps_compose_key2(env, monkeypatch):
    log = env.tmp / "git.log"
    g = env.tmp / "bin" / "fake-git"
    g.write_text(f"#!/bin/sh\necho \"$* | $GIT_SSH_COMMAND\" >> {log}\nexec git \"$@\"\n")
    g.chmod(0o755)
    monkeypatch.setenv("DEPLOY_CHECK_GIT", str(g))
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /run/keys/pack_read -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes")
    ledger_rows(env, [("task10@v1-pg-local", env.rev, DRS, env.commit, "v1")])
    r = reg(env)
    assert r.returncode == 0 and "fetch --tags\n" in r.stdout.replace("fetch --tags(", "X"), r.stdout
    fetch = [l for l in log.read_text().splitlines() if " fetch --tags origin" in l]
    assert fetch and fetch[0].endswith("| ssh -i /run/keys/pack_read -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes")


# ---------------------------------------------------------------- D-375 reconfigure — 같은 서비스 · 같은 장부 줄에서 운영 설정만 바꿈
def _reconf_setup(env, server, change=True, dep=None):
    registered(env)                                                            # 운영 설정에 expect_revision이 들어간 뒤만
    live = env.cfg.read_text()
    tmpl = env.tmp / "tmpl-b.yaml"
    text = re.sub(r'^  expect_revision: .*$', "  # expect_revision: register가 채움", live, count=1, flags=re.M)
    if change:
        text = text.replace("review_policy: auto_approve", "review_policy: auto_approve   # 단계 B 시험: 바뀐 줄")
    if dep:
        text = text.replace("deployment_id: task10@v1-pg-local", f"deployment_id: {dep}")
    tmpl.write_text(text)
    case = env.tmp / "c01.json"
    case.write_text(json.dumps({"event_id": "EVT-R"}))
    Fake.infos = [{"deployment_id": "task10@v1-pg-local", "pack": {"pack_revision": env.rev}, "data_release_id": DRS}]
    env.calls.unlink(missing_ok=True)
    return live, tmpl, ["--service", "deploy-svc", "--config", str(env.cfg), "--template", str(tmpl), "--case-file", str(case), "--url", server, "--wait", "5"]


def _before(env):
    return env.cfg.with_name(f".{env.cfg.name}.before-reconfigure")


def test_reconfigure_ok_changes_live_restarts_once_and_checks_c01(env, server):
    live, tmpl, args = _reconf_setup(env, server)
    r = run("reconfigure", *args)
    assert r.returncode == 0, r.stdout + r.stderr
    assert [x["argv"] for x in calls(env)] == [["restart", "deploy-svc"]]                 # 서비스 restart 한 번(새 서비스 · build · up 0)
    now = env.cfg.read_text()
    assert "단계 B 시험: 바뀐 줄" in now and f'expect_revision: "{env.rev}"' in now           # 템플릿 + 지금 expect_revision
    assert _before(env).read_text() == live                                                # 옛 글 보관
    assert Fake.bodies[-1] == {"event_id": "EVT-R"} and "[4/4] 결과: 운영 설정 바꿈 반영" in r.stdout
    assert status(env)["stage"] == "registered"                                            # 상태 파일 손 0(같은 버전)
    assert not list(env.cfg.parent.glob("*.reconfigure-candidate"))


def test_reconfigure_same_text_does_nothing(env, server):
    live, tmpl, args = _reconf_setup(env, server, change=False)
    r = run("reconfigure", *args)
    assert r.returncode == 0 and "이미 템플릿과 같음" in r.stdout, r.stdout + r.stderr
    assert calls(env) == [] and env.cfg.read_text() == live and not _before(env).exists()


def test_reconfigure_binding_check_failure_keeps_live(env, server):
    live, tmpl, args = _reconf_setup(env, server)
    r = run("reconfigure", *args, extra_env={"FAKE_BIND_FAIL": "1"})
    assert r.returncode == 1 and "묶기 대조 실패" in r.stdout, r.stdout + r.stderr
    assert calls(env) == [] and env.cfg.read_text() == live and not _before(env).exists()


def test_reconfigure_other_deployment_id_stops_before_writing(env, server):
    live, tmpl, args = _reconf_setup(env, server, dep="task10@other-local")
    r = run("reconfigure", *args)
    assert r.returncode == 1 and "배포 id이 운영 설정과 다름" in r.stdout, r.stdout + r.stderr
    assert calls(env) == [] and env.cfg.read_text() == live


def test_reconfigure_drs_changed_after_restart_reverts(env, server):
    live, tmpl, args = _reconf_setup(env, server)
    a = Fake.infos[0]
    Fake.infos = [a, {**a, "data_release_id": "drs:ffffffffffffffff"}]                    # 다시 뜬 서버가 다른 장부 줄(drs)
    r = run("reconfigure", *args)
    assert r.returncode == 1 and "같은 장부 줄이 아님" in r.stdout and "옛 설정으로 되돌려 다시 띄움" in r.stdout, r.stdout + r.stderr
    assert [x["argv"] for x in calls(env)] == [["restart", "deploy-svc"], ["restart", "deploy-svc"]]
    assert env.cfg.read_text() == live


def test_reconfigure_c01_failure_reverts(env, server):
    live, tmpl, args = _reconf_setup(env, server)
    Fake.code = 503
    r = run("reconfigure", *args)
    assert r.returncode == 1 and "C01 응답 503" in r.stdout, r.stdout + r.stderr
    assert env.cfg.read_text() == live and len(calls(env)) == 2


def test_reconfigure_service_down_does_nothing(env):
    registered(env)
    case = env.tmp / "c01.json"
    case.write_text("{}")
    env.calls.unlink(missing_ok=True)
    before = env.cfg.read_text()
    r = run("reconfigure", "--service", "deploy-svc", "--config", str(env.cfg), "--template", str(env.cfg), "--case-file", str(case),
            "--url", "http://127.0.0.1:9", "--wait", "1")
    assert r.returncode == 1 and "응답하지 않음" in r.stdout and calls(env) == [] and env.cfg.read_text() == before


def test_reconfigure_needs_registered_live_config(env, server):
    case = env.tmp / "c01.json"
    case.write_text("{}")
    Fake.infos = [{"deployment_id": "task10@v1-pg-local", "pack": {"pack_revision": env.rev}, "data_release_id": DRS}]
    r = run("reconfigure", "--service", "deploy-svc", "--config", str(env.cfg), "--template", str(env.cfg), "--case-file", str(case),
            "--url", server, "--wait", "1")
    assert r.returncode == 1 and "expect_revision이 없음" in r.stdout, r.stdout + r.stderr   # register 전 설정은 바꾸지 않음
