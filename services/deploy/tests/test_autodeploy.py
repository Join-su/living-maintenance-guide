"""scripts/autodeploy.py 가짜 시험(D-455 감시 · 저장소로 옮김 D-470) — 서버 · 장부 · Pack 도구 모두 가짜, 네트워크 · DB 0.
기록 git 판 시험 10을 옮기고, 장부 읽기가 서버와 같은 ledger_rows를 부르는지 · 도구 경로 없으면 켜지 않는지를 더함."""
import importlib.util
import json
import os
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OLD = "sha256:" + "4" * 64
NEW = "sha256:" + "3" * 64
DRS = "drs:57c80b829770c0c1"
CFG_TEXT = f'deployment_id: c1a15@dev-local\npack:\n  ref: v12                # 옛\n  expect_revision: "{OLD}"\n'


def load():
    spec = importlib.util.spec_from_file_location("autodeploy_under_test", ROOT / "scripts" / "autodeploy.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Fake:
    def __init__(self, A, dry=("dry_run_ok", 0), apply=("tagged", 0)):
        self.A, self.dry, self.apply, self.calls = A, dry, apply, []

    def sh(self, args, **kw):
        A = self.A
        a = " ".join(map(str, args))
        self.calls.append(a)
        if str(A.TOOL) in a:
            res, rc = self.apply if "--apply" in args else self.dry
            if "--apply" in args and rc == 0:
                cid = Path(args[3]).stem
                (A.PACK / "status" / "approved-changes").mkdir(parents=True, exist_ok=True)
                (A.PACK / "status" / "approved-changes" / f"{cid}.json").write_text(json.dumps(
                    {"id": cid, "stage": "tagged", "revision": NEW, "commit": "a" * 40, "tag": "v13"}))
            return types.SimpleNamespace(returncode=rc, stdout="start 기대값 바뀐 case 1\n" + json.dumps({"result": res}), stderr="")
        if "deploy-status.py" in a:
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({"stage": args[2]}), stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def A(tmp_path, monkeypatch):
    m = load()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "c1a15-dev-local.yaml").write_text(CFG_TEXT)
    (tmp_path / "req").mkdir()
    tool = tmp_path / "tool.py"
    tool.write_text("# --by\n")
    for k, v in {"ROOT": tmp_path, "REQ": tmp_path / "req", "PACK": tmp_path / "pack", "STATE": tmp_path / "state", "TOOL": tool,
                 "WAIT": 0.3, "POLL": 0.05, "STOP": False, "TOLD": set()}.items():
        monkeypatch.setattr(m, k, v)
    m.fake = Fake(m)
    m.switched = []
    m.ledger_has_new = True
    monkeypatch.setattr(m, "sh", m.fake.sh)
    monkeypatch.setattr(m, "llmops_state", lambda: (_ for _ in ()).throw(OSError("시험엔 LLMOps 없음")))   # 결정자 못 읽음 = 지금처럼 · 운영 8766에 닿지 않게
    monkeypatch.setattr(m, "ledger", lambda: [("v13", NEW, DRS)] if m.ledger_has_new else [("v12", OLD, DRS)])
    monkeypatch.setattr(m, "load_settings", lambda p: types.SimpleNamespace(deployment_id="c1a15@dev-local"))
    m.real_switch = m.switch
    monkeypatch.setattr(m, "switch", lambda cid, version, tag, rev, drs: m.switched.append((cid, tag, rev, drs)) or "run_x")
    return m


def req(A, cid, **kw):
    d = {"format": A.FORMAT, "change_id": cid, "version": "V8", "candidate_revision": NEW, "base_revision": OLD, **kw}
    (A.REQ / f"{cid}.json").write_text(json.dumps(d))


def deploy_calls(A, cmd):
    return [c for c in A.fake.calls if f"deploy-status.py {cmd}" in c]


