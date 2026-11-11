# ruff: noqa: E501 — SQL 문 · 시험 설명 문장은 길이 제한 밖
"""실제 PostgreSQL 통합 시험(marker db, DATABASE_URL 필요). 시험 DB 컨테이너는 매번 새로 만든다."""

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from c1a15.checks.passwords import is_argon2id_hash_of

pytestmark = pytest.mark.db
SERVER = Path(__file__).resolve().parents[1]
URL = os.environ.get("DATABASE_URL", "")


def alembic(*args, env=None):
    subprocess.run(["alembic", *args], cwd=SERVER, check=True, env=env)


def dbinit():
    env = {**os.environ, "DATA_DIR": str(SERVER.parent / "data"), "PYTHONPATH": str(SERVER / "src")}
    return subprocess.run(["python", "-m", "c1a15.dbinit"], cwd=SERVER, env=env, capture_output=True, text=True)


@pytest.fixture(scope="module")
def db():
    if not URL:
        pytest.skip("DATABASE_URL 없음")
    return create_engine(URL)


def test_migration_round_trip_and_no_model_drift(db):
    alembic("upgrade", "head")
    alembic("downgrade", "base")
    with db.connect() as c:
        assert c.execute(text("select count(*) from pg_namespace where nspname in ('ops','knowledge')")).scalar() == 0
    alembic("upgrade", "head")
    alembic("check")  # 모델과 DB 사이 차이 없음


def test_loader_counts_and_storage(db):
    env = {**os.environ, "DATA_DIR": str(SERVER.parent / "data"), "PYTHONPATH": str(SERVER / "src")}
    subprocess.run(["python", "-m", "c1a15.loader"], cwd=SERVER, env=env, check=True)
    with db.connect() as c:
        q = lambda sql: c.execute(text(sql)).scalar()  # noqa: E731
        assert q("select count(*) from knowledge.manual_sections") == 43
        assert q("select count(*) from knowledge.history_records") == 300
        assert q("select count(*) from ops.grade_table") == 36
        assert q("select count(*) from ops.common_precheck_items") == 3
        assert q("select count(*) from ops.equipment_assignments") == 20
        assert q("select extversion from pg_extension where extname = 'vector'")
        rows = c.execute(text("select role, password_hash from ops.accounts")).all()
    assert len(rows) == 5
    plain = {"tech": os.environ["C1A15_TECH_PASSWORD"], "lead": os.environ["C1A15_LEAD_PASSWORD"]}
    assert all(is_argon2id_hash_of(h, plain[role]) for role, h in rows)
    assert all(plain[role] not in h for role, h in rows)


FOUR = tuple(f"knowledge.{t}" for t in ("active_releases", "release_chunks", "data_releases", "history_embeddings"))


def test_vc56_0009_drops_four_tables_and_downgrade_recreates_empty(db):
    """VC-56(EV-001 CH-34): 0009 = 앱 리트리버 · release 표 넷 지움 · downgrade = 빈 표로 다시 · 다시 upgrade.

    downgrade는 자료를 되살리지 않음(S3-Q5).

    남길 표(history_records · manual_sections)와 적재 자료는 그대로.
    """
    env = {**os.environ, "DATA_DIR": str(SERVER.parent / "data"), "PYTHONPATH": str(SERVER / "src")}
    alembic("upgrade", "head")
    subprocess.run(["python", "-m", "c1a15.loader"], cwd=SERVER, env=env, check=True)
    reg = lambda c, t: c.execute(text("select to_regclass(:t)"), {"t": t}).scalar()  # noqa: E731
    with db.connect() as c:
        assert [reg(c, t) for t in FOUR] == [None] * 4
        assert c.execute(text("select count(*) from knowledge.history_records")).scalar() == 300
    alembic("downgrade", "0008")
    with db.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalar() == "0008"
        assert all(reg(c, t) for t in FOUR)
        assert [c.execute(text(f"select count(*) from {t}")).scalar() for t in FOUR] == [0] * 4
        assert c.execute(text("select count(*) from knowledge.history_records")).scalar() == 300
    alembic("upgrade", "head")
    subprocess.run(["python", "-m", "c1a15.loader"], cwd=SERVER, env=env, check=True)  # 다시 upgrade 뒤 적재기
    with db.connect() as c:
        assert [reg(c, t) for t in FOUR] == [None] * 4
        assert c.execute(text("select version_num from alembic_version")).scalar() == "0009"
        assert c.execute(text("select count(*) from knowledge.history_records")).scalar() == 300


