"""PostgreSQL 1개 · schema 2개(ops · knowledge)(EP-C1-DB-001). 앱은 DB에 직접 붙지 않는다.

S-01(P-02)은 적재 대상 표만 만든다. 세션 · 호출 · 보고서 · 감사 표는 해당 slice의 migration으로 더한다.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "pk": "pk_%(table_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ix": "ix_%(table_name)s_%(column_0_name)s",
        }
    )


# ---------------------------------------------------------------- knowledge
class ManualSection(Base):
    __tablename__ = "manual_sections"
    __table_args__ = {"schema": "knowledge"}

    section_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    chapter: Mapped[int] = mapped_column(Integer)
    symptom_group: Mapped[str | None] = mapped_column(String(4))
    initial_grade: Mapped[str | None] = mapped_column(String(1))
    minutes: Mapped[int | None] = mapped_column(Integer)
    parent_section: Mapped[str | None] = mapped_column(String(16))  # 조치 절(x.y.2)의 상위 점검 절
    text: Mapped[str | None] = mapped_column(Text)  # 조치 절은 본문이 상위 절 안에 있어 비어 있다


class HistoryRecord(Base):
    __tablename__ = "history_records"
    __table_args__ = (
        CheckConstraint("source IN ('generated', 'approved_report')", name="source"),
        {"schema": "knowledge"},
    )

    record_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(String(16))
    symptom_group: Mapped[str] = mapped_column(String(4), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    narrative: Mapped[str] = mapped_column(Text)
    sensor_snapshot: Mapped[dict] = mapped_column(JSONB)
    record: Mapped[dict] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(16))
    provenance: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- ops
class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("role IN ('tech', 'lead')", name="role"), {"schema": "ops"})

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(8))
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GradeEntry(Base):
    __tablename__ = "grade_table"
    __table_args__ = (CheckConstraint("grade IN ('A', 'B', 'C', 'D')", name="grade"), {"schema": "ops"})

    section_id: Mapped[str] = mapped_column(ForeignKey("knowledge.manual_sections.section_id"), primary_key=True)
    grade: Mapped[str] = mapped_column(String(1))
    minutes: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[str] = mapped_column(String(32))


class EquipmentAssignment(Base):
    __tablename__ = "equipment_assignments"
    __table_args__ = {"schema": "ops"}

    equipment_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("ops.accounts.user_id"))


class CommonPrecheckItem(Base):
    __tablename__ = "common_precheck_items"
    __table_args__ = {"schema": "ops"}

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    text: Mapped[str] = mapped_column(Text)
    source_section: Mapped[str] = mapped_column(ForeignKey("knowledge.manual_sections.section_id"))


# ---------------------------------------------------------------- ops · S-02(P-06)
class LoginSession(Base):
    __tablename__ = "login_sessions"
    __table_args__ = (CheckConstraint("app IN ('tech', 'lead')", name="app"), {"schema": "ops"})

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # 원 세션 ID는 저장하지 않는다(sha256)
    user_id: Mapped[str] = mapped_column(ForeignKey("ops.accounts.user_id"))
    app: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Alarm(Base):
    __tablename__ = "alarms"
    __table_args__ = {"schema": "ops"}

    alarm_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(String(16))
    symptom_group: Mapped[str] = mapped_column(String(4))
    alarm: Mapped[dict] = mapped_column(JSONB)
    sensor_snapshot: Mapped[dict] = mapped_column(JSONB)
    trend: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    scenario_id: Mapped[str | None] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("ops.accounts.user_id"))
    status: Mapped[str] = mapped_column(String(16), server_default="new")
    change_seq: Mapped[int] = mapped_column(BigInteger)
    eval_mode: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))  # R12: 평가 실행기(P-16) 알람
    # EV-001 CH-04: ML 길 알람의 위험도 · Top-3 · 확신도 · 모델 release · 증상군 길(경계 길은 null)
    ml: Mapped[dict | None] = mapped_column(JSONB)
    event_id: Mapped[str | None] = mapped_column(String(64))  # EV-001 ES-07: 배포 서버 Run 참조(deploy 길만)
    run_id: Mapped[str | None] = mapped_column(String(64))
    deployment_id: Mapped[str | None] = mapped_column(String(64))


class AlarmCandidate(Base):
    """EV-001 CH-04 · Q-1 (나): 알람을 만들지 않은 '사람 판단 필요' 기록(화면 0 — 사람 판단 화면은 NX-05)."""

    __tablename__ = "alarm_candidates"
    __table_args__ = (
        CheckConstraint("reason IN ('low_confidence', 'rnf', 'model_failure')", name="reason"),
        {"schema": "ops"},
    )

    candidate_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(16))
    risk: Mapped[float | None] = mapped_column(Float)
    top_k: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    boundary_crossed: Mapped[bool] = mapped_column(Boolean)
    model_release: Mapped[str | None] = mapped_column(String(64))
    sensor_snapshot: Mapped[dict] = mapped_column(JSONB)
    event: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), server_default="needs_human")
    eval_mode: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event_id: Mapped[str | None] = mapped_column(String(64))  # EV-001 ES-07
    run_id: Mapped[str | None] = mapped_column(String(64))


class DiagSession(Base):
    __tablename__ = "diag_sessions"
    __table_args__ = (UniqueConstraint("alarm_id"), {"schema": "ops"})

    session_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    alarm_id: Mapped[int] = mapped_column(ForeignKey("ops.alarms.alarm_id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("ops.accounts.user_id"))
    symptom_group: Mapped[str] = mapped_column(String(4))
    similar: Mapped[list] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), server_default="in_progress")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    retrieval: Mapped[dict | None] = mapped_column(JSONB)  # EV-001 ES-07: 첫 Run 리트리버 표시(deploy 길만)
    start_run_id: Mapped[str | None] = mapped_column(String(64))
    deployment_id: Mapped[str | None] = mapped_column(String(64))


class SessionEvent(Base):
    """append-only 업무 기록. (session_id, seq)가 업무 idempotency key다(EP-C1-HITL-001)."""

    __tablename__ = "session_events"
    __table_args__ = (PrimaryKeyConstraint("session_id", "seq", name="pk_session_events"), {"schema": "ops"})

    session_id: Mapped[int] = mapped_column(ForeignKey("ops.diag_sessions.session_id"))
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB)
    actor: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    change_seq: Mapped[int] = mapped_column(BigInteger)


class AuditEntry(Base):
    __tablename__ = "audit_log"
    __table_args__ = {"schema": "ops"}

    audit_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(32))
    equipment_id: Mapped[str | None] = mapped_column(String(16))
    target: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONB)


class Call(Base):
    """파트장 호출(kind approval = C · D 승인 요청, handover = 숙련자 인계). 답은 파트장 앱(S-04 P-11)이 채운다."""

    __tablename__ = "calls"
    __table_args__ = (CheckConstraint("kind IN ('approval', 'handover')", name="kind"), {"schema": "ops"})

    call_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("ops.diag_sessions.session_id"))
    kind: Mapped[str] = mapped_column(String(16))
    section: Mapped[str | None] = mapped_column(String(16))
    grade: Mapped[str | None] = mapped_column(String(1))
    summary: Mapped[str] = mapped_column(Text)
    sent_by: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    answer: Mapped[dict | None] = mapped_column(JSONB)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correction: Mapped[dict | None] = mapped_column(JSONB)  # 조치 뒤 정정 호출이면 그 정정(S-05 P-14)
    corrections: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )  # 보낸 뒤 정비자 정정(AC-47-C2)
    change_seq: Mapped[int] = mapped_column(BigInteger)


class Report(Base):
    """확정한 정비 보고서(R10 8항목). 초안 · 수정은 업무 기록이 정본이고 확정 때 이 표에 남긴다."""

    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint("status IN ('awaiting_approval', 'approved', 'rejected')", name="status"),
        UniqueConstraint("session_id"),
        {"schema": "ops"},
    )

    report_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("ops.diag_sessions.session_id"))
    author: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))
    fields: Mapped[list] = mapped_column(JSONB)
    low_evidence: Mapped[bool] = mapped_column(Boolean)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 마지막 반려(사유 · 파트장 · 시각)
    approval: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 승인 결정(그대로 · 수정 후, S-04 P-11)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    change_seq: Mapped[int] = mapped_column(BigInteger)


class GradeChange(Base):
    """권한 등급 변경 기록(AC-25-C2: 누가 · 언제 · 전 · 후). 등급표(GradeTable)는 지금 값, 이 표는 이력."""

    __tablename__ = "grade_changes"
    __table_args__ = {"schema": "ops"}

    change_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    section_id: Mapped[str] = mapped_column(ForeignKey("knowledge.manual_sections.section_id"))
    before: Mapped[str] = mapped_column(String(1))
    after: Mapped[str] = mapped_column(String(1))
    changed_by: Mapped[str] = mapped_column(String(32))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
