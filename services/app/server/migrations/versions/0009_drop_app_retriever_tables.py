"""EV-001 ES-19(CH-34): 앱 리트리버 · release 표 지움 — 상위 10은 배포 서버 Pack(start), R12는 받은 편지(ES-15 ~ 18)

- 지움: knowledge.active_releases · release_chunks · data_releases(0006) · history_embeddings(0001)
- 남김: knowledge.history_records · manual_sections(진단 카드 · 기록 창 · 받은 편지 · 매뉴얼)
- pgvector 확장은 그대로(0001이 만들고 base downgrade가 지움)
- downgrade = 네 표를 빈 채로 다시 만든다(자료는 되살리지 않음 — 되돌림은 앞 commit의 적재기를 다시 돌림, S3-Q5)
- 맥 시험 · 시연 DB만. VPS 운영 DB 적용은 사용자 따로 승인.
- 운영 문(EV-001 ES-21 CH-40): 네 표에 행이 있으면 C1A15_ALLOW_0009=yes 없이는 멈춤
  — 빈 표(시험 · 첫 설치)는 그대로 지나감.

Revision ID: 0009
Revises: 0008
"""

import os

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


TABLES = ("history_embeddings", "data_releases", "release_chunks", "active_releases")


def upgrade() -> None:
    # 운영 문(EV-001 CH-40, S3-Q5): 지울 표에 자료가 있으면 사람이 정한 때만 — 백업 뒤 C1A15_ALLOW_0009=yes
    rows = {t: op.get_bind().execute(sa.text(f"select count(*) from knowledge.{t}")).scalar() for t in TABLES}
    if sum(rows.values()) and os.environ.get("C1A15_ALLOW_0009") != "yes":
        counts = " ".join(f"{t}={n}" for t, n in rows.items())
        raise RuntimeError(f"0009: 지울 표에 행이 있음({counts}) — 백업 뒤 C1A15_ALLOW_0009=yes 로 다시")
    op.drop_table("active_releases", schema="knowledge")
    op.drop_table("release_chunks", schema="knowledge")
    op.drop_table("data_releases", schema="knowledge")
    op.drop_table("history_embeddings", schema="knowledge")


def downgrade() -> None:
    # 0001 · 0006과 같은 모양(이름 · 제약 그대로), 빈 표
    op.create_table(
        "history_embeddings",
        sa.Column("record_id", sa.String(32)),
        sa.Column("embedding", Vector(256), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("record_id", name="pk_history_embeddings"),
        sa.ForeignKeyConstraint(
            ["record_id"], ["knowledge.history_records.record_id"], name="fk_history_embeddings_record_id"
        ),
        schema="knowledge",
    )
    op.create_table(
        "data_releases",
        sa.Column("release_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("recipe", JSONB(), nullable=False),
        sa.Column("recipe_sha256", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(64), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("source", JSONB(), nullable=False),
        sa.Column("parent_release_id", sa.String(64), nullable=True),
        sa.Column("release_sha256", sa.String(64), nullable=False),
        sa.Column("queries", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('history', 'manual')", name="ck_data_releases_kind"),
        sa.PrimaryKeyConstraint("release_id", name="pk_data_releases"),
        schema="knowledge",
    )
    op.create_table(
        "release_chunks",
        sa.Column("release_id", sa.String(64), nullable=False),
        sa.Column("chunk_id", sa.String(64), nullable=False),
        sa.Column("doc", sa.String(16), nullable=False),
        sa.Column("locator", JSONB(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("metadata", JSONB(), nullable=False),
        sa.Column("embedding", Vector(256), nullable=False),
        sa.ForeignKeyConstraint(
            ["release_id"], ["knowledge.data_releases.release_id"], name="fk_release_chunks_release_id"
        ),
        sa.PrimaryKeyConstraint("release_id", "chunk_id", name="pk_release_chunks"),
        schema="knowledge",
    )
    op.create_table(
        "active_releases",
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("release_id", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["release_id"], ["knowledge.data_releases.release_id"], name="fk_active_releases_release_id"
        ),
        sa.PrimaryKeyConstraint("kind", name="pk_active_releases"),
        schema="knowledge",
    )
