"""등록 대조 PG(VFE EV-005 ES-E, CHG-02 · V-36): deploy_server/registration.py — mode llmops_db · llmops_pg.

- 위쪽(평소 시험): 설정 검사, 닿지 않는 PG(127.0.0.1:1)에서 서버 503 · deploy-status ③ · V-04 실패 문구, 접속 정보 값이 문구에 없음
- 아래쪽(V-36, DEPLOY_PG=1 — scripts/test-db.sh): 시험마다 새로 만든 시험 DB(llmopsagent_t_<hex>, test_logdb.fresh_db = dbadmin init의
  DB 부분 복제(계정 비밀번호 다시 넣기 없음) + dbadmin migrate로 0001 ~ 0006 적용)의 llmops.deployment에만 줄을 넣고, 서버 · deploy-status · ES-3 확인(V-04)이 runtime_reader로 같은 함수를 거쳐
  세 칸을 대조하는지 본다. 공유 DB(llmopsagent · llmopsagent_test)에는 접속하지 않는다(전후 읽기 확인은 run 폴더 도구)
- 장부 fixture = 보고 023 2절 pack_commit 새 번호 규칙(6단계)으로 옮긴 줄 모양: v0 두 줄(v0-local · v0-pg-local, 옛 e2c5df0 40자 · 7자 → 93ca0a1)
  · v1 한 줄(v1-pg-local, 옛 647c87e → 6628470). 규칙 적용 기록은 run 폴더 evidence/es-e-pack-commit-rule.txt"""
import importlib.util

import pytest
import yaml
from tracewriter import TraceWriteError

from conftest import ROOT, V0_REVISION
from deploy_server.execution import RUNTIME_DSN_ENV, ExecutionError
from deploy_server.registration import LedgerUnavailable, ledger_rows, registration_check
from deploy_server.settings import SettingsError, load_settings
from fake_writer import FakeWriter
from test_logdb import _dbadmin, _dsn, fresh_db, fresh_db_0009, pg_gate, rows  # noqa: F401 - fixture를 이 모듈에도 등록

spec = importlib.util.spec_from_file_location("es3_check", ROOT / "scripts" / "es3_check.py")
es3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(es3)
ds = es3.ds

V1_REVISION = "sha256:3e315c048c962f83981648dc6d6c0f547d39a9ab7c128d331777113cb2bc42ac"
PG_DRS, FILE_DRS = "drs:54e2537743f7fa9c", "drs:b7eec8736389157c"
NEW_V0, NEW_V1 = "93ca0a162dff9f70ca43bb4ea4b27a97457d3295", "6628470b854116bce1d790418e98494d796a7f7c"   # 10-19 이력 정리 뒤 태그 v0 · v1 commit
OLD_V0 = "e2c5df0c8dada0e2c7226a14437c4efb95a42fb4"
FAKE_DSN = "postgresql://runtime_reader:fake-pass-ledger@127.0.0.1:1/none"
PG = {"mode": "llmops_pg"}


def pg_config(tmp_path, base, env_file, **doc_over):
    """base 설정 사본(등록 = PG, 실행용 DSN 찾는 곳 = env_file, 데이터 폴더 = 시험 폴더)."""
    doc = yaml.safe_load((ROOT / "config" / f"{base}.yaml").read_text())
    doc.update(registration=dict(PG), data_dir=str(tmp_path / f"data-{base}"), **doc_over)
    doc["runtime_db"] = {"env_file": str(env_file)}
    cfg = tmp_path / f"{base}-pg-test.yaml"
    cfg.write_text(yaml.safe_dump(doc, allow_unicode=True))
    return cfg


@pytest.fixture
def no_runtime_env(monkeypatch):
    monkeypatch.delenv(RUNTIME_DSN_ENV, raising=False)   # env_file만 쓰게(개발 DB로 새지 않게)


# ------------------------------------------------------------------ 설정
def test_llmops_pg_takes_mode_only():
    s = load_settings(ROOT / "config" / "v1-pg-vps.yaml")
    assert s.registration == PG
    with pytest.raises(SettingsError, match="mode만"):
        load_settings(ROOT / "config" / "v1-pg-local.yaml", overrides={"registration": {"mode": "llmops_pg", "path": "x.db"}})
    with pytest.raises(SettingsError, match="llmops_pg"):
        load_settings(ROOT / "config" / "v1-pg-local.yaml", overrides={"registration": {"mode": "pg"}})


def test_ledger_rows_needs_a_registration_mode():
    with pytest.raises(ValueError, match="등록 대조 설정"):
        ledger_rows({"mode": "none"}, "task10@v1-pg-local")


