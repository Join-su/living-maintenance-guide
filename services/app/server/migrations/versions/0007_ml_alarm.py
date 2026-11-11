"""EV-001 ES-04(CH-04): ML 알람 앞단 — 저장 자리(더하기만)

- ops.alarms.ml: ML 길 알람에 붙는 값(위험도 · Top-3 · 확신도 · 모델 release · 증상군 길),
  경계 길 알람은 null
- ops.alarm_candidates: 알람을 만들지 않은 '사람 판단 필요' 기록
  (사유 low_confidence · rnf · model_failure, Q-1 (나)) — 화면 0
이 판은 저장 자리만: 앱이 C1-a15 Pack 예측을 부르는 길(C1A15_ALARM=ml)은
앱 → Pack 연결 결정 뒤(답장 007-06 · 007-11-01).

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alarms", sa.Column("ml", JSONB(), nullable=True), schema="ops")
    op.create_table(
        "alarm_candidates",
        sa.Column("candidate_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("equipment_id", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("risk", sa.Float(), nullable=True),
        sa.Column("top_k", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("boundary_crossed", sa.Boolean(), nullable=False),
        sa.Column("model_release", sa.String(64), nullable=True),
        sa.Column("sensor_snapshot", JSONB(), nullable=False),
        sa.Column("event", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), server_default="needs_human", nullable=False),
        sa.Column("eval_mode", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("reason IN ('low_confidence', 'rnf', 'model_failure')", name="ck_alarm_candidates_reason"),
        sa.PrimaryKeyConstraint("candidate_id", name="pk_alarm_candidates"),
        schema="ops",
    )


def downgrade() -> None:
    op.drop_table("alarm_candidates", schema="ops")
    op.drop_column("alarms", "ml", schema="ops")
