"""백업 스크립트 scripts/vps/backup · pull-backup 가짜 경계 시험(VFE EV-005 ES-C, V-34).
가짜 pg_dump(받은 인자 · PG* 환경 변수 이름만 기록) · 시험 볼륨 폴더 · 가짜 rsync(시험 폴더 복사). PATH 맨 앞의 덫(docker · rsync · pg_dump)이
실제 명령 호출 0을 증명한다. DSN은 시험용 가짜 값이고, 명령줄 · 출력 · 파일 이름 · manifest에 없는지 본다."""
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from conftest import ROOT

BACKUP = ROOT / "scripts" / "vps" / "backup"
PULL = ROOT / "scripts" / "vps" / "pull-backup"
FAKE_PW = "fake-pw-Zq9"
FAKE_DSN = f"postgresql://db_owner:{FAKE_PW}@postgres:5432/llmops"
DAYS = [f"2026-10-{d:02d}" for d in range(9, 17)]             # 8개


@pytest.fixture
def env(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trap = tmp_path / "trap.log"
    for name in ("docker", "rsync", "pg_dump"):
        f = bin_dir / name
        f.write_text(f"#!/bin/sh\necho {name} >> {trap}\nexit 99\n")
        f.chmod(0o755)
    log = tmp_path / "pg_dump.log"
    fake = bin_dir / "fake-pg-dump"
    fake.write_text(f"""#!{sys.executable}
import json, os, sys
names = sorted(k for k in os.environ if k.startswith("PG"))
open({str(log)!r}, "a").write(json.dumps({{"argv": sys.argv[1:], "env_names": names,
    "password_set": bool(os.environ.get("PGPASSWORD")), "host": os.environ.get("PGHOST"), "db": os.environ.get("PGDATABASE")}}) + "\\n")
if os.environ.get("FAKE_PG_FAIL"):
    sys.stderr.write("pg_dump: error: connection to server at \\"" + os.environ["PGHOST"] + "\\" failed: password authentication failed for user \\"" + os.environ["PGUSER"] + "\\" (pw " + os.environ.get("PGPASSWORD", "") + ")\\n")
    sys.exit(3)
sys.stdout.buffer.write(b"PGDMP fake dump")
""")
    fake.chmod(0o755)
    rs = bin_dir / "fake-rsync"
    rs.write_text(f"""#!{sys.executable}
import shutil, sys
args = [a for a in sys.argv[1:]]
src, dst = args[-2].rstrip("/"), args[-1].rstrip("/")
if src.startswith("missing"):
    sys.exit(23)
shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.partial"))
open({str(tmp_path / 'rsync.log')!r}, "a").write(" ".join(args[:-2]) + "\\n")
""")
    rs.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("BACKUP_PG_DUMP", str(fake))
    monkeypatch.setenv("PULL_BACKUP_RSYNC", str(rs))
    monkeypatch.delenv("LLMOPSAGENT_BACKUP_DSN", raising=False)
    secret = tmp_path / "backup.env"
    secret.write_text(f"OTHER=x\nLLMOPSAGENT_BACKUP_DSN={FAKE_DSN}\n")
    vols = {}
    for name in ("payloads", "status", "deploy-data-task10-v1-pg-local"):
        d = tmp_path / "volumes" / name / "_data"            # VPS 볼륨처럼 끝 이름이 모두 _data
        (d / "sub").mkdir(parents=True)
        (d / "sub" / f"{name}.txt").write_text(name)
        vols[name] = d
    dest = tmp_path / "backup"
    dest.mkdir()

    class E:
        pass
    e = E()
    e.tmp, e.log, e.secret, e.vols, e.dest, e.trap = tmp_path, log, secret, vols, dest, trap
    yield e
    assert not trap.exists(), f"실제 명령이 불림: {trap.read_text()}"


def backup(e, *extra, date="2026-10-18", vols=True, extra_env=None):
    args = ["--dest", str(e.dest), "--env-file", str(e.secret), "--date", date, *extra]
    if vols:
        for n, p in e.vols.items():
            args += ["--volume", f"{n}={p}"]
    return subprocess.run([sys.executable, str(BACKUP), *args], capture_output=True, text=True,
                          env={**os.environ, **(extra_env or {})}, timeout=60)


def no_secret(e, r):
    assert FAKE_PW not in r.stdout + r.stderr and FAKE_DSN not in r.stdout + r.stderr
    if e.log.exists():
        assert FAKE_PW not in e.log.read_text()                      # 명령줄에 값 없음(가짜 pg_dump가 argv를 기록)
    for p in e.dest.rglob("*"):
        assert FAKE_PW not in p.name
        if p.name == "manifest.json":
            assert FAKE_PW not in p.read_text() and "db_owner" not in p.read_text()


def test_backup_makes_dump_volumes_manifest(env):
    r = backup(env)
    assert r.returncode == 0, r.stdout + r.stderr
    day = env.dest / "2026-10-18"
    assert sorted(p.name for p in day.iterdir()) == ["db.dump", "manifest.json", "volumes.tar.gz"]
    assert (day / "db.dump").read_bytes() == b"PGDMP fake dump"
    call = json.loads(env.log.read_text().splitlines()[0])
    assert call["argv"] == ["--format=custom", "--no-password"] and call["password_set"]
    assert (call["host"], call["db"]) == ("postgres", "llmops")
    assert {"PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"} <= set(call["env_names"])
    m = json.loads((day / "manifest.json").read_text())
    assert m["dsn_env"] == "LLMOPSAGENT_BACKUP_DSN" and set(m["volumes"]) == set(env.vols)
    assert [f["name"] for f in m["files"]] == ["db.dump", "volumes.tar.gz"] and all(len(f["sha256"]) == 64 for f in m["files"])
    with tarfile.open(day / "volumes.tar.gz") as t:
        names = t.getnames()
    for n, p in env.vols.items():                                      # 끝 이름이 같아도 겹치지 않음
        assert f"{m['volumes'][n]}/sub/{n}.txt" in names
    no_secret(env, r)
    assert not any(p.name.endswith(".partial") for p in env.dest.iterdir())


def test_backup_keeps_7_and_touches_only_date_dirs_inside(env):
    for d in DAYS:
        (env.dest / d).mkdir()
    (env.dest / "notes").mkdir()
    (env.dest / "README.txt").write_text("keep")
    outside = env.tmp / "outside"
    outside.mkdir()
    (outside / "x").write_text("x")
    (env.dest / "2026-01-01").symlink_to(outside, target_is_directory=True)   # 날짜 이름 링크는 따라가지 않음
    r = backup(env)
    assert r.returncode == 0, r.stdout
    left = sorted(p.name for p in env.dest.iterdir() if p.is_dir() and not p.is_symlink() and p.name.startswith("2026-10"))
    assert left == DAYS[-6:] + ["2026-10-18"]                           # 9개 중 최근 7개
    assert (env.dest / "notes").is_dir() and (env.dest / "README.txt").exists() and (outside / "x").exists()
    assert (env.dest / "2026-01-01").is_symlink()
    no_secret(env, r)


def test_backup_failure_keeps_old_and_does_not_prune(env):
    for d in DAYS:
        (env.dest / d).mkdir()
    r = backup(env, extra_env={"FAKE_PG_FAIL": "1"})
    assert r.returncode == 1 and "지난 백업은 그대로" in r.stdout
    assert sorted(p.name for p in env.dest.iterdir()) == DAYS        # 새 폴더 · .partial 없음, 지우기 없음
    no_secret(env, r)


def test_backup_same_day_rerun_replaces(env):
    assert backup(env).returncode == 0
    (env.dest / "2026-10-18" / "stale").write_text("old")
    assert backup(env).returncode == 0
    assert not (env.dest / "2026-10-18" / "stale").exists()


def test_backup_dsn_from_environment_wins(env):
    r = backup(env, extra_env={"LLMOPSAGENT_BACKUP_DSN": "postgresql://u2:pw2x@db2:5433/other"})
    assert r.returncode == 0
    call = json.loads(env.log.read_text().splitlines()[0])
    assert (call["host"], call["db"]) == ("db2", "other") and "pw2x" not in r.stdout + env.log.read_text()


@pytest.mark.parametrize("args,env_text", [
    (["--volume", "payloads=relative/path"], None),
    (["--volume", "Bad Name=/tmp"], None),
    ([], "OTHER=x\n"),                                                   # DSN 이름 없음
    ([], "LLMOPSAGENT_BACKUP_DSN=mysql://a:b@h/d\n"),                     # 모양 다름
    (["--keep", "0"], None),
    ([], "LLMOPSAGENT_BACKUP_DSN=postgresql://a:b@h:port/d\n"),           # 포트가 숫자 아님
    (["--dsn-env", "lower"], None),
])
def test_backup_usage_errors_write_nothing(env, args, env_text):
    if env_text is not None:
        env.secret.write_text(env_text)
    r = backup(env, *args, vols=False)
    assert r.returncode == 2 and not any(env.dest.iterdir()) and not env.log.exists()
    assert "b@h" not in r.stderr


# ---------------------------------------------------------------- 맥 받기
def remote_with(env, days):
    remote = env.tmp / "remote-backup"
    remote.mkdir()
    for d in days:
        sub = remote / d
        sub.mkdir()
        (sub / "db.dump").write_bytes(d.encode())
        import hashlib
        (sub / "manifest.json").write_text(json.dumps({"files": [{"name": "db.dump", "bytes": len(d), "sha256": hashlib.sha256(d.encode()).hexdigest()}]}))
    (remote / "2026-10-18.partial").mkdir()
    return remote


def pull(env, remote, mac):
    return subprocess.run([sys.executable, str(PULL), "--remote", str(remote), "--dest", str(mac)], capture_output=True, text=True, timeout=60)


def test_pull_keeps_7_on_mac_and_skips_partial(env):
    remote = remote_with(env, DAYS[-7:])
    mac = env.tmp / "mac"
    mac.mkdir()
    for d in DAYS[:2]:                                                   # 맥에 남아 있던 오래된 것
        (mac / d).mkdir()
    (mac / "keep.txt").write_text("k")
    r = pull(env, remote, mac)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(p.name for p in mac.iterdir() if p.is_dir()) == DAYS[-7:]
    assert (mac / "keep.txt").exists() and not (mac / "2026-10-18.partial").exists()
    assert "--exclude *.partial" in (env.tmp / "rsync.log").read_text()


def test_pull_hash_mismatch_does_not_prune(env):
    remote = remote_with(env, DAYS[-7:])
    (remote / DAYS[-1] / "db.dump").write_bytes(b"broken!!!!")
    mac = env.tmp / "mac"
    mac.mkdir()
    (mac / DAYS[0]).mkdir()
    r = pull(env, remote, mac)
    assert r.returncode == 1 and DAYS[-1] in r.stdout and (mac / DAYS[0]).exists()


def test_pull_failure(env):
    mac = env.tmp / "mac"
    mac.mkdir()
    r = subprocess.run([sys.executable, str(PULL), "--remote", "missing:/x", "--dest", str(mac)], capture_output=True, text=True, cwd=env.tmp)
    assert r.returncode == 1 and "받기 실패" in r.stdout and not any(mac.iterdir())


def test_default_pg_dump_is_compose_exec_with_names_only(tmp_path, monkeypatch):
    """기본 명령 모양(계약 N5): compose exec -T, -e 이름만(값 없음), 서비스 postgres, 절대 compose 경로. 가짜 docker가 받은 인자를 기록."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    d = bin_dir / "docker"
    d.write_text(f"""#!{sys.executable}
import json, os, sys
open({str(log)!r}, "w").write(json.dumps({{"argv": sys.argv[1:], "pw": bool(os.environ.get("PGPASSWORD"))}}))
sys.stdout.buffer.write(b"PGDMP")
""")
    d.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("BACKUP_PG_DUMP", raising=False)
    monkeypatch.setenv("LLMOPSAGENT_BACKUP_DSN", FAKE_DSN)
    dest = tmp_path / "backup"
    dest.mkdir()
    r = subprocess.run([sys.executable, str(BACKUP), "--dest", str(dest), "--date", "2026-10-18", "--env-file", str(tmp_path / "none")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    call = json.loads(log.read_text())
    a = call["argv"]
    assert a[:2] == ["compose", "-f"] and Path(a[2]).is_absolute() and a[2].endswith("docker/compose.yaml")
    assert a[3:5] == ["exec", "-T"] and a[-4:] == ["postgres", "pg_dump", "--format=custom", "--no-password"]
    pairs = a[5:-4]
    assert pairs[::2] == ["-e"] * 5 and pairs[1::2] == ["PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"]
    assert FAKE_PW not in json.dumps(a) and call["pw"]


def test_backup_never_prunes_the_one_just_made(env):
    for d in [f"2026-11-{x:02d}" for x in range(1, 9)]:                # 미래 날짜 8개
        (env.dest / d).mkdir()
    r = backup(env, date="2026-10-01")
    assert r.returncode == 0 and (env.dest / "2026-10-01" / "manifest.json").exists()
    assert len([p for p in env.dest.iterdir() if p.is_dir()]) == 7


def test_backup_duplicate_volume_name_and_link_in_day_slot(env):
    r = backup(env, "--volume", f"payloads={env.vols['payloads']}", "--volume", f"payloads={env.vols['status']}", vols=False)
    assert r.returncode == 2 and not any(env.dest.iterdir())
    (env.dest / "2026-10-18").symlink_to(env.tmp, target_is_directory=True)
    r = backup(env)
    assert r.returncode == 2 and (env.dest / "2026-10-18").is_symlink() and not (env.dest / "2026-10-18.partial").exists()


def test_backup_without_password_does_not_pass_host_pgpassword(env):
    env.secret.write_text("LLMOPSAGENT_BACKUP_DSN=postgresql://db_owner@postgres:5432/llmops\n")
    r = backup(env, extra_env={"PGPASSWORD": "host-left-over"})
    assert r.returncode == 0 and not json.loads(env.log.read_text().splitlines()[0])["password_set"]


def test_pull_empty_manifest_is_mismatch(env):
    remote = remote_with(env, DAYS[-1:])
    (remote / DAYS[-1] / "manifest.json").write_text(json.dumps({"files": []}))
    mac = env.tmp / "mac"
    mac.mkdir()
    r = pull(env, remote, mac)
    assert r.returncode == 1 and "파일 목록 없음" in r.stdout


def fake_tar(env, code, msg_volume=None):
    """진짜 tar로 묶고 종료 코드 · 메시지만 바꾸는 가짜(tar는 덫 대상이 아님)."""
    f = env.tmp / "bin" / "fake-tar"
    member = f"{str(env.vols[msg_volume].resolve()).lstrip('/')}/sub/x" if msg_volume else "x"
    f.write_text(f"""#!/bin/sh
tar "$@" || exit 2
echo "tar: {member}: file changed as we read it" >&2
exit {code}
""")
    f.chmod(0o755)
    return {"BACKUP_TAR": str(f)}


def test_backup_tar_exit1_is_warning_for_that_volume(env):
    r = backup(env, extra_env=fake_tar(env, 1, "status"))
    assert r.returncode == 0 and "경고" in r.stdout
    m = json.loads((env.dest / "2026-10-18" / "manifest.json").read_text())
    assert [w["volume"] for w in m["warnings"]] == ["status"]
    assert (env.dest / "2026-10-18" / "db.dump").exists()


def test_backup_tar_exit2_fails_with_reason(env):
    r = backup(env, extra_env=fake_tar(env, 2))
    assert r.returncode == 1 and "tar 종료 2: tar: x: file changed" in r.stdout and not (env.dest / "2026-10-18").exists()


def test_backup_pg_dump_error_shown_without_secrets(env):
    r = backup(env, extra_env={"FAKE_PG_FAIL": "1"})
    assert r.returncode == 1 and "password authentication failed" in r.stdout
    assert FAKE_PW not in r.stdout and "db_owner" not in r.stdout