# ------------------------------------------------------------------ 닿지 않는 PG(평소 시험, 실제 DB 없음)
def test_server_without_ledger_dsn_does_not_start(make_deployment, tmp_path, no_runtime_env):
    with pytest.raises(ExecutionError, match=RUNTIME_DSN_ENV):
        make_deployment(config="v0-local", registration=dict(PG), runtime_db={"env_file": str(tmp_path / "missing.env")})


def test_server_unreachable_ledger_is_503_and_hides_dsn(make_deployment, client_for, cases, tmp_path, no_runtime_env):
    envf = tmp_path / "reader.env"
    envf.write_text(f"{RUNTIME_DSN_ENV}={FAKE_DSN}\n")
    probe = make_deployment(config="v0-local", registration=dict(PG), runtime_db={"env_file": str(envf)})
    assert "fake-pass-ledger" in probe.secrets() and FAKE_DSN in probe.secrets()           # 가림 목록에 들어감
    dep = make_deployment(config="v0-local", data_dir=tmp_path / "d2", registration=dict(PG), runtime_db={"env_file": str(envf)},
                          trace_writer=FakeWriter(tmp_path / "t", registration_check=probe._registration_check()))
    import json
    r = client_for(dep).post("/runs", content=json.dumps(cases["C01"]).encode(), headers={"Idempotency-Key": "pg-down"})
    assert r.status_code == 503
    e = r.json()["error"]
    assert e["code"] == "trace_unavailable" and e["retryable"] is True and "fake-pass" not in r.text and "://" not in r.text
    assert dep.provider.calls == []


def test_pg_check_refuses_other_origins():
    check = registration_check(PG, FAKE_DSN)
    with pytest.raises(TraceWriteError) as ei:
        check({"origin": "evaluation", "evaluation_run_id": "r1"})
    assert ei.value.code == "trace_schema"
    with pytest.raises(TraceWriteError) as ei:
        check({"origin": "deployment", "source_kind": "agent_pack", "deployment_id": "task10@v0-local"})
    assert ei.value.code == "trace_unavailable" and "fake-pass" not in str(ei.value)


def test_deploy_status_unreachable_ledger_is_step3(tmp_path, no_runtime_env):
    envf = tmp_path / "reader.env"
    envf.write_text(f"{RUNTIME_DSN_ENV}={FAKE_DSN}\n")
    cfg = pg_config(tmp_path, "v1-pg-local", envf)
    st = tmp_path / "status"
    assert ds.main(["registered", "--id", "CHG-PG1", "--version", "V1", "--config", str(cfg), "--status-dir", str(st)]) == 1
    rec = ds.read_status(st / "CHG-PG1.json")
    assert rec["stage"] == "failed" and rec["detail"].startswith(ds.STEP3)
    assert "llmops.deployment" in rec["detail"] and "LedgerUnavailable" in rec["detail"]
    assert "fake-pass" not in rec["detail"] and "://" not in rec["detail"]
    envf.unlink()
    assert ds.main(["registered", "--id", "CHG-PG2", "--version", "V1", "--config", str(cfg), "--status-dir", str(st)]) == 1
    assert "ExecutionError" in ds.read_status(st / "CHG-PG2.json")["detail"]


def test_pg_ledger_always_needs_status_dir(tmp_path, capsys):   # 이름은 ES-E 그대로(시험 id 유지) — 답장 031-01 뒤 뜻: 저장소 .env가 아닌 PG 설정은 늘 --status-dir
    cfg = pg_config(tmp_path, "v1-pg-local", tmp_path / "none.env")
    assert not ds.uses_real_ledger(load_settings(cfg))
    assert ds.main(["registered", "--id", "CHG-PG3", "--version", "V1", "--config", str(cfg)]) == 2
    assert "--status-dir" in capsys.readouterr().err


def test_v04_unreachable_ledger_fails_with_type_only(tmp_path, monkeypatch):
    monkeypatch.setenv(RUNTIME_DSN_ENV, FAKE_DSN)
    s = load_settings(pg_config(tmp_path, "v1-pg-local", tmp_path / "none.env"))
    ok, why = es3.check(s, NEW_V1, V1_REVISION, PG_DRS, "v1")
    assert not ok and why.startswith("장부를 읽을 수 없음: llmops.deployment(LedgerUnavailable") and "fake-pass" not in why
    assert es3.side_files(s) is None                                                      # PG 장부는 곁 파일 관찰 없음


# ------------------------------------------------------------------ V-36: 시험 DB의 llmops.deployment(DEPLOY_PG=1)
LEDGER = [   # (deployment_id, pack_revision, data_release_id, runtime_version, pack_commit, pack_tag, config_sha256, decision)
    ("task10@v0-local", V0_REVISION, FILE_DRS, "0.7.0", NEW_V0, "v0", "60be2efff9ee7910082aed4ecf648a46b79ca83768da91760a9791b5e2cdac75", "deploy_bootstrap"),
    ("task10@v0-pg-local", V0_REVISION, PG_DRS, "0.8.0", NEW_V0, "v0", "4204a44785089da12c96acb0ad75a46807ba5119fb3f3b24d3f0d5520a71741b", "deploy_bootstrap"),
    ("task10@v1-pg-local", V1_REVISION, PG_DRS, "0.8.0", NEW_V1, "v1", "4204a44785089da12c96acb0ad75a46807ba5119fb3f3b24d3f0d5520a71741b", "deploy_approved"),
]


