"""API 공통: DB · 매뉴얼 지식 · 인증(앱별 쿠키 · X-App · 역할 대조). 이력 리트리버는 배포 서버 Pack(EV-001 CH-31)."""

import hashlib
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from sqlalchemy import Connection, select, text

from c1a15.config import require_env
from c1a15.db import models as m
from c1a15.db.session import engine as make_engine
from c1a15.engine import alarm_rules, knowledge

SESSION_HOURS = 12  # 야간 교대 한 번(EP-C1-AUTH-002)
COOKIE = {"tech": "tech_session", "lead": "lead_session"}


@lru_cache
def engine():
    return make_engine()


def conn():
    """요청 하나 = transaction 하나. after_commit에 넣은 일은 commit이 끝난 뒤에만 한다(실패하면 버림).

    쓰는 곳은 모두 Depends(conn, scope="function") — commit이 응답을 보내기 전에 끝나야 다음 요청이 방금 쓴 기록을 본다
    (FastAPI 기본 scope='request'는 응답 뒤에 commit, S-06 평가 실행에서 발견).
    """
    hooks: list = []
    with engine().begin() as c:
        c.info["after_commit"] = hooks
        try:
            yield c
        finally:
            c.info.pop("after_commit", None)
    for fn in hooks:
        fn()


@lru_cache
def manual() -> knowledge.ManualKnowledge:
    with engine().connect() as c:
        rows = c.execute(select(m.ManualSection.__table__)).all()
    sections = [
        {
            "id": r.section_id,
            "title": r.title,
            "kind": r.kind,
            "chapter": r.chapter,
            "group": r.symptom_group,
            "grade": r.initial_grade,
            "minutes": r.minutes,
            "parent": r.parent_section,
            "text": r.text or "",
        }
        for r in rows
    ]
    return knowledge.from_sections(sections)


@lru_cache
def alarm_table() -> dict:
    """FLOW-01 경계 판정 표(적재 자료 data/alarm_rules.json)."""
    return alarm_rules.load(Path(require_env("DATA_DIR")) / "alarm_rules.json")


@lru_cache
def _chapter_titles() -> dict[int, tuple[str, str]]:
    """매뉴얼 장 제목 '## 4. 온도 차 저하 (냉각 계통)' → {4: ('온도 차 저하', '냉각 계통')}(적재 자료 MG-MM.md)."""
    text = (Path(require_env("DATA_DIR")) / "manuals" / "MG-MM.md").read_text(encoding="utf-8")
    return {int(n): (name, system) for n, name, system in re.findall(r"^## (\d+)\. (.+?) \((.+)\)$", text, flags=re.M)}


def _group_chapter() -> dict[str, int]:
    return {s["group"]: s["chapter"] for s in manual().sections.values() if s.get("group")}


def group_names() -> dict[str, str]:
    return {g: _chapter_titles()[ch][0] for g, ch in _group_chapter().items()}


def group_systems() -> dict[str, str]:
    """증상군 계통 = 매뉴얼 장 제목 괄호(예 '냉각 계통')."""
    return {g: _chapter_titles()[ch][1] for g, ch in _group_chapter().items()}


@lru_cache
def equipment_types() -> dict[str, str]:
    """설비 종류 = 정비 이력의 equipment_type(설비별)."""
    with engine().connect() as c:
        rows = c.execute(select(m.HistoryRecord.equipment_id, m.HistoryRecord.record["equipment_type"].astext)).all()
    return {e: t for e, t in rows if t}


def default_equipment_type() -> str:
    types = list(equipment_types().values())
    return max(set(types), key=types.count) if types else ""


@lru_cache
def scenarios() -> dict[str, dict]:
    """시연 각본(R11 재가동 후 표시 값). 각본에는 숨은 원인이 없다."""
    data = json.loads((Path(require_env("DATA_DIR")) / "demo" / "scenarios.json").read_text(encoding="utf-8"))
    return {s["scenario_id"]: s for s in data["scenarios"]}


def reset_caches() -> None:
    for f in (engine, manual, alarm_table, _chapter_titles, equipment_types, scenarios):
        f.cache_clear()


def grades(c: Connection) -> tuple[dict[str, str], dict[str, int]]:
    """R13: 판정마다 현재 등급표를 읽는다."""
    rows = c.execute(select(m.GradeEntry.section_id, m.GradeEntry.grade, m.GradeEntry.minutes)).all()
    return {r[0]: r[1] for r in rows}, {r[0]: r[2] for r in rows}


def next_change_seq(c: Connection) -> int:
    return c.execute(text("select nextval('ops.change_seq')")).scalar_one()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(c: Connection, user_id: str, app: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    c.execute(
        m.LoginSession.__table__.insert().values(
            token_hash=token_hash(token), user_id=user_id, app=app, expires_at=now + timedelta(hours=SESSION_HOURS)
        )
    )
    return token


def request_app(request: Request) -> str:
    """nginx가 server 블록별로 덮어쓰는 앱 표시. 없거나 모르는 값이면 거부(API 포트는 밖에 열지 않는다)."""
    app = request.headers.get("x-app")
    if app not in COOKIE:
        raise HTTPException(status_code=403, detail="unknown_app")
    return app


class User:
    def __init__(self, user_id: str, role: str, app: str):
        self.user_id, self.role, self.app = user_id, role, app


def current_user(request: Request, c: Connection = Depends(conn, scope="function")) -> User:
    """앱 표시 · 그 앱의 쿠키 이름 · 세션의 앱 · 계정 역할이 모두 맞아야 한다.

    FP-01: 쿠키는 host 단위라 두 앱 쿠키가 함께 온다.
    """
    app = request_app(request)
    token = request.cookies.get(COOKIE[app])
    if not token:
        raise HTTPException(status_code=401, detail="no_session")
    row = c.execute(
        select(
            m.LoginSession.user_id,
            m.LoginSession.app,
            m.LoginSession.expires_at,
            m.LoginSession.revoked_at,
            m.Account.role,
        )
        .join(m.Account, m.Account.user_id == m.LoginSession.user_id)
        .where(m.LoginSession.token_hash == token_hash(token))
    ).first()
    if not row or row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=401, detail="invalid_session")
    if row.app != app or row.role != app:
        raise HTTPException(status_code=403, detail="wrong_app")
    return User(row.user_id, row.role, app)


def tech_user(user: User = Depends(current_user)) -> User:
    if user.app != "tech":
        raise HTTPException(status_code=403, detail="tech_only")
    return user


def internal_token(request: Request) -> None:
    """알람 등록(시연 알람 발생기)은 nginx를 거치지 않는 내부 호출만: X-App 없음 + 내부 토큰."""
    expected = os.environ.get("C1A15_INTERNAL_TOKEN", "")
    if (
        request.headers.get("x-app") is not None
        or not expected
        or not secrets.compare_digest(request.headers.get("x-internal-token", ""), expected)
    ):
        raise HTTPException(status_code=403, detail="internal_only")
