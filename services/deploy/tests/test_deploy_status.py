"""배포 상태 파일 도구 시험(지시 010, VFE EV-002 CHG-08 · V-19): status/deployments/<id>.json — registered · switched · failed.
등록 원장은 시험 폴더 SQLite(llmops.db deployment 칸 모양), 실행 요약은 시험 폴더 RunStore. 실제 llmops.db·서버·DB 없음."""
import hashlib
import importlib.util
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

from deploy_server.stores import RunStore

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("deploy_status", ROOT / "scripts" / "deploy-status.py")
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)

REV = "sha256:" + "3e315c04" + "a" * 56
OTHER = "sha256:" + "877b0ed5" + "b" * 56
COMMIT = "88c5e0cf" + "c" * 32
FIELDS = ["id", "version", "revision", "stage", "detail", "commit", "tag", "deployment_id", "updated_at"]


def registry(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE deployment (deployment_id TEXT, pack_revision TEXT, data_release_id TEXT, pack_commit TEXT, pack_tag TEXT)")
    con.executemany("INSERT INTO deployment VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return path


@pytest.fixture
def env(tmp_path):
    """시험 설정(v1-pg-local 모양, 등록·데이터 폴더만 시험 폴더) + 등록 원장 + 상태 폴더."""
    reg = registry(tmp_path / "llmops.db", [("task10@v0-pg-local", OTHER, "drs:54e2537743f7fa9c", "93ca0a1", "v0"),
                                           ("task10@v1-pg-local", REV, "drs:54e2537743f7fa9c", COMMIT, "v1")])
    doc = yaml.safe_load((ROOT / "config" / "v1-pg-local.yaml").read_text())
    doc["pack"]["expect_revision"] = REV
    doc["registration"] = {"mode": "llmops_db", "path": str(reg)}   # 시험 장부 = 시험 폴더 SQLite(저장소 설정은 A5 뒤 llmops_pg — 답장 031-01)
    doc["data_dir"] = str(tmp_path / "data")
    cfg = tmp_path / "v1-test.yaml"
    cfg.write_text(yaml.safe_dump(doc, allow_unicode=True))

    class E:
        pass
    e = E()
    e.reg, e.cfg, e.dir, e.data, e.doc, e.tmp = reg, cfg, tmp_path / "status", tmp_path / "data", doc, tmp_path
    e.run = lambda *a: ds.main([*a, "--status-dir", str(e.dir)])
    e.read = lambda cid="CHG-T1": json.loads((e.dir / f"{cid}.json").read_text())
    return e


def save_run(e, run_id="run_01TEST", **over):
    rec = {"run_id": run_id, "status": "succeeded", "deployment_id": "task10@v1-pg-local",
           "pack": {"pack_id": "task10", "pack_revision": REV, "commit": COMMIT, "tag": "v1"}}
    rec.update(over)
    RunStore(e.data / "runs").save(rec)
    return run_id


def args(e, cmd, cid="CHG-T1"):
    return [cmd, "--id", cid, "--version", "V1", "--config", str(e.cfg)]


def test_registered_writes_common_format(env):
    assert env.run(*args(env, "registered")) == 0
    rec = env.read()
    assert list(rec) == FIELDS
    assert (rec["stage"], rec["revision"], rec["commit"], rec["tag"], rec["deployment_id"], rec["version"]) == \
           ("registered", REV, COMMIT, "v1", "task10@v1-pg-local", "V1")
    assert "3e315c04" in rec["detail"] and "54e25377" in rec["detail"]
    assert rec["updated_at"][:4].isdigit() and ("+" in rec["updated_at"][19:] or rec["updated_at"].endswith("Z"))


@pytest.mark.parametrize("case", ["other_revision", "no_row", "empty_expect", "no_db"])
def test_registered_mismatch_writes_failed(env, case):
    if case == "other_revision":
        env.doc["pack"]["expect_revision"] = "sha256:" + "d" * 64
    elif case == "no_row":
        env.doc["deployment_id"] = "task10@v9-pg-local"
    elif case == "empty_expect":
        env.doc["pack"]["expect_revision"] = ""
    else:
        env.doc["registration"]["path"] = str(env.tmp / "missing.db")
    env.cfg.write_text(yaml.safe_dump(env.doc, allow_unicode=True))
    assert env.run(*args(env, "registered")) == 1
    rec = env.read()
    assert list(rec) == FIELDS and rec["stage"] == "failed" and rec["detail"]


def test_empty_expect_revision_failed_has_null_revision(env):
    env.doc["pack"]["expect_revision"] = ""
    env.cfg.write_text(yaml.safe_dump(env.doc, allow_unicode=True))
    assert env.run(*args(env, "registered")) == 1
    assert env.read()["revision"] is None


def test_wal_ledger_leaves_no_side_files(env):
    wal = env.tmp / "wal" / "llmops.db"
    wal.parent.mkdir()
    con = sqlite3.connect(wal)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE deployment (deployment_id TEXT, pack_revision TEXT, data_release_id TEXT, pack_commit TEXT, pack_tag TEXT)")
    con.execute("INSERT INTO deployment VALUES (?,?,?,?,?)", ("task10@v1-pg-local", REV, "drs:54e2537743f7fa9c", COMMIT, "v1"))
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    for side in ("-wal", "-shm"):
        Path(f"{wal}{side}").unlink(missing_ok=True)
    before = hashlib.sha256(wal.read_bytes()).hexdigest()
    env.doc["registration"]["path"] = str(wal)
    env.cfg.write_text(yaml.safe_dump(env.doc, allow_unicode=True))
    assert env.run(*args(env, "registered")) == 0
    assert env.read()["commit"] == COMMIT
    assert sorted(p.name for p in wal.parent.iterdir()) == ["llmops.db"]
    assert hashlib.sha256(wal.read_bytes()).hexdigest() == before


def test_switched_again_only_for_same_run(env):
    env.run(*args(env, "registered"))
    rid = save_run(env)
    assert env.run(*args(env, "switched"), "--run-id", rid) == 0
    assert env.run(*args(env, "switched"), "--run-id", rid) == 0
    other = save_run(env, run_id="run_01OTHER")
    assert env.run(*args(env, "switched"), "--run-id", other) == 1
    assert env.read()["stage"] == "failed"


def test_bad_config_or_status_json_is_usage_error(env):
    assert ds.main(["registered", "--id", "CHG-T1", "--version", "V1", "--config", str(env.tmp / "none.yaml"), "--status-dir", str(env.dir)]) == 2
    env.dir.mkdir()
    (env.dir / "CHG-T1.json").write_text("{broken")
    assert env.run(*args(env, "switched"), "--run-id", save_run(env)) == 2
    assert (env.dir / "CHG-T1.json").read_text() == "{broken"


def test_registration_ledger_is_read_only(env):
    before = hashlib.sha256(env.reg.read_bytes()).hexdigest()
    env.run(*args(env, "registered"))
    assert hashlib.sha256(env.reg.read_bytes()).hexdigest() == before
    assert not list(env.tmp.glob("llmops.db-*"))   # -wal·-shm·-journal 없음


def test_switched_after_registered(env):
    env.run(*args(env, "registered"))
    rid = save_run(env)
    assert env.run(*args(env, "switched"), "--run-id", rid) == 0
    rec = env.read()
    assert list(rec) == FIELDS
    assert (rec["stage"], rec["revision"], rec["commit"], rec["tag"]) == ("switched", REV, COMMIT, "v1")
    assert rid in rec["detail"] and "태그 v1" in rec["detail"]


def test_switched_without_registered_fails(env):
    rid = save_run(env)
    assert env.run(*args(env, "switched"), "--run-id", rid) == 1
    assert env.read()["stage"] == "failed"


@pytest.mark.parametrize("over", [{"deployment_id": "task10@v0-pg-local"}, {"status": "failed"},
                                  {"pack": {"pack_id": "task10", "pack_revision": OTHER, "commit": COMMIT, "tag": "v0"}}])
def test_switched_mismatch_fails(env, over):
    env.run(*args(env, "registered"))
    rid = save_run(env, **over)
    assert env.run(*args(env, "switched"), "--run-id", rid) == 1
    rec = env.read()
    assert rec["stage"] == "failed" and rid in rec["detail"]


def test_switched_unknown_run_fails(env):
    env.run(*args(env, "registered"))
    assert env.run(*args(env, "switched"), "--run-id", "run_01NONE") == 1
    assert env.read()["stage"] == "failed"


def test_failed_keeps_known_values(env):
    env.run(*args(env, "registered"))
    assert env.run(*args(env, "failed"), "--detail", "v1 서버가 시작하지 않음") == 1
    rec = env.read()
    assert (rec["stage"], rec["detail"], rec["commit"], rec["tag"]) == ("failed", "v1 서버가 시작하지 않음", COMMIT, "v1")


@pytest.mark.parametrize("detail", ["postgresql://u:x@h/db", "PASSWORD=x", "두 줄\n입니다", "", "가" * 201])
def test_failed_detail_guard_writes_nothing(env, detail):
    assert env.run(*args(env, "failed"), "--detail", detail) == 2
    assert not env.dir.exists() or not list(env.dir.iterdir())


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x y", "x" * 65, "abc\n"])
def test_bad_id_writes_nothing(env, bad):
    assert ds.main(["registered", "--id", bad, "--version", "V1", "--config", str(env.cfg), "--status-dir", str(env.dir)]) == 2
    assert not env.dir.exists()


def test_no_temp_files_left(env):
    env.run(*args(env, "registered"))
    env.run(*args(env, "switched"), "--run-id", save_run(env))
    assert sorted(p.name for p in env.dir.iterdir()) == ["CHG-T1.json"]


def test_non_real_config_needs_own_status_dir(env):
    rh = env.tmp / "v1-pg-rehearsal.yaml"
    rh.write_text(env.cfg.read_text())
    real = dict(env.doc, registration={"mode": "llmops_db", "path": str(ds.REAL_LLMOPS_DB)})
    rh_real = env.tmp / "x-rehearsal.yaml"   # 이름에 rehearsal + 실제 원장 경로 — 이름만으로도 거부(원장을 열기 전)
    rh_real.write_text(yaml.safe_dump(real, allow_unicode=True))
    for cfg in (rh, env.cfg, rh_real):   # 이름에 rehearsal · 등록 경로가 실제 llmops.db가 아님
        assert ds.main(["registered", "--id", "CHG-T1", "--version", "V1", "--config", str(cfg)]) == 2
    assert not (ROOT / "status" / "deployments" / "CHG-T1.json").exists()


def test_real_ledger_judgement_uses_absolute_path(monkeypatch):
    from deploy_server.settings import load_settings
    monkeypatch.delenv("LLMOPSAGENT_RUNTIME_DSN", raising=False)   # 셸 환경 변수에 기대지 않게(그 변수가 있으면 PG 장부는 실제가 아님 — 답장 031-01)
    assert ds.uses_real_ledger(load_settings(ROOT / "config" / "v1-pg-local.yaml"))
    assert ds.uses_real_ledger(load_settings(ROOT / "config" / "v0-pg-local.yaml"))
    assert not ds.uses_real_ledger(load_settings(ROOT / "config" / "v1-pg-rehearsal.yaml"))


def test_real_pg_ledger_is_the_repo_env_only(tmp_path):   # 답장 031-01: Mac pg-local(이 저장소 .env) = 실제, VPS · 다른 env 파일 = 아님
    from deploy_server.settings import load_settings
    assert all(load_settings(ROOT / "config" / f"{n}.yaml").registration == {"mode": "llmops_pg"} for n in ("v1-pg-local", "v0-pg-local"))
    assert not ds.uses_real_ledger(load_settings(ROOT / "config" / "v1-pg-vps.yaml"))      # /run/deploy/deploy.env — VPS는 늘 --status-dir
    doc = yaml.safe_load((ROOT / "config" / "v1-pg-local.yaml").read_text(encoding="utf-8"))
    doc["runtime_db"] = {"env_file": str(tmp_path / "other.env")}
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True))
    assert not ds.uses_real_ledger(load_settings(tmp_path / "c.yaml"))


