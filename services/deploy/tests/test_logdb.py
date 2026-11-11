"""logDB 시험(지시 006 2단계 6): 서버가 공유 기록기(TraceWriter, trace_writer 계정)로 PostgreSQL `trace`에 실행 기록을 쌓는다.

- DB: llmops-lite-v2 db/compose.yaml의 로컬 PostgreSQL. 시험마다 새 DB(llmopsagent_t_<random>)를 만들고(llmops db/dbadmin.py init의
  DB 부분과 같음 — 계정 비밀번호를 다시 넣는 부분은 클러스터 전체라 부르지 않음, 보고 025 · ES-E) dbadmin migrate로 변경 파일을 적용하고,
  끝나면 지운다. 공유 DB(llmopsagent · llmopsagent_test)는 건드리지 않는다(이름 · DSN 바꿈을 적용 전에 확인)
- 비밀 값: db/.env의 비밀번호는 시험 폴더의 env 파일로만 넘기고 출력하지 않는다(scripts/test-db.sh는 --tb=short, 지역 변수 출력 없음)
- DEPLOY_PG=1일 때만 돈다(scripts/test-db.sh). 그때 DB가 없으면 실패, 평소(scripts/test.sh)에는 건너뜀
- 등록 대조: 시험 폴더의 llmops.db 모양 SQLite(deployment 표)에 deploy-bootstrap이 낸 값과 같은 줄을 넣는다"""
import importlib.util
import os
import secrets
import sqlite3
from pathlib import Path

import pytest

from conftest import ROOT, V0_REVISION
from deploy_server.fake_provider import FakeProvider

LLMOPS = ROOT.parent / "llmops-lite-v2"
ENV_FILE = LLMOPS / "db" / ".env"
DRS = "drs:b7eec8736389157c"   # deploy-bootstrap(파일 backend 실행 설정)이 낸 값

pytestmark = pytest.mark.pg


def _dbadmin():
    spec = importlib.util.spec_from_file_location("dbadmin", LLMOPS / "db" / "dbadmin.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Env:
    """db/.env 값. 어디에 찍혀도(실패 출력, repr) 키 이름만 보인다."""
    __slots__ = ("_d",)

    def __init__(self, d):
        self._d = dict(d)

    def __getitem__(self, k):
        return self._d[k]

    def get(self, k, default=None):
        return self._d.get(k, default)

    def __repr__(self):
        return f"_Env(keys={sorted(self._d)})"

    __str__ = __repr__


@pytest.fixture
def pg_gate():
    if os.environ.get("DEPLOY_PG") != "1":
        pytest.skip("logDB 시험은 scripts/test-db.sh(DEPLOY_PG=1)에서만 돕니다")


SHARED_DBS = {"llmopsagent", "llmopsagent_test", "postgres"}


def _create_test_db(adm, env, name):
    """dbadmin.init의 DB 부분만: 새 DB(owner db_owner) · vector · public 막기 · 계정 CONNECT · 계정별 search_path(이 DB에만).
    init의 계정 부분(CREATE/ALTER ROLE … PASSWORD)은 클러스터 전체(공유 DB와 같은 계정)라 부르지 않고, 계정이 있는지만 본다."""
    from psycopg import sql
    su = env["LLMOPSAGENT_SUPERUSER_DSN"]
    su_test = adm._with_db(su, name)
    if not name.startswith("llmopsagent_t_") or not su_test.split("?")[0].endswith(f"/{name}"):   # 아래 문장이 postgres · 공유 DB로 가지 않게
        raise adm.AdminError("시험 DB 이름 · superuser DSN 바꿈 확인 실패")
    with adm._connect(su, autocommit=True) as c:
        missing = [r for r in adm.ROLES if not c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (r,)).fetchone()]
        if missing:
            raise adm.AdminError(f"계정이 없습니다(llmops 세션의 dbadmin init 몫): {missing}")
        c.execute(sql.SQL("CREATE DATABASE {} OWNER db_owner").format(sql.Identifier(name)))
    with adm._connect(su_test, autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        c.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
        c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(name), sql.SQL(", ").join(sql.Identifier(r) for r in adm.ROLES)))
        c.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.SQL(", ").join(sql.Identifier(r) for r in adm.PUBLIC_USAGE)))
        for role, (_, _, path) in adm.ROLES.items():
            if path:
                c.execute(sql.SQL("ALTER ROLE {} IN DATABASE {} SET search_path = {}").format(sql.Identifier(role), sql.Identifier(name),
                                                                                             sql.SQL(", ").join(sql.Identifier(p) for p in path)))