def test_baseline_ignored_new_processed(A):
    req(A, "CHG-c1a15-V7-old")
    base = {p.name: p.stat().st_mtime_ns for p in A.REQ.glob("*.json")}
    req(A, "CHG-c1a15-V8-new")
    req(A, "CHG-task10-V2-x")
    req(A, "CHG-c1a15-V9-dry", dry_run=True)
    assert [p.stem for p in A.pick(base)] == ["CHG-c1a15-V8-new"]


def test_baseline_name_rewritten_is_new(A):
    req(A, "CHG-c1a15-V8-new")
    base = {p.name: p.stat().st_mtime_ns - 1 for p in A.REQ.glob("*.json")}   # 켠 뒤 다시 써짐
    assert [p.stem for p in A.pick(base)] == ["CHG-c1a15-V8-new"]
    (A.ROOT / "status" / "deployments").mkdir(parents=True)
    (A.ROOT / "status" / "deployments" / "CHG-c1a15-V8-new.json").write_text("{}")
    assert A.pick(base) == []   # deploy 상태 파일 있으면 손 0


def test_full_chain_and_config(A):
    req(A, "CHG-c1a15-V8-new")
    A.run(once=True, baseline={})
    assert A.switched == [("CHG-c1a15-V8-new", "v13", NEW, DRS)]
    assert any("es3_check.py" in c and "--tag v13" in c and "--commit " + "a" * 40 in c and f"--drs {DRS}" in c for c in A.fake.calls)
    assert all("--by 자동 감시" in c and "--branch demo-nano" in c for c in A.fake.calls if str(A.TOOL) in c)
    txt = (A.ROOT / "config" / "c1a15-dev-local.yaml").read_text()
    assert "  ref: v13 " in txt and NEW in txt


def test_same_id_twice_zero(A):
    req(A, "CHG-c1a15-V8-new")
    A.run(once=True, baseline={})
    n = len(A.fake.calls)
    os.utime(A.REQ / "CHG-c1a15-V8-new.json")
    A.run(once=True, baseline={})   # 다시 써져도 done 표시 — 손 0
    assert len(A.fake.calls) == n
    assert not A.claim("CHG-c1a15-V8-new")
    assert A.pick({}) == []


def test_dry_run_refused_stops_before_apply(A):
    A.fake.dry = ("refused", 1)
    req(A, "CHG-c1a15-V8-new")
    A.run(once=True, baseline={})
    assert not any("--apply" in c for c in A.fake.calls)
    assert not any("es3_check" in c for c in A.fake.calls)
    assert A.switched == []


def test_format_error_marks_deploy_failed(A):
    A.fake.dry = ("format_error", 2)
    req(A, "CHG-c1a15-V8-new")
    A.run(once=True, baseline={})
    assert any("format_error" in c for c in deploy_calls(A, "failed"))


def test_already_applied_goes_on(A):
    A.fake.dry, A.fake.apply = ("already_applied_dry_run", 0), ("already_applied", 0)
    req(A, "CHG-c1a15-V8-new")
    A.run(once=True, baseline={})
    assert len(A.switched) == 1


def test_ledger_timeout_message(A):
    A.ledger_has_new = False
    req(A, "CHG-c1a15-V8-new")
    A.run(once=True, baseline={})
    assert any("LLMOps 화면" in c for c in deploy_calls(A, "failed"))
    assert not any("es3_check" in c for c in A.fake.calls)


def test_stop_between_steps(A, monkeypatch):
    orig = A.pack_tool

    def tool_then_stop(r, apply):
        out = orig(r, apply)
        A.STOP = True
        return out
    monkeypatch.setattr(A, "pack_tool", tool_then_stop)
    req(A, "CHG-c1a15-V8-new")
    A.run(once=True, baseline={})
    assert any("감시 꺼짐" in c for c in deploy_calls(A, "failed"))
    assert not any("--apply" in c for c in A.fake.calls)


