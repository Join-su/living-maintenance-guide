"""R12 pgvector 끝-끝 — 일회용 컨테이너(D-323 U-5 · D-309 (3) 방식, packrt-1 보고 011 5절).
맥에 있는 이미지 pgvector/pgvector:0.8.6-pg16-bookworm으로 loopback 임의 port 컨테이너 하나 → 시험 DB에 부록 A DDL(시험 Pack id `r12` schema,
계정 runtime_reader · index_loader · GRANT 그대로) → 견본 기준 version 적재 → pgvector store로 첫 Run → 받은 편지 1건 → r12-update
(build → load --check → load → check_binding DB 대조) → 다음 첫 Run이 새 version · 새 문서 · flat-local과 같은 결과(점수 차 ≤ 1e-5) ·
다시 적재 = exists · runtime_reader로 적재 = load_refused. 끝나면 컨테이너 · 볼륨을 지우고 남은 것 0 확인.
DEPLOY_PG_CONTAINER=1일 때만 돈다(기본 전체 시험에서는 건너뜀). 비밀번호는 그 자리에서 만들어 env로만 넘기고 출력 · 파일 증거에 넣지 않는다
(시험 폴더의 env 파일은 tmp_path 안, 끝나면 pytest가 지움). 공유 DB · 실제 키 · 받기 0."""
import json
import os
import secrets
import shutil
import subprocess
import sys
import time

import pytest
import yaml

from test_r12_update import BASE_V, UPDATE, _config, _deployment, _first_run, _inbox, _pack_repo, _run_script, _state, needs_index

IMAGE = "pgvector/pgvector:0.8.6-pg16-bookworm"
SCHEMA = "r12"   # 시험 Pack의 pack.id(packrt가 조각 표 schema로 씀, D-298)
EPS = 1e-5
pytestmark = [needs_index, pytest.mark.skipif(os.environ.get("DEPLOY_PG_CONTAINER") != "1", reason="DEPLOY_PG_CONTAINER=1일 때만(일회용 컨테이너)")]

DDL = f"""
CREATE ROLE db_owner NOLOGIN;
CREATE ROLE llmops_app NOLOGIN;
CREATE ROLE runtime_reader LOGIN PASSWORD {{reader}};
CREATE ROLE index_loader LOGIN PASSWORD {{loader}};
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA {SCHEMA} AUTHORIZATION db_owner;
CREATE TABLE {SCHEMA}.data_release (
  release_id text PRIMARY KEY, store text NOT NULL, kind text NOT NULL CHECK (kind IN ('table','index')),
  sha256 text NOT NULL, manifest jsonb NOT NULL,
  embedding_provider text, embedding_model text, embedding_dim integer,
  embedding_mode text CHECK (embedding_mode IN ('real','fake')),
  loaded_at timestamptz NOT NULL DEFAULT now(), loaded_by text NOT NULL DEFAULT current_user,
  CHECK ((kind = 'index') = (embedding_mode IS NOT NULL AND embedding_model IS NOT NULL AND embedding_dim IS NOT NULL)));
CREATE TABLE {SCHEMA}.index_chunk (
  release_id text NOT NULL REFERENCES {SCHEMA}.data_release(release_id),
  chunk_id text NOT NULL, text text NOT NULL, metadata jsonb NOT NULL, locator jsonb NOT NULL, embedding vector NOT NULL,
  PRIMARY KEY (release_id, chunk_id));
ALTER TABLE {SCHEMA}.data_release OWNER TO db_owner;
ALTER TABLE {SCHEMA}.index_chunk OWNER TO db_owner;
GRANT USAGE ON SCHEMA {SCHEMA} TO index_loader, runtime_reader, llmops_app;
GRANT SELECT, INSERT ON {SCHEMA}.data_release, {SCHEMA}.index_chunk TO index_loader;
GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO runtime_reader, llmops_app;
ALTER ROLE index_loader SET search_path = {SCHEMA}, public;
"""


def _docker(*a, check=True, env=None):
    return subprocess.run(["docker", *a], capture_output=True, text=True, check=check, env=env)


