"""S-04(P-11): 파트장 검토 결정 · 등급 변경 기록 · 평가 모드 표시

- ops.reports.approval: 승인 결정(그대로 · 수정 후, 파트장 · 시각 · 고친 항목 전 → 후 · 메모)
- ops.alarms.eval_mode: 평가 실행기(P-16)가 만든 알람 — 그 보고서는 승인해도 이력 · 색인에 넣지 않는다(R12, AC-09-C2)
- ops.grade_changes: 권한 등급 변경 기록(누가 · 언제 · 전 · 후, AC-25-C2)

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("approval", JSONB()), schema="ops")
    op.add_column(
        "alarms", sa.Column("eval_mode", sa.Boolean(), server_default=sa.false(), nullable=False), schema="ops"
    )
    op.create_table(
        "grade_changes",
        sa.Column("change_id", sa.BigInteger(), sa.Identity()),
        sa.Column("section_id", sa.String(16), nullable=False),
        sa.Column("before", sa.String(1), nullable=False),
        sa.Column("after", sa.String(1), nullable=False),
        sa.Column("changed_by", sa.String(32), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["section_id"], ["knowledge.manual_sections.section_id"], name="fk_grade_changes_section_id"
        ),
        sa.PrimaryKeyConstraint("change_id", name="pk_grade_changes"),
        schema="ops",
    )


def downgrade() -> None:
    op.drop_table("grade_changes", schema="ops")
    op.drop_column("alarms", "eval_mode", schema="ops")
    op.drop_column("reports", "approval", schema="ops")