def test_switch_same_revision_no_restart(A, monkeypatch):
    monkeypatch.setattr(A, "listener", lambda: "123")
    monkeypatch.setattr(A, "info_line", lambda: ({"tag": "v13"}, NEW))
    monkeypatch.setattr(A, "get", lambda path, timeout=5: {"data_release_id": DRS})
    monkeypatch.setattr(A, "post_start", lambda: "run_y")
    monkeypatch.setattr(A, "outbound", lambda pid: None)
    assert A.real_switch("CHG-c1a15-V8-new", "V8", "v13", NEW, DRS) == "run_y"
    assert not any("r12-update" in c or c.startswith("kill") for c in A.fake.calls)
    assert len(deploy_calls(A, "switched")) == 1


def test_ledger_reads_with_server_function(tmp_path, monkeypatch):
    """장부 읽기 = registration.ledger_rows(설정 registration · 배포 id · runtime_reader DSN) — 초관리자 docker exec 0 · 닿지 못하면 빈 목록."""
    m = load()
    s = types.SimpleNamespace(registration={"mode": "llmops_pg"}, deployment_id="c1a15@dev-local")
    seen = []
    monkeypatch.setattr(m, "load_settings", lambda p: s)
    monkeypatch.setattr(m, "ledger_dsn", lambda settings: "dsn-not-printed")
    monkeypatch.setattr(m, "ledger_rows", lambda reg, dep, dsn: seen.append((reg, dep, dsn)) or [
        {"pack_tag": "v13", "pack_revision": NEW, "data_release_id": DRS, "pack_commit": "a" * 40}])
    assert m.ledger_row(NEW) == ("v13", DRS)
    assert m.ledger_row(OLD) is None
    assert seen[0] == ({"mode": "llmops_pg"}, "c1a15@dev-local", "dsn-not-printed")

    def boom(reg, dep, dsn):
        raise m.LedgerUnavailable("OperationalError")
    monkeypatch.setattr(m, "ledger_rows", boom)
    assert m.ledger() == []
    assert "docker" not in (ROOT / "scripts" / "autodeploy.py").read_text().split('"""', 2)[2]


def test_tool_default_is_pack_repo_tool(monkeypatch):
    """c1a15-pack 013-76: 도구가 저장소 코드로(tools/change/receive_approved.py) → AD_PACK_TOOL 없으면 그 경로 · --branch는 늘 넘김."""
    monkeypatch.delenv("AD_PACK_TOOL", raising=False)
    m = load()
    assert m.TOOL == m.PACK / "tools" / "change" / "receive_approved.py"
    monkeypatch.setenv("AD_PACK_TOOL", "/elsewhere/tool.py")
    assert load().TOOL == Path("/elsewhere/tool.py")


def test_no_tool_refuses_start(tmp_path, monkeypatch):
    m = load()
    monkeypatch.setattr(m, "TOOL", tmp_path / "없음.py")
    with pytest.raises(SystemExit, match="AD_PACK_TOOL"):
        m.need_tool()
    monkeypatch.setattr(m, "TOOL", tmp_path / "없음.py")
    monkeypatch.setattr(m, "STATE", tmp_path / "state")
    with pytest.raises(SystemExit, match="AD_PACK_TOOL"):
        m.start()
    assert not (tmp_path / "state").exists()   # 거절 때 상태 폴더 · pid 파일 손 0