@pytest.fixture
def pg(tmp_path):
    """loopback 임의 port 컨테이너 하나(이름에 시험 표시) → 시험 DB r12test · 계정 셋. 끝나면 컨테이너 · 볼륨 지움 → 남은 것 0 확인."""
    import psycopg
    from psycopg import sql
    if _docker("image", "inspect", IMAGE, check=False).returncode:
        pytest.skip(f"맥에 이미지 {IMAGE}가 없음(받지 않음)")
    name = f"llmopsagent-r12-test-{secrets.token_hex(4)}"
    pw = {k: secrets.token_urlsafe(18) for k in ("su", "reader", "loader")}
    _docker("run", "-d", "--name", name, "-e", "POSTGRES_PASSWORD", "-p", "127.0.0.1::5432", IMAGE,
            env={**os.environ, "POSTGRES_PASSWORD": pw["su"]})   # 값은 env로만(명령줄 0)
    try:
        port = int(_docker("port", name, "5432/tcp").stdout.strip().rsplit(":", 1)[1])
        url = lambda user, p, db: f"postgresql://{user}:{p}@127.0.0.1:{port}/{db}?connect_timeout=3"   # noqa: E731
        for _ in range(60):
            try:
                with psycopg.connect(url("postgres", pw["su"], "postgres"), autocommit=True) as c:
                    c.execute("CREATE DATABASE r12test")
                break
            except psycopg.OperationalError:
                time.sleep(1)
        else:
            pytest.fail("시험 컨테이너 DB가 뜨지 않음")
        with psycopg.connect(url("postgres", pw["su"], "r12test"), autocommit=True) as c:
            c.execute(sql.SQL(DDL).format(reader=sql.Literal(pw["reader"]), loader=sql.Literal(pw["loader"])))   # DDL은 매개변수 묶기 없음
        reader_env, loader_env = tmp_path / "deploy.env", tmp_path / "loader.env"
        reader_env.write_text(f"LLMOPSAGENT_RUNTIME_DSN={url('runtime_reader', pw['reader'], 'r12test')}\n")
        loader_env.write_text(f"LLMOPSAGENT_INDEX_LOADER_DSN={url('index_loader', pw['loader'], 'r12test')}\n")
        os.chmod(reader_env, 0o600)
        os.chmod(loader_env, 0o600)
        yield {"name": name, "su": url("postgres", pw["su"], "r12test"), "reader_env": reader_env, "loader_env": loader_env,
               "reader": url("runtime_reader", pw["reader"], "r12test"), "loader": url("index_loader", pw["loader"], "r12test"),
               "secrets": list(pw.values())}
    finally:
        _docker("rm", "-f", "-v", name, check=False)
        assert _docker("ps", "-a", "-q", "--filter", f"name={name}").stdout.strip() == "", "시험 컨테이너가 남음"


def _load(pack_dir, release, dsn, check=False):
    """packrt.index load(같은 Python) — 적재 DSN은 이 자식 env에만."""
    env = {k: v for k, v in os.environ.items() if not k.endswith("_DSN") and "PASSWORD" not in k}
    args = [sys.executable, "-m", "packrt.index", "load", "--pack", str(pack_dir), "--release", str(release), "--json"]
    if check:
        args.insert(-1, "--check")
    else:
        env["LLMOPSAGENT_INDEX_LOADER_DSN"] = dsn
    r = subprocess.run(args, capture_output=True, text=True, env=env)
    return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])


def _hits(body):
    return body["result"]["grounding"].get("similar") or []


def _same(a, b):
    """flat-local ↔ pgvector 같은 결과(D-298 Q-5): id 순서 · locator · ranks · metadata 같음, 점수 차 ≤ 1e-5."""
    assert len(a) == len(b)
    for x, y in zip(a, b):
        sx, sy = {k: v for k, v in x.items() if "score" not in k}, {k: v for k, v in y.items() if "score" not in k}
        assert sx == sy, (sx, sy)
        for k in x:
            if "score" in k and isinstance(x[k], (int, float)):
                assert abs(x[k] - y[k]) <= EPS, (k, x[k], y[k])