def add_ledger_rows(db, rows_, created_at=None):
    """시험 DB llmops.deployment에 줄 더하기(db_owner로 — 등록은 llmops 몫이라 시험 fixture만). created_at 기본 = 결정 번호."""
    import psycopg
    adm, env, name = db
    with psycopg.connect(_dsn(adm, env, "db_owner", name), connect_timeout=5) as c:
        for i, (dep_id, rev, drs, rtv, commit, tag, cfg_sha, decision) in enumerate(rows_):
            (did,) = c.execute("INSERT INTO llmops.decision (subject_kind, subject_id, decision, reason, decided_by, decided_at) "
                               "VALUES ('deployment', %s, %s, 'ES-E 시험 장부', 'test-es-e', 0) RETURNING decision_id", (dep_id, decision)).fetchone()
            c.execute("INSERT INTO llmops.deployment (deployment_id, pack_id, pack_revision, data_release_id, release_bindings, runtime_version, "
                      "pack_commit, pack_tag, config_sha256, decision_id, created_at) VALUES (%s, 'task10', %s, %s, '{}', %s, %s, %s, %s, %s, %s)",
                      (dep_id, rev, drs, rtv, commit, tag, cfg_sha, did, float(did) if created_at is None else created_at[i]))


def _ledger(db, tmp_path):
    adm, env, name = db
    add_ledger_rows(db, LEDGER)
    envf = tmp_path / "reader.env"
    envf.write_text(f"{RUNTIME_DSN_ENV}={_dsn(adm, env, 'runtime_reader', name)}\n", encoding="utf-8")
    envf.chmod(0o600)
    trace_env = tmp_path / "trace.env"
    trace_env.write_text(f"LLMOPSAGENT_TRACE_DSN={_dsn(adm, env, 'trace_writer', name)}\n", encoding="utf-8")
    trace_env.chmod(0o600)
    return db, envf, trace_env


@pytest.fixture
def ledger_db(fresh_db, tmp_path, no_runtime_env):
    """시험 DB llmops.deployment에 옮긴 줄 3개. runtime_reader DSN은 env 파일로만."""
    return _ledger(fresh_db, tmp_path)


@pytest.fixture
def ledger_db_0009(fresh_db_0009, tmp_path, no_runtime_env):
    """ledger_db와 같은 줄 3개 — llmops 0010 앞(G-3이 실제 0010을 적용)."""
    return _ledger(fresh_db_0009, tmp_path)


def _llmops_0010():
    """llmops 작업 트리의 0010(EV-044, 767a464 합침) — 시험 DB가 0010까지 올라가는지."""
    adm = _dbadmin()
    return any(num == 10 for num, _, _ in adm.migration_files())


@pytest.mark.pg
def test_v36_ledger_rows_read_by_runtime_reader(ledger_db):
    from deploy_server.execution import read_env_value
    _, envf, _ = ledger_db
    dsn = read_env_value(envf, RUNTIME_DSN_ENV)
    got = ledger_rows(PG, "task10@v0-pg-local", dsn)
    want = {"deployment_id": "task10@v0-pg-local", "pack_revision": V0_REVISION, "data_release_id": PG_DRS, "pack_commit": NEW_V0, "pack_tag": "v0"}
    assert got == [{**want, "model_release_id": None, "model_bindings": None} if _llmops_0010() else want]   # 0010 뒤: 옛 줄 모델 = null(D-328 Q-1 (가)) · 모델 묶음 칸도 읽음(D-373)
    assert ledger_rows(PG, "task10@dev-local", dsn) == []


@pytest.mark.pg
def test_v36_ledger_rows_order_by_created_at(ledger_db):
    """같은 배포 id 줄이 여럿이면 created_at 순(SQLite rowid 순의 근사 — PG llmops.deployment에는 ord 칸이 없음)."""
    from deploy_server.execution import read_env_value
    db, envf, _ = ledger_db
    later, earlier = (("task10@order", V1_REVISION, PG_DRS, "0.8.0", NEW_V1, "v1", "c" * 64, "deploy_approved"),
                      ("task10@order", V0_REVISION, PG_DRS, "0.8.0", NEW_V0, "v0", "c" * 64, "deploy_bootstrap"))
    add_ledger_rows(db, [later, earlier], created_at=[200.0, 100.0])   # 늦은 줄을 먼저 넣음
    got = ledger_rows(PG, "task10@order", read_env_value(envf, RUNTIME_DSN_ENV))
    assert [r["pack_tag"] for r in got] == ["v0", "v1"]


