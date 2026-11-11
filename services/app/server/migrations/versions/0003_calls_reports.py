"""S-03(P-09): 파트장 호출(승인 요청 · 숙련자 인계)과 정비 보고서

호출과 보고서는 세션을 넘어 파트장 앱(S-04)이 목록으로 읽는다. 세션 안의 사실(보냄 · 답 · 수정 · 확정)은
업무 기록(session_events)이 정본이고, 이 표는 목록 · 상태 조회용 투영이다.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calls",
        sa.Column("call_id", sa.BigInteger(), sa.Identity()),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("section", sa.String(16)),
        sa.Column("grade", sa.String(1)),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("sent_by", sa.String(32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("answer", JSONB()),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.Column("change_seq", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["ops.diag_sessions.session_id"], name="fk_calls_session_id"),
        sa.CheckConstraint("kind IN ('approval', 'handover')", name="ck_calls_kind"),
        sa.PrimaryKeyConstraint("call_id", name="pk_calls"),
        schema="ops",
    )
    op.create_table(
        "reports",
        sa.Column("report_id", sa.BigInteger(), sa.Identity()),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("author", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("fields", JSONB(), nullable=False),
        sa.Column("low_evidence", sa.Boolean(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("rejection", JSONB()),  # 마지막 반려(사유 · 파트장 · 시각) — 쓰는 쪽은 S-04 P-11
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("change_seq", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["ops.diag_sessions.session_id"], name="fk_reports_session_id"),
        sa.CheckConstraint("status IN ('awaiting_approval', 'approved', 'rejected')", name="ck_reports_status"),
        sa.PrimaryKeyConstraint("report_id", name="pk_reports"),
        sa.UniqueConstraint("session_id", name="uq_reports_session_id"),
        schema="ops",
    )


def downgrade() -> None:
    op.drop_table("reports", schema="ops")
    op.drop_table("calls", schema="ops")