def test_u5_r12_pgvector_end_to_end_in_a_throwaway_container(tmp_path, pg):
    import psycopg
    keep = dict(os.environ)   # 시험 안 Deployment가 이 프로세스 env에 실행용 DSN을 넣고 다른 DB 이름을 뺌 → 끝나면 되돌림
    try:
        repo = _pack_repo(tmp_path)
        rc, out = _load(repo / "pack", repo / "releases" / "indexes" / BASE_V, pg["loader"], check=True)
        assert rc == 0 and out["status"] == "checked", out
        rc, out = _load(repo / "pack", repo / "releases" / "indexes" / BASE_V, pg["loader"])   # 기준 version을 먼저 적재(운영 첫 적재 몫)
        assert rc == 0 and out["status"] == "loaded" and out["schema"] == SCHEMA and out["chunks"] == 3, out

        root, conf = _config(tmp_path, repo)
        cfg = yaml.safe_load(conf.read_text())
        flat_store = dict(cfg["execution"]["stores"]["history"])
        cfg["execution"]["stores"]["history"] = {"backend": "pgvector", "release": BASE_V, "connection": {"env": "LLMOPSAGENT_RUNTIME_DSN"},
                                                 "embedding": {"mode": "fake"}}
        cfg["runtime_db"] = {"env_file": str(pg["reader_env"])}
        conf.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))

        st, body, info = _first_run(_deployment(conf), "공구 마모")
        assert st == 201 and info["releases"]["stores"] == {"history": BASE_V}, body
        old_drs = info["data_release_id"]
        assert len(_hits(body)) == 3

        new = _inbox(tmp_path)
        r = _run_script(root, "update", "--config", str(conf), *UPDATE, "--loader-env-file", str(pg["loader_env"]))
        assert r.returncode == 0, r.stdout + r.stderr
        assert "적재 loaded" in r.stdout and "운영 설정 바꿈" in r.stdout, r.stdout
        assert not any(s in r.stdout + r.stderr for s in pg["secrets"])
        live = root / "config-live" / conf.name
        new_v = yaml.safe_load(live.read_text())["execution"]["stores"]["history"]["release"]
        assert new_v != BASE_V and _state(tmp_path)["history"][-1]["status"] == "restart_needed"

        st2, body2, info2 = _first_run(_deployment(live), new["text"])   # 다음 첫 Run = pgvector에서 새 version
        assert st2 == 201 and info2["releases"]["stores"] == {"history": new_v} and info2["data_release_id"] != old_drs, body2
        hits = _hits(body2)
        assert len(hits) == 4 and any(new["record_id"] in json.dumps(h.get("locator"), ensure_ascii=False) for h in hits)

        flat = yaml.safe_load(live.read_text())   # 같은 version을 flat-local로(폴더는 쓰기 폴더에 그대로) → 같은 결과
        flat["execution"]["stores"]["history"] = {**flat_store, "release": new_v}
        flat.pop("runtime_db", None)
        flat_conf = root / "config" / "r12-flat.yaml"
        flat_conf.write_text(yaml.safe_dump(flat, allow_unicode=True, sort_keys=False))
        st3, body3, _ = _first_run(_deployment(flat_conf), new["text"])
        assert st3 == 201
        _same(_hits(body3), hits)

        built = next((tmp_path / "data" / "releases" / "indexes").iterdir())
        rc, out = _load(repo / "pack", built, pg["loader"])
        assert rc == 0 and out["status"] == "exists", out   # 다시 적재 = 쓴 것 0
        rc, out = _load(repo / "pack", built, pg["reader"])
        assert rc == 1 and out["error"]["code"] == "load_refused", out   # runtime_reader로는 적재 거절

        with psycopg.connect(pg["su"]) as c:
            n_rel = c.execute(f"SELECT count(*) FROM {SCHEMA}.data_release").fetchone()[0]
            n_chunk = dict(c.execute(f"SELECT release_id, count(*) FROM {SCHEMA}.index_chunk GROUP BY 1").fetchall())
        assert n_rel == 2 and n_chunk == {BASE_V: 3, new_v: 4}
    finally:
        os.environ.clear()
        os.environ.update(keep)
