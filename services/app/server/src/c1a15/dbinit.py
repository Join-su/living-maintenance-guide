"""DB 준비(compose `init`, EV-001 CH-39): python -m c1a15.dbinit

- 빈 DB(alembic_version 표가 없거나 행이 없음) = 첫 설치
  → 적재 입력 확인 → migration head + 적재기.
- 판 = 이 이미지의 head인데 업무 자료(매뉴얼 · 계정 · 이력)가 모두 0행 = 첫 설치 미완(적재 실패 뒤)
  → 적재를 다시(지울 자료 0).
- 이미 설치된 DB = migration · 적재 0. 판이 이미지 head와 다르면 끝(1)
  — compose `api`는 init이 성공해야 뜨므로 앱이 서지 않는다.
  사람 migration = 일회용 `migrate` 서비스(compose profile `migrate`). 운영 자료(승인 이력 · 등급 · 계정 · 운영 기록)는
  띄울 때마다 다시 넣지 않는다(PR-35).
"""

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import text

from c1a15.config import require_env
from c1a15.db.session import engine

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
MIGRATE_HINT = "docker compose --profile migrate run --rm migrate"


def alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "migrations"))
    return cfg


def current_revision() -> str | None:
    """설치된 판 — 표가 없거나 행이 없으면 None(빈 DB)."""
    with engine().connect() as c:
        if c.execute(text("select to_regclass('public.alembic_version')")).scalar() is None:
            return None
        return c.execute(text("select version_num from alembic_version")).scalar()


def business_rows() -> int:
    with engine().connect() as c:
        return c.execute(
            text(
                "select (select count(*) from knowledge.manual_sections) + (select count(*) from ops.accounts)"
                " + (select count(*) from knowledge.history_records)"
            )
        ).scalar()


def check_inputs() -> None:
    """적재 입력을 migration 전에 읽어 본다 — 자료 · 비밀번호가 없으면 DB를 건드리기 전에 멈춤(ES-21 review B2)."""
    from c1a15.loader import core

    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    core.read_manual(data_dir)
    core.read_history(data_dir)
    core.account_seeds(require_env("C1A15_TECH_PASSWORD"), require_env("C1A15_LEAD_PASSWORD"))


def load() -> str:
    """적재기 — 한 줄 요약을 돌려준다(LangGraph checkpoint 표는 ES-23에서 뺌)."""
    from c1a15.loader.__main__ import main as loader

    out = io.StringIO()
    with redirect_stdout(out):
        loader()
    lines = out.getvalue().splitlines()
    return next((line.removeprefix("loaded ") for line in lines if line.startswith("loaded ")), "")


def main() -> int:
    cfg = alembic_config()
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    now = current_revision()
    if now is None:
        check_inputs()
        command.upgrade(cfg, "head")
        print(f"dbinit: 빈 DB — migration head({head}) · 적재 {load()}", flush=True)
        return 0
    if now == head:
        if business_rows() == 0:
            check_inputs()
            print(f"dbinit: 첫 설치 미완({now}, 업무 자료 0행) — 적재 다시 · 적재 {load()}", flush=True)
            return 0
        print(f"dbinit: 이미 설치됨({now}) — migration · 적재 0", flush=True)
        return 0
    try:
        script.get_revision(now)
    except CommandError:
        print(f"dbinit: DB 판 {now}은 이 이미지({head})가 모르는 판 — 이 이미지로 띄우지 않음", flush=True)
        return 1
    print(f"dbinit: DB 판 {now} ≠ 이미지 {head} — 사람 migration 필요: {MIGRATE_HINT}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