@pytest.mark.pg
def test_v36_missing_ledger_table_is_none(fresh_db, no_runtime_env):
    """표가 없는 DB(0005 전 모양)는 '장부 없음'(None) — 시험 DB에서만 표 이름을 바꿔 봄(db_owner = 표 주인)."""
    import psycopg
    adm, env, name = fresh_db
    owner = _dsn(adm, env, "db_owner", name)
    with psycopg.connect(owner, connect_timeout=5, autocommit=True) as c:
        c.execute("ALTER TABLE llmops.deployment RENAME TO deployment_gone")
    assert ledger_rows(PG, "task10@v0-local", owner) is None


@pytest.mark.pg
def test_v36_server_three_columns_from_pg(ledger_db, tmp_path, packs_dir, client_for, cases, monkeypatch):
    """서버(실제 TraceWriter → 시험 DB trace)가 PG 장부로 세 칸을 대조: v0-local 등록 줄 → 201, dev-local(줄 없음) → 500, 기록 0."""
    import json
    from deploy_server.app import Deployment
    monkeypatch.delenv("LLMOPSAGENT_TRACE_DSN", raising=False)
    db, envf, trace_env = ledger_db

    def make(config):
        s = load_settings(ROOT / "config" / f"{config}.yaml",
                          overrides={"data_dir": str(tmp_path / f"data-{config}"), "packs_dir": str(packs_dir), "registration": dict(PG),
                                     "runtime_db": {"env_file": str(envf)}, "trace": {"dsn_env": "LLMOPSAGENT_TRACE_DSN", "env_file": str(trace_env)}})
        return Deployment(s)

    dep = make("v0-local")
    r = client_for(dep).post("/runs", content=json.dumps(cases["C01"]).encode(), headers={"Idempotency-Key": "pg-reg-1"})
    assert r.status_code == 201, r.text
    b = r.json()
    assert rows(db, "SELECT deployment_id, pack_revision, data_release_id FROM trace.run WHERE run_id=%s", (b["run_id"],)) == [
        ("task10@v0-local", V0_REVISION, FILE_DRS)]
    other = make("dev-local")
    r = client_for(other).post("/runs", content=json.dumps(cases["C01"]).encode(), headers={"Idempotency-Key": "pg-reg-2"})
    assert r.status_code == 500
    e = r.json()["error"]
    assert e["code"] == "deployment_not_registered" and e["details"]["deployment_id"] == "task10@dev-local"
    assert rows(db, "SELECT count(*) FROM trace.run") == [(1,)] and other.provider.calls == []
    assert "runtime_reader:" not in r.text
    # 같은 배포 id에 한 칸만 다른 줄: revision만 다름 · drs만 다름 → 둘 다 거부(세 칸 모두 같아야 함)
    add_ledger_rows(db, [("task10@rev-only", V1_REVISION, FILE_DRS, "0.7.0", NEW_V1, "v1", "d" * 64, "deploy_bootstrap"),
                         ("task10@drs-only", V0_REVISION, PG_DRS, "0.7.0", NEW_V0, "v0", "d" * 64, "deploy_bootstrap")])
    for dep_id in ("task10@rev-only", "task10@drs-only"):
        s = load_settings(ROOT / "config" / "v0-local.yaml",
                          overrides={"deployment_id": dep_id, "data_dir": str(tmp_path / f"data-{dep_id[7:]}"), "packs_dir": str(packs_dir),
                                     "registration": dict(PG), "runtime_db": {"env_file": str(envf)},
                                     "trace": {"dsn_env": "LLMOPSAGENT_TRACE_DSN", "env_file": str(trace_env)}})
        one = Deployment(s)
        r = client_for(one).post("/runs", content=json.dumps(cases["C01"]).encode(), headers={"Idempotency-Key": f"pg-{dep_id[7:]}"})
        assert r.status_code == 500 and r.json()["error"]["code"] == "deployment_not_registered", dep_id
        assert one.provider.calls == []
    assert rows(db, "SELECT count(*) FROM trace.run") == [(1,)]


@pytest.mark.pg
def test_v36_deploy_status_registered_from_pg(ledger_db, tmp_path):
    _, envf, _ = ledger_db
    cfg = pg_config(tmp_path, "v1-pg-local", envf)
    st = tmp_path / "status"
    assert ds.main(["registered", "--id", "CHG-PG-V1", "--version", "V1", "--config", str(cfg), "--status-dir", str(st)]) == 0
    rec = ds.read_status(st / "CHG-PG-V1.json")
    assert (rec["stage"], rec["revision"], rec["commit"], rec["tag"]) == ("registered", V1_REVISION, NEW_V1, "v1")


