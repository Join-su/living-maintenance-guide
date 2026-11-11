"""S-02(P-06): 로그인 세션 · 알람 · 진단 세션 · append-only 업무 기록 · 감사

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_sessions",
        sa.Column("token_hash", sa.String(64)),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("app", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("token_hash", name="pk_login_sessions"),
        sa.ForeignKeyConstraint(["user_id"], ["ops.accounts.user_id"], name="fk_login_sessions_user_id"),
        sa.CheckConstraint("app IN ('tech', 'lead')", name="ck_login_sessions_app"),
        schema="ops",
    )
    op.create_table(
        "alarms",
        sa.Column("alarm_id", sa.BigInteger(), sa.Identity()),
        sa.Column("equipment_id", sa.String(16), nullable=False),
        sa.Column("symptom_group", sa.String(4), nullable=False),
        sa.Column("alarm", JSONB(), nullable=False),
        sa.Column("sensor_snapshot", JSONB(), nullable=False),
        sa.Column("trend", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("scenario_id", sa.String(32)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_to", sa.String(32)),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("change_seq", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("alarm_id", name="pk_alarms"),
        sa.ForeignKeyConstraint(["assigned_to"], ["ops.accounts.user_id"], name="fk_alarms_assigned_to"),
        schema="ops",
    )
    op.execute("CREATE SEQUENCE ops.change_seq")
    op.create_table(
        "diag_sessions",
        sa.Column("session_id", sa.BigInteger(), sa.Identity()),
        sa.Column("alarm_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("symptom_group", sa.String(4), nullable=False),
        sa.Column("similar", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="in_progress"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("session_id", name="pk_diag_sessions"),
        sa.UniqueConstraint("alarm_id", name="uq_diag_sessions_alarm_id"),
        sa.ForeignKeyConstraint(["alarm_id"], ["ops.alarms.alarm_id"], name="fk_diag_sessions_alarm_id"),
        sa.ForeignKeyConstraint(["user_id"], ["ops.accounts.user_id"], name="fk_diag_sessions_user_id"),
        schema="ops",
    )
    op.create_table(
        "session_events",
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("actor", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("change_seq", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("session_id", "seq", name="pk_session_events"),
        sa.ForeignKeyConstraint(["session_id"], ["ops.diag_sessions.session_id"], name="fk_session_events_session_id"),
        schema="ops",
    )
    op.create_table(
        "audit_log",
        sa.Column("audit_id", sa.BigInteger(), sa.Identity()),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor", sa.String(32), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("equipment_id", sa.String(16)),
        sa.Column("target", sa.String(64), nullable=False),
        sa.Column("detail", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("audit_id", name="pk_audit_log"),
        schema="ops",
    )


def downgrade() -> None:
    op.drop_table("audit_log", schema="ops")
    op.drop_table("session_events", schema="ops")
    op.drop_table("diag_sessions", schema="ops")
    op.execute("DROP SEQUENCE ops.change_seq")
    op.drop_table("alarms", schema="ops")
    op.drop_table("login_sessions", schema="ops")