def _fresh_db(upto=None):
    """새 시험 DB(llmopsagent_t_<hex>) → llmops 작업 트리의 migration 적용(upto = 그 번호까지만, G-3: 0009 → 실제 0010) → 끝나면 지움."""
    import shutil
    import tempfile
    adm = _dbadmin()
    env = _Env(adm.read_env(ENV_FILE))
    if not env.get("LLMOPSAGENT_SUPERUSER_DSN"):
        pytest.fail("llmops-lite-v2/db/.env가 없습니다(llmops 세션이 python db/dbadmin.py env로 만듦)")
    name = f"llmopsagent_t_{secrets.token_hex(4)}"
    owner_dsn = adm._with_db(env["LLMOPSAGENT_OWNER_DSN"], name)
    if name in SHARED_DBS or not owner_dsn.split("?")[0].endswith(f"/{name}"):   # migrate가 기본 DB로 가지 않게(보고 025 (b))
        pytest.fail("시험 DB 이름 · DSN 바꿈 확인 실패 — migrate를 부르지 않음")
    try:
        with tempfile.TemporaryDirectory() as part:
            try:
                _create_test_db(adm, env, name)
                if upto is None:
                    adm.migrate(env, dbname=name)
                else:   # 같은 파일(내용 · 이름 그대로)을 그 번호까지만 — 뒤에 전체 폴더로 migrate하면 나머지만 적용됨
                    for num, _, path in adm.migration_files():
                        if num <= upto:
                            shutil.copyfile(path, Path(part) / path.name)
                    adm.migrate(env, dbname=name, folder=Path(part))
            except adm.AdminError as e:
                pytest.fail(f"PostgreSQL을 준비할 수 없습니다: {type(e).__name__}")
            yield adm, env, name
    finally:   # 준비 중 실패해도 만든 시험 DB는 지운다
        from psycopg import sql
        with adm._connect(env["LLMOPSAGENT_SUPERUSER_DSN"], autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def fresh_db(pg_gate):
    yield from _fresh_db()


@pytest.fixture
def fresh_db_0009(pg_gate):
    """llmops 0010(장부 모델 칸, EV-044) 앞 — G-3이 그 위에 실제 0010을 적용."""
    yield from _fresh_db(upto=9)


def _dsn(adm, env, role, name):
    return adm.dsn(role, env[adm.ROLES[role][0]], env.get("LLMOPSAGENT_PG_PORT", adm.DEFAULT_PORT), name)


def rows(db, sql, args=()):
    import psycopg
    adm, env, name = db
    with psycopg.connect(_dsn(adm, env, "db_owner", name), connect_timeout=5) as c:   # 읽기는 db_owner(trace_writer는 SELECT가 없음)
        return c.execute(sql, args).fetchall()


def registry(tmp_path, rows_):
    f = tmp_path / "llmops.db"
    con = sqlite3.connect(f)
    con.execute("CREATE TABLE deployment (deployment_id TEXT, pack_revision TEXT, data_release_id TEXT)")
    con.executemany("INSERT INTO deployment VALUES (?,?,?)", rows_)
    con.commit()
    con.close()
    return f


@pytest.fixture
def pg_deployment(fresh_db, tmp_path, packs_dir):
    """설정 파일의 trace·registration으로 서버가 직접 TraceWriter를 만든다(시험 DB의 trace_writer DSN을 담은 env 파일만 바꿈)."""
    adm, env, name = fresh_db
    envf = tmp_path / "trace.env"
    envf.write_text(f"LLMOPSAGENT_TRACE_DSN={_dsn(adm, env, 'trace_writer', name)}\n", encoding="utf-8")
    envf.chmod(0o600)

    def make(config="dev-local", reg_rows=None, provider=None):
        reg_rows = [("task10@dev-local", V0_REVISION, DRS), ("task10@v0-local", V0_REVISION, DRS)] if reg_rows is None else reg_rows
        sub = tmp_path / f"{config}-{secrets.token_hex(2)}"
        sub.mkdir()
        from deploy_server.app import Deployment
        from deploy_server.settings import load_settings
        s = load_settings(ROOT / "config" / f"{config}.yaml",
                          overrides={"data_dir": str(sub / "data"), "packs_dir": str(packs_dir),
                                     "registration": {"mode": "llmops_db", "path": str(registry(sub, reg_rows))},
                                     "trace": {"dsn_env": "LLMOPSAGENT_TRACE_DSN", "env_file": str(envf)}})
        return Deployment(s, provider=provider)
    return make


def _post(client, body, key):
    import json
    return client.post("/runs", content=json.dumps(body, ensure_ascii=False).encode(), headers={"Idempotency-Key": key})


@pytest.fixture(autouse=True)
def _no_trace_dsn_in_env(monkeypatch):
    monkeypatch.delenv("LLMOPSAGENT_TRACE_DSN", raising=False)   # env 파일만 쓰게(개발 DB로 새지 않게)


def test_event_card_to_report_to_logdb(pg_deployment, fresh_db, client_for, cases):
    dep = pg_deployment("v0-local")
    from tracewriter import TraceWriter
    assert isinstance(dep.trace, TraceWriter)
    c = client_for(dep)
    r = _post(c, cases["C01"], "db-1")
    assert r.status_code == 201, r.text
    b = r.json()
    rid = b["run_id"]
    assert b["data_release_id"] == DRS and b["pack"]["tag"] == "v0"

    run = rows(fresh_db, "SELECT trace_id, origin, source_kind, deployment_id, pack_revision, data_release_id, runtime_version, retention_class,"
                         " redaction_policy_version FROM trace.run WHERE run_id=%s", (rid,))
    assert run == [(b["trace"]["trace_id"], "deployment", "agent_pack", "task10@v0-local", V0_REVISION, DRS, b["runtime_version"], "standard", "redact-1")]
    ev = rows(fresh_db, "SELECT seq, type, node_id FROM trace.event WHERE run_id=%s ORDER BY seq", (rid,))
    types = [t for _, t, _ in ev]
    assert [s for s, _, _ in ev] == list(range(len(ev)))
    assert types[0] == "run_started" and types[-2] == "artifact_written" and types[-1] == "run_ended"
    assert {"node_started", "node_ended", "review_requested", "review_recorded"} <= set(types)
    assert rows(fresh_db, "SELECT status, outcome FROM trace.run_current WHERE run_id=%s", (rid,)) == [("succeeded", "completed")]

    rep = c.get(b["report"]["href"]).json()
    art = rows(fresh_db, "SELECT node_id, format, sha256, location, size FROM trace.artifact WHERE run_id=%s", (rid,))
    assert art == [("report", "md", rep["sha256"], rep["location"], rep["size"])]
    assert rows(fresh_db, "SELECT event, node_id, action, actor_kind FROM trace.review WHERE run_id=%s ORDER BY seq", (rid,)) == [
        ("review_requested", "review", None, None), ("review_recorded", "review", "approve", "auto")]
    ret = rows(fresh_db, "SELECT node_id, store, release_id, query_ref FROM trace.retrieval WHERE run_id=%s ORDER BY seq", (rid,))
    # task10 find_history는 custom_code라 retrieval 줄이 없다(입출력은 node_event 참조). retrieve node는 find_manuals 하나
    assert {(n, st, rl) for n, st, rl, _ in ret} == {("find_manuals", "manuals", "ir-manuals-v2")}
    assert all(q for *_, q in ret)
    llm = rows(fresh_db, "SELECT provider, model, prompt_ref FROM trace.node_event WHERE run_id=%s AND node_id='draft' AND status='done'", (rid,))
    assert llm and llm[0][:2] == ("openai", "gpt-4.1-mini") and llm[0][2]

    refs = rows(fresh_db, "SELECT sha256, location, retention_class FROM trace.payload_ref WHERE run_id=%s", (rid,))
    assert refs and {rc for *_, rc in refs} <= {"standard", "provider_raw"}
    root = dep.settings.trace["payload_root"]
    assert all((root / loc).is_file() for _, loc, _ in refs)

    again = _post(c, cases["C01"], "db-1")                       # 같은 키: 다시 실행하지 않음 → 기록도 하나
    assert again.json() == b and rows(fresh_db, "SELECT count(*) FROM trace.run") == [(1,)]


class OverclaimingModel(FakeProvider):
    def _draft(self, prompt):
        out = super()._draft(prompt)
        out["uncertainty"] = "센서 값으로 보아 원인이 확정되었습니다"
        return out


def test_answer_check_failure_escalates_in_logdb(pg_deployment, fresh_db, client_for, cases):
    dep = pg_deployment("dev-local", provider=OverclaimingModel())
    c = client_for(dep)
    r = _post(c, cases["C01"], "db-esc")
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["review"]["action"] == "escalate" and b["outcome"] == "completed" and b["report"]
    rid = b["run_id"]
    note = "자동 escalate(답 검사 실패 초안, 사람 검토 아님)"
    assert rows(fresh_db, "SELECT event, node_id, review_id, action, actor_kind, reason FROM trace.review WHERE run_id=%s ORDER BY seq", (rid,)) == [
        ("review_requested", "review_failed", f"{rid}:review_failed:1", None, None, None),
        ("review_recorded", "review_failed", f"{rid}:review_failed:1", "escalate", "auto", note)]
    assert rows(fresh_db, "SELECT count(*) FROM trace.artifact WHERE run_id=%s", (rid,)) == [(1,)]
    assert rows(fresh_db, "SELECT deployment_id, retention_class FROM trace.run WHERE run_id=%s", (rid,)) == [("task10@dev-local", "dev")]
    assert rows(fresh_db, "SELECT status, outcome FROM trace.run_current WHERE run_id=%s", (rid,)) == [("succeeded", "completed")]


def test_unregistered_deployment_leaves_no_run_in_logdb(pg_deployment, fresh_db, client_for, cases):
    dep = pg_deployment("v0-local", reg_rows=[("task10@dev-local", V0_REVISION, DRS)])   # v0-local은 등록 안 됨
    r = _post(client_for(dep), cases["C01"], "db-unreg")
    assert r.status_code == 500
    e = r.json()["error"]
    assert e["code"] == "deployment_not_registered" and e["details"]["deployment_id"] == "task10@v0-local"
    assert rows(fresh_db, "SELECT count(*) FROM trace.run") == [(0,)] and rows(fresh_db, "SELECT count(*) FROM trace.event") == [(0,)]
    assert dep.provider.calls == []


def test_database_down_is_503_trace_unavailable(pg_gate, tmp_path, packs_dir, client_for, cases):
    """DB에 닿지 않으면 실행하지 않고 503 trace_unavailable(retryable). 메시지에 접속 정보가 나오지 않는다."""
    from deploy_server.app import Deployment
    from deploy_server.settings import load_settings
    envf = tmp_path / "down.env"
    envf.write_text("LLMOPSAGENT_TRACE_DSN=postgresql://trace_writer:fake-pass@127.0.0.1:1/none\n", encoding="utf-8")
    s = load_settings(ROOT / "config" / "dev-local.yaml",
                      overrides={"data_dir": str(tmp_path / "data"), "packs_dir": str(packs_dir), "registration": {"mode": "none"},
                                 "trace": {"dsn_env": "LLMOPSAGENT_TRACE_DSN", "env_file": str(envf)}})
    dep = Deployment(s)
    r = _post(client_for(dep), cases["C01"], "db-down")
    assert r.status_code == 503
    e = r.json()["error"]
    assert e["code"] == "trace_unavailable" and e["retryable"] is True and "fake-pass" not in r.text
    assert dep.provider.calls == []


# ------------------------------------------------------------------ PostgreSQL 실행 설정(지시 006b 3, 켜지는 때: llmops 016 E-S-01)
def _pg_backend():
    from packrt.ir import releases
    return "pgvector" in getattr(releases, "BUILTIN_BACKENDS", {}).get("vector", set())


PG_DRS = "drs:54e2537743f7fa9c"   # 예상 값(오케 지시 006b). E-S-01 뒤 deploy-bootstrap 값과 대조해 등록


@pytest.mark.xfail(not _pg_backend(), run=False, strict=True,
                   reason="016 대기: 불러온 packrt에 PostgreSQL backend(pgvector·postgres-table)가 없습니다 — llmops 016 E-S-01(packrt 0.8.0) 뒤 바로 켜짐")
def test_pg_execution_config_retrieval_in_logdb(pg_deployment, fresh_db, client_for, cases, monkeypatch):
    """task10@v0-pg-local: store를 개발 DB(llmopsagent)의 task10 schema에서 runtime_reader로 읽기만 하고, 실행 기록은 시험 DB trace에 쌓는다.
    trace.retrieval에 pgvector 검색 줄(ir-manuals-v3-fake, embedding_mode fake)이 남는다. 응답·파일·trace에 실행용 DSN 값이 없다."""
    import os
    monkeypatch.delenv("LLMOPSAGENT_RUNTIME_DSN", raising=False)   # 설정 runtime_db.env_file(llmops db/.env)에서 한 줄만 읽게. 끝나면 되돌림
    dep = pg_deployment("v0-pg-local", reg_rows=[("task10@v0-pg-local", V0_REVISION, PG_DRS)])
    assert dep.settings.uses_postgres and dep.runner.data_release_id == PG_DRS
    c = client_for(dep)
    r = _post(c, cases["C01"], "db-pg-1")
    assert r.status_code == 201, r.text
    b = r.json()
    rid = b["run_id"]
    assert b["deployment_id"] == "task10@v0-pg-local" and b["pack"]["tag"] == "v0" and b["data_release_id"] == PG_DRS

    assert rows(fresh_db, "SELECT deployment_id, data_release_id FROM trace.run WHERE run_id=%s", (rid,)) == [("task10@v0-pg-local", PG_DRS)]
    assert rows(fresh_db, "SELECT status, outcome FROM trace.run_current WHERE run_id=%s", (rid,)) == [("succeeded", "completed")]
    ret = rows(fresh_db, "SELECT node_id, store, release_id, embedding_mode, embedding_model, results FROM trace.retrieval WHERE run_id=%s ORDER BY seq", (rid,))
    assert {(n, st, rl, m) for n, st, rl, m, *_ in ret} == {("find_manuals", "manuals", "ir-manuals-v3-fake", "fake")}
    assert all(res for *_, res in ret) and ret[0][4] == "text-embedding-3-small"
    types = [t for (t,) in rows(fresh_db, "SELECT type FROM trace.event WHERE run_id=%s ORDER BY seq", (rid,))]
    assert types[0] == "run_started" and types[-2:] == ["artifact_written", "run_ended"]

    run = c.get(b["href"]).json()
    hist = run["result"]["grounding"]["evidence"]
    assert hist and run["result"]["output"].startswith("# 정비 대응 보고서")

    from deploy_server.execution import dsn_secrets
    secrets_ = dsn_secrets(os.environ["LLMOPSAGENT_RUNTIME_DSN"])   # 서버가 프로세스 환경에 넣은 값(출력하지 않고 비교만)
    blobs = [p.read_bytes() for p in dep.settings.data_dir.rglob("*") if p.is_file()]
    blobs += [p.read_bytes() for p in dep.settings.trace["payload_root"].rglob("*") if p.is_file()]
    blobs.append(str(rows(fresh_db, "SELECT body FROM trace.event WHERE run_id=%s", (rid,))).encode())
    leaked = [i for i, blob in enumerate(blobs) if any(s_.encode() in blob for s_ in secrets_)]
    assert leaked == [], "실행용 DSN 값이 파일·trace에 남음(값은 출력하지 않음)"


# ------------------------------------------------------------------ 종료 뒤 다시 기록(EV-002 V-16 d)
def test_writer_close_then_next_run_records_again(pg_deployment, fresh_db, client_for, cases):
    """서버 종료 경로의 close()(tracewriter 0.1.1) 뒤에도 같은 기록기로 다음 실행이 새 연결로 기록된다."""
    dep = pg_deployment("dev-local")
    c = client_for(dep)
    first = _post(c, cases["C01"], "db-close-1").json()
    dep.close()
    second = _post(c, cases["C02"], "db-close-2")
    assert second.status_code == 201, second.text
    ids = {r for (r,) in rows(fresh_db, "SELECT run_id FROM trace.run")}
    assert ids == {first["run_id"], second.json()["run_id"]}
    assert rows(fresh_db, "SELECT status FROM trace.run_current WHERE run_id=%s", (second.json()["run_id"],)) == [("succeeded",)]


# ------------------------------------------------------------------ 태그 v1 + PostgreSQL 실행 설정(EV-002 CHG-03)
from test_v1 import V1_WAIT  # noqa: E402


@V1_WAIT
@pytest.mark.xfail(not _pg_backend(), run=False, reason="016 대기: 불러온 packrt에 PostgreSQL backend가 없습니다")
def test_v1_pg_c01_in_logdb(pg_deployment, fresh_db, client_for, cases, monkeypatch):
    """task10@v1-pg-local로 C01 → 201. 등록 대조는 시험 폴더의 SQLite 등록 fixture(실제 llmops.db는 쓰지 않음)."""
    from deploy_server.settings import load_settings
    monkeypatch.delenv("LLMOPSAGENT_RUNTIME_DSN", raising=False)
    rev = load_settings(ROOT / "config" / "v1-pg-local.yaml").expect_revision
    assert rev, "config/v1-pg-local.yaml의 expect_revision이 비어 있습니다(EV-002 ES-2에서 채움)"
    dep = pg_deployment("v1-pg-local", reg_rows=[("task10@v1-pg-local", rev, PG_DRS)])
    r = _post(client_for(dep), cases["C01"], "db-v1-1")
    assert r.status_code == 201, r.text
    b = r.json()
    rid = b["run_id"]
    assert b["pack"]["tag"] == "v1" and b["pack"]["pack_revision"] == rev and b["data_release_id"] == PG_DRS
    assert b["review"]["actor_kind"] == "auto"
    assert rows(fresh_db, "SELECT deployment_id, pack_revision, data_release_id FROM trace.run WHERE run_id=%s", (rid,)) == [("task10@v1-pg-local", rev, PG_DRS)]
    ret = rows(fresh_db, "SELECT node_id, release_id, embedding_mode FROM trace.retrieval WHERE run_id=%s", (rid,))
    assert ("find_manuals", "ir-manuals-v3-fake", "fake") in ret   # v1에 더한 retrieve node 줄은 더 있어도 됨(EV-002 v10)
    recorded = rows(fresh_db, "SELECT action, actor_kind FROM trace.review WHERE run_id=%s AND event='review_recorded'", (rid,))
    assert recorded == [(b["review"]["action"], "auto")]