def test_runtime_dsn_env_var_makes_pg_local_not_real(monkeypatch):   # 환경 변수 DSN이 먼저 쓰이므로 실제 장부라고 보지 않음(--status-dir 필요)
    from deploy_server.settings import load_settings
    s = load_settings(ROOT / "config" / "v1-pg-local.yaml")
    monkeypatch.delenv("LLMOPSAGENT_RUNTIME_DSN", raising=False)
    assert ds.uses_real_ledger(s)
    monkeypatch.setenv("LLMOPSAGENT_RUNTIME_DSN", "postgresql://x@127.0.0.1:1/none")
    assert not ds.uses_real_ledger(s)


def test_env_file_value_never_reaches_output(env, capsys):
    fake = "postgresql://runtime_reader:SECRETVALUE9@127.0.0.1:5432/llmopsagent"
    (env.tmp / ".env").write_text(f"LLMOPSAGENT_RUNTIME_DSN={fake}\nLLMOPSAGENT_TRACE_DSN={fake}\n")
    env.doc["runtime_db"]["env_file"] = str(env.tmp / ".env")
    env.doc["trace"]["env_file"] = str(env.tmp / ".env")
    env.cfg.write_text(yaml.safe_dump(env.doc, allow_unicode=True))
    env.run(*args(env, "registered"))
    env.run(*args(env, "switched"), "--run-id", save_run(env))
    env.run(*args(env, "failed"), "--detail", "멈춤")
    out = capsys.readouterr()
    assert "SECRETVALUE9" not in out.out + out.err
    assert "SECRETVALUE9" not in (env.dir / "CHG-T1.json").read_text()


def test_status_folder_is_git_ignored():
    r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", "status/deployments/CHG-T1.json"])
    assert r.returncode == 0


@pytest.mark.parametrize("case", ["no_row", "no_db"])
def test_registered_failure_detail_names_step3(env, case):
    """EV-002 v12(V-24): 등록 대조 실패는 화면 ③ 글귀로 시작하고 '전환'이 없음(llmops는 '전환'이 있으면 ④로 표시)."""
    if case == "no_row":
        env.doc["pack"]["expect_revision"] = OTHER
        env.cfg.write_text(yaml.safe_dump(env.doc, allow_unicode=True))
    else:
        env.reg.unlink()
    assert env.run(*args(env, "registered")) == 1
    detail = env.read()["detail"]
    assert detail.startswith("배포단 등록 실패 — ") and "전환" not in detail


def test_switched_failure_detail_names_step4(env):
    """EV-002 v12(V-24): 운영 전환 실패는 화면 ④ 글귀로 시작."""
    env.run(*args(env, "registered"))
    assert env.run(*args(env, "switched"), "--run-id", "run_01NONE") == 1
    assert env.read()["detail"].startswith("운영 전환 실패 — ")
