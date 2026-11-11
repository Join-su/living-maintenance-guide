"""S-05(P-14): 정비자 정정 — 호출에 붙는 정정

- ops.calls.correction: 조치 뒤 정정으로 나간 '정정 호출'(T10 / summary_corrected)이면
  그 정정(절 · 전 · 후 · 사유 · 시각)
- ops.calls.corrections: 보낸 뒤 정비자가 정정한 내용(AC-47-C2, 결정 019 ② ③) — L04 '보낸 뒤 정비자 정정' 상자

정정 자체는 업무 기록(ops.session_events)의 덧붙임 기록(type correction · note_append)이 정본이다.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("correction", JSONB()), schema="ops")
    op.add_column(
        "calls", sa.Column("corrections", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False), schema="ops"
    )


def downgrade() -> None:
    op.drop_column("calls", "corrections", schema="ops")
    op.drop_column("calls", "correction", schema="ops")