def test_d645_waits_for_the_tagged_ledger_row_and_rechecks_before_es3(A, monkeypatch):
    """D-645(packrt-1 230): ③ 앞 — 장부 줄은 revision만이 아니라 ② 태그까지 같은 줄을 기다리고, es3 대조는 --no-status로 다시 물어
    통과할 때 한 번만 진짜 es3_check(상태 registered). 실패 까닭 줄은 감시 기록에 남김."""
    rows = iter([[("v12-앞시도", NEW, DRS)], [("v12-앞시도", NEW, DRS)], [("v12-앞시도", NEW, DRS), ("v13", NEW, DRS)]])
    last = [[]]
    monkeypatch.setattr(A, "ledger", lambda: last.__setitem__(0, next(rows, last[0])) or last[0])
    tries = []
    real = A.fake.sh

    def sh(args, **kw):
        a = " ".join(map(str, args))
        if "es3_check.py" in a and "--no-status" in a:
            tries.append(a)
            A.fake.calls.append(a)
            ok = len(tries) >= 2
            return types.SimpleNamespace(returncode=0 if ok else 1, stdout="V-04 통과" if ok else "장부 줄 tag가 v13이 아님: '앞'", stderr="")
        return real(args, **kw)
    monkeypatch.setattr(A, "sh", sh)
    monkeypatch.setattr(A, "ES3_WAIT", 1.0)
    said = []
    monkeypatch.setattr(A, "say", lambda *a: said.append(" ".join(map(str, a))))
    req(A, "CHG-c1a15-V8-new")
    A.run(once=True, baseline={})
    real_es3 = [c for c in A.fake.calls if "es3_check.py" in c and "--no-status" not in c]
    assert len(tries) == 2 and len(real_es3) == 1 and "--tag v13" in real_es3[0]
    assert A.switched == [("CHG-c1a15-V8-new", "v13", NEW, DRS)]
    assert any("장부 줄 tag가 v13이 아님" in s for s in said)                 # 실패 까닭 줄이 감시 기록에


def test_d645_es3_still_failing_after_wait_stops_once(A, monkeypatch):
    real = A.fake.sh

    def sh(args, **kw):
        a = " ".join(map(str, args))
        if "es3_check.py" in a:
            A.fake.calls.append(a)
            return types.SimpleNamespace(returncode=1, stdout="장부 줄 drs 다름: x / 예상 y", stderr="")
        return real(args, **kw)
    monkeypatch.setattr(A, "sh", sh)
    monkeypatch.setattr(A, "ES3_WAIT", 0.2)
    req(A, "CHG-c1a15-V8-new")
    A.run(once=True, baseline={})
    real_es3 = [c for c in A.fake.calls if "es3_check.py" in c and "--no-status" not in c]
    assert len(real_es3) == 1 and A.switched == []                              # 진짜 es3_check(실패 기록)는 한 번


def test_d645_deploy_status_refuses_demo_config_without_status_dir(tmp_path):
    """packrt-1 232 재현: 실제 원장(박아 둔 llmops.db)이 아닌 설정이면 deploy-status는 --status-dir 없이 거절(종료 2) — 감시가 안 넘기면 ③이 늘 멈춤."""
    import subprocess
    import sys
    cmd = [sys.executable, "-B", str(ROOT / "scripts" / "deploy-status.py"), "registered", "--id", "CHG-c1a15-V9-x", "--version", "V9",
           "--config", str(ROOT / "config" / "c1a15-dev2-demo.yaml")]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, env={"PATH": "/usr/bin:/bin", "HOME": os.environ["HOME"], "PYTHONPATH": str(ROOT)})
    assert r.returncode == 2 and "--status-dir를 따로" in r.stderr


def test_d645_watcher_passes_its_status_dir_to_deploy_status_and_es3(A):
    req(A, "CHG-c1a15-V8-new")
    A.run(once=True, baseline={})
    sd = f"--status-dir {A.ROOT / 'status' / 'deployments'}"
    es3 = [c for c in A.fake.calls if "es3_check.py" in c]
    ds = [c for c in A.fake.calls if "deploy-status.py" in c]
    assert es3 and all(sd in c for c in es3)
    assert A.switched and all(sd in c for c in ds)


def test_d645_status_dir_env_override(A, monkeypatch, tmp_path):
    monkeypatch.setenv("AD_STATUS_DIR", str(tmp_path / "llmops-status"))
    A.failed_ok = True
    try:
        A.failed("CHG-c1a15-V8-x", "V8", "시험")
    except A.Stop:
        pass
    assert any(f"--status-dir {tmp_path / 'llmops-status'}" in c for c in A.fake.calls if "deploy-status.py failed" in c)