def test_vc62_dbinit_first_install_then_noop_then_mismatch(db):
    """VC-62 · PR-35(EV-001 CH-39): 빈 DB = head + 적재 · 이미 설치 = migration · 적재 0(운영 자료 그대로) · 판 다름 = 멈춤(1)."""
    alembic("downgrade", "base")  # alembic_version 행 없음 = 첫 설치
    r = dbinit()
    assert r.returncode == 0 and "빈 DB — migration head(0009) · 적재 sections=43 history=300 accounts=5" in r.stdout, (
        r.stdout + r.stderr
    )
    with db.begin() as c:  # 운영 자료 흉내: 승인 이력 한 건 · 등급 바꿈
        c.execute(
            text(
                "insert into knowledge.history_records select 'R-9999', equipment_id, symptom_group, occurred_at, narrative, "
                "sensor_snapshot, record, 'approved_report', 'approved_report:9999' from knowledge.history_records limit 1"
            )
        )
        c.execute(text("update ops.grade_table set grade = 'C' where section_id = '4.3'"))
    r = dbinit()
    assert r.returncode == 0 and r.stdout.strip() == "dbinit: 이미 설치됨(0009) — migration · 적재 0", (
        r.stdout + r.stderr
    )
    with db.connect() as c:
        assert (
            c.execute(text("select count(*) from knowledge.history_records where record_id = 'R-9999'")).scalar() == 1
        )
        assert c.execute(text("select grade from ops.grade_table where section_id = '4.3'")).scalar() == "C"
    with db.begin() as c:
        c.execute(text("update alembic_version set version_num = '0008'"))
    r = dbinit()
    assert r.returncode == 1 and "DB 판 0008 ≠ 이미지 0009" in r.stdout and "--profile migrate" in r.stdout, r.stdout
    with db.begin() as c:  # 이미지가 모르는 판(더 새 DB)
        c.execute(text("update alembic_version set version_num = '9999'"))
    r = dbinit()
    assert r.returncode == 1 and "모르는 판" in r.stdout and "--profile migrate" not in r.stdout, r.stdout
    with db.begin() as c:
        c.execute(text("update alembic_version set version_num = '0009'"))
        c.execute(text("delete from knowledge.history_records where record_id = 'R-9999'"))
    alembic("downgrade", "base")
    assert dbinit().returncode == 0  # 다음 시험을 위해 처음 상태(적재 · 등급 원래 값)


def test_vc62_dbinit_recovers_incomplete_first_install(db):
    """ES-21 review B2: migration은 됐는데 적재가 실패한 반쪽 설치(판 = head · 업무 자료 0행) → 다음 dbinit가 적재를 다시."""
    alembic("downgrade", "base")
    alembic("upgrade", "head")  # 적재 없이 head만(적재 실패 흉내)
    with db.connect() as c:
        assert c.execute(text("select count(*) from ops.accounts")).scalar() == 0
    r = dbinit()
    assert r.returncode == 0 and "첫 설치 미완(0009, 업무 자료 0행)" in r.stdout and "history=300" in r.stdout, (
        r.stdout + r.stderr
    )
    with db.connect() as c:
        assert c.execute(text("select count(*) from knowledge.history_records")).scalar() == 300
    assert "이미 설치됨" in dbinit().stdout


