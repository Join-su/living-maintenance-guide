"""EV-001 ES-03(CH-05 · CH-06 · CH-08): data release — 이력 · 매뉴얼 조각을 hash 고정 판으로

- knowledge.data_releases: 판 하나 — 종류(history · manual), recipe · recipe_sha256, 임베딩 모델 · 차원,
  원문 출처 · 앞 판 · 판 hash · 미리 계산한 질의 벡터. 한 번 만든 판은 고치지 않는다(새 판을 더함, R12 승인도 새 판)
- knowledge.release_chunks: 판의 조각(조각 ID · 문서 · 원문 위치 · 본문 · 메타데이터 · 임베딩)
- knowledge.active_releases: 종류마다 지금 검색이 읽는 판
기존 표(history_records · history_embeddings · manual_sections)는 그대로 둔다(더하기만).

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table("active_releases", schema="knowledge")
    op.drop_table("release_chunks", schema="knowledge")
    op.drop_table("data_releases", schema="knowledge")
