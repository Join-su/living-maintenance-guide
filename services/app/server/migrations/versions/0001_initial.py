"""S-01(P-02): ops · knowledge schema, pgvector, 적재 대상 표

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS knowledge")
    op.execute("CREATE SCHEMA IF NOT EXISTS ops")
    op.create_table(
        "manual_sections",
        sa.Column("section_id", sa.String(16), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("chapter", sa.Integer(), nullable=False),
        sa.Column("symptom_group", sa.String(4)),
        sa.Column("initial_grade", sa.String(1)),
        sa.Column("minutes", sa.Integer()),
        sa.Column("parent_section", sa.String(16)),
        sa.Column("text", sa.Text()),
        sa.PrimaryKeyConstraint("section_id", name="pk_manual_sections"),
        schema="knowledge",
    )
    op.create_table(
        "history_records",
        sa.Column("record_id", sa.String(32)),
        sa.Column("equipment_id", sa.String(16), nullable=False),
        sa.Column("symptom_group", sa.String(4), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column("sensor_snapshot", JSONB(), nullable=False),
        sa.Column("record", JSONB(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("record_id", name="pk_history_records"),
        sa.CheckConstraint("source IN ('generated', 'approved_report')", name="ck_history_records_source"),
        schema="knowledge",
    )
    op.create_index("ix_history_records_symptom_group", "history_records", ["symptom_group"], schema="knowledge")
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
        "accounts",
        sa.Column("user_id", sa.String(32)),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", sa.String(8), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name="pk_accounts"),
        sa.CheckConstraint("role IN ('tech', 'lead')", name="ck_accounts_role"),
        schema="ops",
    )
    op.create_table(
        "grade_table",
        sa.Column("section_id", sa.String(16)),
        sa.Column("grade", sa.String(1), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("section_id", name="pk_grade_table"),
        sa.ForeignKeyConstraint(
            ["section_id"], ["knowledge.manual_sections.section_id"], name="fk_grade_table_section_id"
        ),
        sa.CheckConstraint("grade IN ('A', 'B', 'C', 'D')", name="ck_grade_table_grade"),
        schema="ops",
    )
    op.create_table(
        "equipment_assignments",
        sa.Column("equipment_id", sa.String(16)),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("equipment_id", name="pk_equipment_assignments"),
        sa.ForeignKeyConstraint(["user_id"], ["ops.accounts.user_id"], name="fk_equipment_assignments_user_id"),
        schema="ops",
    )
    op.create_table(
        "common_precheck_items",
        sa.Column("seq", sa.Integer(), autoincrement=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source_section", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("seq", name="pk_common_precheck_items"),
        sa.ForeignKeyConstraint(
            ["source_section"], ["knowledge.manual_sections.section_id"], name="fk_common_precheck_items_source_section"
        ),
        schema="ops",
    )


def downgrade() -> None:
    op.drop_table("common_precheck_items", schema="ops")
    op.drop_table("equipment_assignments", schema="ops")
    op.drop_table("grade_table", schema="ops")
    op.drop_table("accounts", schema="ops")
    op.drop_table("history_embeddings", schema="knowledge")
    op.drop_index("ix_history_records_symptom_group", table_name="history_records", schema="knowledge")
    op.drop_table("history_records", schema="knowledge")
    op.drop_table("manual_sections", schema="knowledge")
    op.execute("DROP SCHEMA IF EXISTS ops")
    op.execute("DROP SCHEMA IF EXISTS knowledge")
    op.execute("DROP EXTENSION IF EXISTS vector")
