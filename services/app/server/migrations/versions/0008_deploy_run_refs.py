"""EV-001 ES-07(CH-16): 배포 서버 Run 참조 — 저장 자리(더하기만, 모두 nullable)

- ops.alarms: event_id(Pack 알람 Run의 사건 id) · run_id · deployment_id
- ops.alarm_candidates: event_id · run_id
- ops.diag_sessions: retrieval(첫 Run의 리트리버 표시 + 앱 임시 release id) · start_run_id · deployment_id
- 단계마다의 run_id · deployment_id는 session_events(step_opened).payload 안(표 변경 0)
기본 길(C1A15_JUDGE=inproc)은 이 칸을 쓰지 않는다.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in ("event_id", "run_id", "deployment_id"):
        op.add_column("alarms", sa.Column(col, sa.String(64), nullable=True), schema="ops")
    for col in ("event_id", "run_id"):
        op.add_column("alarm_candidates", sa.Column(col, sa.String(64), nullable=True), schema="ops")
    op.add_column("diag_sessions", sa.Column("retrieval", JSONB(), nullable=True), schema="ops")
    op.add_column("diag_sessions", sa.Column("start_run_id", sa.String(64), nullable=True), schema="ops")
    op.add_column("diag_sessions", sa.Column("deployment_id", sa.String(64), nullable=True), schema="ops")


def downgrade() -> None:
    for col in ("deployment_id", "start_run_id", "retrieval"):
        op.drop_column("diag_sessions", col, schema="ops")
    for col in ("run_id", "event_id"):
        op.drop_column("alarm_candidates", col, schema="ops")
    for col in ("deployment_id", "run_id", "event_id"):
        op.drop_column("alarms", col, schema="ops")