def test_vc62_dbinit_checks_inputs_before_migration(db):
    """ES-21 review B2: 적재 입력(자료 폴더)이 없으면 migration 전에 멈춤 — DB는 빈 채(반쪽 설치 0)."""
    alembic("downgrade", "base")
    env = {**os.environ, "DATA_DIR": "/nonexistent-c1a15-data", "PYTHONPATH": str(SERVER / "src")}
    r = subprocess.run(["python", "-m", "c1a15.dbinit"], cwd=SERVER, env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "nonexistent-c1a15-data" in r.stderr, r.stderr[
        -400:
    ]  # 자료 폴더 없음 때문(review 2차 N3)
    no_pw = {k: v for k, v in env.items() if k != "C1A15_TECH_PASSWORD"} | {"DATA_DIR": str(SERVER.parent / "data")}
    r = subprocess.run(["python", "-m", "c1a15.dbinit"], cwd=SERVER, env=no_pw, capture_output=True, text=True)
    assert r.returncode != 0 and "C1A15_TECH_PASSWORD" in r.stderr, r.stderr[-400:]
    with db.connect() as c:
        assert not c.execute(text("select count(*) from alembic_version")).scalar()
    assert dbinit().returncode == 0  # 처음 상태로


def test_vc63_0009_gate_needs_allow_when_rows(db):
    """VC-63 · PR-36(EV-001 CH-40): 지울 표에 행이 있으면 C1A15_ALLOW_0009=yes 없이는 0009를 적용하지 않음 · 행 그대로."""
    alembic("downgrade", "0008")
    with db.begin() as c:
        c.execute(
            text(
                "insert into knowledge.data_releases (release_id, kind, recipe, recipe_sha256, embedding_model, embedding_dim, "
                "source, release_sha256) values ('rel-test', 'history', '{}', 'x', 'm', 1, '{}', 'y')"
            )
        )
    no_allow = {k: v for k, v in os.environ.items() if k != "C1A15_ALLOW_0009"}
    with pytest.raises(subprocess.CalledProcessError):
        alembic("upgrade", "head", env=no_allow)
    with db.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalar() == "0008"
        assert c.execute(text("select count(*) from knowledge.data_releases")).scalar() == 1
    alembic("upgrade", "head", env={**no_allow, "C1A15_ALLOW_0009": "yes"})
    with db.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalar() == "0009"
        assert c.execute(text("select to_regclass('knowledge.data_releases')")).scalar() is None


def test_adv03_plaintext_store_is_caught(db):
    """ADV-03: 저장 계층을 원문 저장으로 바꾼 결함을 시험 DB에 흉내 내면 저장값 검사가 실패를 잡아야 한다."""
    with db.begin() as c:
        c.execute(
            text(
                "insert into ops.accounts(user_id, display_name, role, password_hash) "
                "values ('adv.plain', 'x', 'tech', 'plain-pass')"
            )
        )
        stored = c.execute(text("select password_hash from ops.accounts where user_id='adv.plain'")).scalar()
        c.execute(text("delete from ops.accounts where user_id='adv.plain'"))
    assert not is_argon2id_hash_of(stored, "plain-pass")


# EV-001 ES-18: test_release_db.py를 지우며 옮겨 옴 — 릴리스와 무관한 ES-04 시험
def test_alarm_candidates_reason_check(db):
    """EV-001 ES-04(가): '사람 판단 필요' 기록 표 — 사유는 low_confidence · rnf · model_failure만.

    경계 길 알람의 ml 칸은 null.
    """
    import sqlalchemy

    ins = text(
        "insert into ops.alarm_candidates (equipment_id, reason, boundary_crossed, sensor_snapshot, event) "
        "values ('M-01', :r, false, '{}'::jsonb, '{}'::jsonb)"
    )
    with db.begin() as c:
        for r in ("low_confidence", "rnf", "model_failure"):
            c.execute(ins, {"r": r})
        assert (
            c.execute(text("select count(*) from ops.alarm_candidates where status = 'needs_human'")).scalar_one() >= 3
        )
    with pytest.raises(sqlalchemy.exc.IntegrityError), db.begin() as c:
        c.execute(ins, {"r": "boundary"})
    with db.begin() as c:
        c.execute(text("delete from ops.alarm_candidates"))
        assert c.execute(text("select count(*) from ops.alarms where ml is not null")).scalar_one() == 0