@pytest.mark.pg
def test_v36_v04_new_commit_passes_old_commit_fails(ledger_db, tmp_path):
    """V-04(es3_check): 옮긴 v0-pg-local 줄(새 40자) = 태그 v0 commit → 통과 · registered, 옛 40자 알림 → ③ 실패(규칙 그대로)."""
    import subprocess
    _, envf, _ = ledger_db
    cfg = pg_config(tmp_path, "v0-pg-local", envf)
    s = load_settings(cfg)
    assert es3.check(s, NEW_V0, V0_REVISION, PG_DRS, "v0")[0]
    ok, why = es3.check(s, OLD_V0, V0_REVISION, PG_DRS, "v0")
    assert not ok and "commit이 알림 commit 40자가 아님" in why
    st = tmp_path / "status"
    r = subprocess.run([str(ROOT / "scripts" / "py"), str(ROOT / "scripts" / "es3_check.py"), "--config", str(cfg), "--id", "RB-PG-V0",
                        "--version", "V0", "--commit", NEW_V0, "--revision", V0_REVISION, "--tag", "v0", "--status-dir", str(st)],
                       capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PG 장부(llmops.deployment) — 해당 없음" in r.stdout and "runtime_reader:" not in r.stdout + r.stderr
    rec = ds.read_status(st / "RB-PG-V0.json")
    assert (rec["stage"], rec["commit"], rec["tag"]) == ("registered", NEW_V0, "v0")


@pytest.mark.pg
def test_v36_runtime_reader_reads_only_the_ledger(ledger_db):
    """0006 권한: runtime_reader는 llmops.deployment SELECT만(쓰기 · 다른 llmops 표 읽기 거절). 0005 트리거: 장부는 추가만(db_owner도)."""
    import psycopg
    from deploy_server.execution import read_env_value
    (adm, env, name), envf, _ = ledger_db
    with psycopg.connect(read_env_value(envf, RUNTIME_DSN_ENV), connect_timeout=5, autocommit=True) as c:
        assert c.execute("SELECT count(*) FROM llmops.deployment").fetchone() == (3,)
        for sql in ("INSERT INTO llmops.deployment (deployment_id) VALUES ('x')", "SELECT count(*) FROM llmops.decision",
                    "UPDATE llmops.deployment SET pack_tag = 'v9'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)
    with psycopg.connect(_dsn(adm, env, "db_owner", name), connect_timeout=5, autocommit=True) as c:
        with pytest.raises(psycopg.errors.RaiseException, match="append only"):
            c.execute("UPDATE llmops.deployment SET pack_tag = 'v9' WHERE deployment_id = 'task10@v0-local'")


@pytest.mark.pg
def test_v36_account_without_ledger_grant_is_unavailable(fresh_db, no_runtime_env):
    """장부 권한(0006)이 없는 계정(trace_writer — llmops schema USAGE 없음)은 '장부 없음'이 아니라 읽기 실패(형 이름만)."""
    adm, env, name = fresh_db
    with pytest.raises(LedgerUnavailable, match="InsufficientPrivilege"):
        ledger_rows(PG, "task10@v0-local", _dsn(adm, env, "trace_writer", name))


# ------------------------------------------------------------------ G-3: 장부 모델 칸(llmops 0010 · EV-044) — D-326 Q-B · D-328 Q-1 (가)
def _add_model_column(db):
    """llmops 실제 0010(EV-044 767a464 — 모델 칸 · 네 칸 열쇠 · mrs 꼴 · data_release_auto 결정)을 시험 DB에 적용(D-337, 흉내 대신)."""
    adm, env, name = db
    if not _llmops_0010():
        pytest.fail("llmops 작업 트리에 migration 0010이 없습니다(EV-044 합침 767a464 뒤 시험)")
    assert adm.migrate(env, dbname=name) == [f.name for n, _, f in adm.migration_files() if n >= 10]


def _add_model_row(db, dep_id, rev, drs, mrs, commit, tag):
    """장부는 덧붙이기만(append_only) — 모델 값이 있는 새 줄(같은 배포 id 네 칸 열쇠)."""
    import psycopg
    adm, env, name = db
    with psycopg.connect(_dsn(adm, env, "db_owner", name), connect_timeout=5) as c:
        (did,) = c.execute("INSERT INTO llmops.decision (subject_kind, subject_id, decision, reason, decided_by, decided_at) "
                           "VALUES ('deployment', %s, 'deploy_bootstrap', 'G-3 시험 장부', 'test-g3', 0) RETURNING decision_id", (dep_id,)).fetchone()
        c.execute("INSERT INTO llmops.deployment (deployment_id, pack_id, pack_revision, data_release_id, release_bindings, runtime_version, "
                  "pack_commit, pack_tag, config_sha256, decision_id, created_at, model_release_id) "
                  "VALUES (%s, 'task10', %s, %s, '{}', '0.12.1', %s, %s, %s, %s, %s, %s)", (dep_id, rev, drs, commit, tag, "e" * 64, did, 1000.0 + did, mrs))


RUN_V0PG = {"origin": "deployment", "source_kind": "agent_pack", "deployment_id": "task10@v0-pg-local", "pack_revision": V0_REVISION,
            "data_release_id": PG_DRS}


@pytest.mark.pg
def test_g3_ledger_model_column_rules(ledger_db_0009, tmp_path):
    from deploy_server.execution import read_env_value
    db, envf, _ = ledger_db_0009
    dsn = read_env_value(envf, RUNTIME_DSN_ENV)
    registration_check(PG, dsn, "mrs:any")(dict(RUN_V0PG))          # 0010 전(칸 없음) = 지금처럼 세 칸 대조
    assert "model_release_id" not in ledger_rows(PG, "task10@v0-pg-local", dsn)[0]
    _add_model_column(db)
    assert ledger_rows(PG, "task10@v0-pg-local", dsn)[0]["model_release_id"] is None
    registration_check(PG, dsn, "mrs:any")(dict(RUN_V0PG))          # null 줄(0010 전에 쓴 줄) = 받아들임(Q-1 (가))
    registration_check(PG, dsn, None)(dict(RUN_V0PG))
    _add_model_row(db, "task10@g3-pg-local", V1_REVISION, PG_DRS, "mrs:aaaaaaaaaaaaaaaa", NEW_V1, "v1")
    g3 = {**RUN_V0PG, "deployment_id": "task10@g3-pg-local", "pack_revision": V1_REVISION}
    registration_check(PG, dsn, "mrs:aaaaaaaaaaaaaaaa")(dict(g3))   # 모델 같음 = 통과
    for other in ("mrs:bbbbbbbbbbbbbbbb", None):                    # 모델 다름 · 모델 없는 Pack = 장부에 없는 모델
        with pytest.raises(TraceWriteError) as e:
            registration_check(PG, dsn, other)(dict(g3))
        assert e.value.code == "deployment_not_registered"
    _add_model_row(db, "task10@g3-pg-local", V1_REVISION, PG_DRS, "mrs:bbbbbbbbbbbbbbbb", NEW_V1, "v1")
    registration_check(PG, dsn, "mrs:bbbbbbbbbbbbbbbb")(dict(g3))   # 같은 세 칸 · 다른 모델 줄이 더해지면 그 모델도 통과(네 칸 열쇠)
    import packrt.trace as pt
    s = load_settings(pg_config(tmp_path, "v1-pg-local", envf, deployment_id="task10@g3-pg-local"))
    want = pt.model_release_id({k: v.get("release") for k, v in (s.execution.get("models") or {}).items()})
    ok, why = es3.check(s, NEW_V1, V1_REVISION, PG_DRS, "v1")        # V-04도 같은 규칙: 이 설정 모델이 장부 두 줄 중 하나와 같아야
    assert ok is (want in ("mrs:aaaaaaaaaaaaaaaa", "mrs:bbbbbbbbbbbbbbbb")) and (ok or "장부 줄 모델 다름" in why), why


# ---------------------------------------------------------------- D-373(D-341 덧) 모델 다름 거부를 따로 말함 — code · 500 그대로
def test_d373_refusal_reason_and_model_fields():
    from deploy_server.registration import refusal
    from deploy_server.runner import model_nodes
    run = {"deployment_id": "c1a15@x", "pack_revision": "sha256:" + "1" * 64, "data_release_id": "drs:1"}
    row = {"pack_revision": run["pack_revision"], "data_release_id": "drs:1", "model_release_id": "mrs:aaaaaaaaaaaaaaaa",
           "model_bindings": '{"failure_classifier": {"release_id": "mr-standin", "manifest_sha256": "x"}, "same": {"release_id": "mr-s"}}'}
    reason, msg, extra = refusal(run, [], "mrs:bbbbbbbbbbbbbbbb")
    assert reason == "not_registered" and extra == {"reason": "not_registered"}
    assert msg == f"등록되지 않은 배포입니다: deployment_id=c1a15@x, pack_revision={run['pack_revision']}, data_release_id=drs:1"   # 지금 글 그대로
    reason, _, _ = refusal(run, [{**row, "data_release_id": "drs:2"}, {**row, "pack_revision": "sha256:" + "2" * 64}], "mrs:b")
    assert reason == "not_registered"                                       # revision · drs가 같은 줄이 없으면 모델 다름이 아님
    nodes = model_nodes({"graphs": {"run": {"nodes": [{"id": "predict_failure", "kind": "predict@1", "params": {"model": "failure_classifier"}},
                                                      {"id": "other", "kind": "retrieve@2", "params": {"model": "failure_classifier"}}]},
                                    "b": {"nodes": [{"id": "p2", "kind": "predict@1", "params": {"model": "failure_classifier"}}]}}})
    assert nodes == {"failure_classifier": ["p2", "predict_failure"]}         # predict node만 · 모든 graph
    reason, msg, extra = refusal(run, [row], "mrs:bbbbbbbbbbbbbbbb", {"failure_classifier": "mr-lgbm", "same": "mr-s"}, nodes)
    assert reason == "model_mismatch" and msg.startswith("장부의 모델과 다른 배포입니다: deployment_id=c1a15@x")
    assert "실행 모델 mrs:bbbbbbbbbbbbbbbb, 장부 모델 mrs:aaaaaaaaaaaaaaaa" in msg
    assert extra == {"reason": "model_mismatch", "model_release_id": "mrs:bbbbbbbbbbbbbbbb", "ledger_model_release_ids": ["mrs:aaaaaaaaaaaaaaaa"],
                     "models": {"failure_classifier": {"running": "mr-lgbm", "ledger": "mr-standin", "nodes": ["p2", "predict_failure"]}}}   # 다른 모델만
    _, _, extra = refusal(run, [{**row, "model_bindings": "깨진 글"}], "mrs:b", {"failure_classifier": "mr-lgbm"}, nodes)
    assert extra["models"] == {"failure_classifier": {"running": "mr-lgbm", "ledger": None, "nodes": ["p2", "predict_failure"]}}   # 장부 묶음 못 읽음 = 실행 이름만
    many = [{**row, "model_release_id": f"mrs:{i:016x}"} for i in range(40)]
    _, _, extra = refusal(run, many, "mrs:b", {f"m{i}": "r" for i in range(50)})
    assert len(extra["ledger_model_release_ids"]) == 16 and len(extra["models"]) == 32   # 크기 상한


def test_d373_check_keeps_last_refusal_and_clears_it(monkeypatch):
    import deploy_server.registration as reg
    rows = [{"pack_revision": "sha256:r", "data_release_id": "drs:1", "model_release_id": "mrs:aaaaaaaaaaaaaaaa"}]
    monkeypatch.setattr(reg, "pg_rows", lambda dsn, dep: rows)
    check = reg.registration_check(PG, "dsn", "mrs:bbbbbbbbbbbbbbbb", {"m": "x"}, {"m": ["n1"]})
    assert check.last_refusal is None
    run = {"origin": "deployment", "source_kind": "agent_pack", "deployment_id": "c1a15@x", "pack_revision": "sha256:r", "data_release_id": "drs:1"}
    with pytest.raises(TraceWriteError) as e:
        check(dict(run))
    assert e.value.code == "deployment_not_registered" and check.last_refusal["reason"] == "model_mismatch"
    assert check.last_refusal["models"]["m"] == {"running": "x", "ledger": None, "nodes": ["n1"]}
    rows[0]["model_release_id"] = "mrs:bbbbbbbbbbbbbbbb"
    check(dict(run))                                                        # 통과하면 비움(다음 실행에 옛 까닭이 붙지 않게)
    assert check.last_refusal is None


@pytest.mark.pg
def test_d373_server_model_only_mismatch_is_500_and_says_so(ledger_db, tmp_path, packs_dir, client_for, cases, monkeypatch):
    """같은 revision · 자료 묶음, 장부 모델만 다름 → 500 deployment_not_registered 그대로 · 글 '장부의 모델과 다른 배포' · details reason ·
    모델 칸 · node 실행 0 · trace 0 · 비밀 0. 줄 없는 배포 id는 reason not_registered · 지금 글."""
    import json
    import psycopg
    from deploy_server.app import Deployment
    monkeypatch.delenv("LLMOPSAGENT_TRACE_DSN", raising=False)
    db, envf, trace_env = ledger_db
    if not _llmops_0010():
        pytest.fail("llmops 작업 트리에 migration 0010이 없습니다")
    adm, env, name = db
    with psycopg.connect(_dsn(adm, env, "db_owner", name), connect_timeout=5) as c:
        (did,) = c.execute("INSERT INTO llmops.decision (subject_kind, subject_id, decision, reason, decided_by, decided_at) "
                           "VALUES ('deployment', 'task10@d373', 'deploy_bootstrap', 'D-373 시험', 'test', 0) RETURNING decision_id").fetchone()
        c.execute("INSERT INTO llmops.deployment (deployment_id, pack_id, pack_revision, data_release_id, release_bindings, runtime_version, pack_tag, "
                  "config_sha256, decision_id, created_at, model_release_id, model_bindings) VALUES ('task10@d373', 'task10', %s, %s, '{}', '0.12.1', 'v0', "
                  "%s, %s, 2000, 'mrs:aaaaaaaaaaaaaaaa', %s)",
                  (V0_REVISION, FILE_DRS, "e" * 64, did, json.dumps({"failure_classifier": {"release_id": "mr-standin"}})))

    def make(dep_id):
        s = load_settings(ROOT / "config" / "v0-local.yaml",
                          overrides={"deployment_id": dep_id, "data_dir": str(tmp_path / f"data-{dep_id[7:]}"), "packs_dir": str(packs_dir),
                                     "registration": dict(PG), "runtime_db": {"env_file": str(envf)},
                                     "trace": {"dsn_env": "LLMOPSAGENT_TRACE_DSN", "env_file": str(trace_env)}})
        return Deployment(s)

    before = rows(db, "SELECT count(*) FROM trace.run")
    dep = make("task10@d373")
    r = client_for(dep).post("/runs", content=json.dumps(cases["C01"]).encode(), headers={"Idempotency-Key": "d373-1"})
    assert r.status_code == 500, r.text
    e = r.json()["error"]
    assert (e["code"], e["where"], e["retryable"]) == ("deployment_not_registered", "trace", False)
    assert e["message"].startswith("장부의 모델과 다른 배포입니다: deployment_id=task10@d373")
    d = e["details"]
    assert (d["deployment_id"], d["pack_revision"], d["data_release_id"], d["reason"]) == ("task10@d373", V0_REVISION, FILE_DRS, "model_mismatch")
    assert d["model_release_id"] == dep.model_release_id and d["ledger_model_release_ids"] == ["mrs:aaaaaaaaaaaaaaaa"]
    assert d["models"]["failure_classifier"]["ledger"] == "mr-standin" and d["models"]["failure_classifier"]["running"] is None
    assert dep.provider.calls == [] and rows(db, "SELECT count(*) FROM trace.run") == before
    assert "runtime_reader:" not in r.text and "postgresql://" not in r.text
    other = make("task10@d373-none")
    r = client_for(other).post("/runs", content=json.dumps(cases["C01"]).encode(), headers={"Idempotency-Key": "d373-2"})
    e = r.json()["error"]
    assert r.status_code == 500 and e["details"]["reason"] == "not_registered" and e["message"].startswith("등록되지 않은 배포입니다:")
    assert "models" not in e["details"]


def test_d645_v04_passes_when_any_same_revision_row_matches(tmp_path, monkeypatch):
    """D-645(packrt-1 230): 같은 revision · drs 줄이 여럿이면 첫 줄만 보지 않고 칸이 모두 맞는 줄이 하나라도 있으면 통과 —
    앞선 시도의 줄(태그 · commit 빈 칸 또는 다름)이 먼저 걸려 '등록 대조 실패'가 나던 꼴."""
    monkeypatch.setenv(RUNTIME_DSN_ENV, FAKE_DSN)
    s = load_settings(pg_config(tmp_path, "v1-pg-local", tmp_path / "none.env"))
    old = {"pack_revision": V1_REVISION, "data_release_id": PG_DRS, "pack_commit": OLD_V0, "pack_tag": "v1"}
    new = {**old, "pack_commit": NEW_V1}
    monkeypatch.setattr(es3, "ledger_rows", lambda reg, dep, dsn: [old, new])
    monkeypatch.setattr(es3, "model_ok", lambda row, mrs: True)
    ok, why = es3.check(s, NEW_V1, V1_REVISION, PG_DRS, "v1")
    assert ok and why.startswith("V-04 통과"), why
    monkeypatch.setattr(es3, "ledger_rows", lambda reg, dep, dsn: [old])          # 맞는 줄 없음 → 첫 줄의 다른 점을 그대로 말함
    ok, why = es3.check(s, NEW_V1, V1_REVISION, PG_DRS, "v1")
    assert not ok and "commit이 알림 commit 40자가 아님" in why


def test_d645_no_status_option_checks_without_writing(tmp_path, monkeypatch, capsys):
    """--no-status: 대조만 하고 상태 파일 · deploy-status 부름 0(감시가 장부 줄이 다 찰 때까지 다시 물을 때 화면에 failed가 깜빡이지 않게)."""
    monkeypatch.setenv(RUNTIME_DSN_ENV, FAKE_DSN)
    cfg = pg_config(tmp_path, "v1-pg-local", tmp_path / "none.env")
    monkeypatch.setattr(es3, "ledger_rows", lambda reg, dep, dsn: [])
    called = []
    monkeypatch.setattr(es3.subprocess, "run", lambda *a, **k: called.append(a) or None)
    rc = es3.main(["--config", str(cfg), "--id", "CHG-X", "--version", "V1", "--commit", NEW_V1, "--revision", V1_REVISION, "--tag", "v1", "--no-status"])
    assert rc == 1 and called == [] and "장부 줄 없음" in capsys.readouterr().out
