"""ai document analyses

Revision ID: b2d4e9a71c03
Revises: f1a8c3e56d20
Create Date: 2026-10-06 16:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b2d4e9a71c03"
down_revision = "f1a8c3e56d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_document_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("analysis_confidence", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("worker_name", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCESS', 'INVALID_OUTPUT', 'FAILED')",
            name="ck_ai_document_analyses_status",
        ),
        sa.CheckConstraint(
            "analysis_confidence IS NULL OR (analysis_confidence >= 0 AND analysis_confidence <= 100)",
            name="ck_ai_document_analyses_confidence",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["external_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "model_name",
            "prompt_version",
            name="uq_ai_document_analyses_document_model_prompt",
        ),
    )
    op.create_index("ix_ai_document_analyses_document_id", "ai_document_analyses", ["document_id"])
    op.create_index("ix_ai_document_analyses_status", "ai_document_analyses", ["status"])
    op.create_index("ix_ai_document_analyses_completed_at", "ai_document_analyses", ["completed_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_document_analyses_completed_at", table_name="ai_document_analyses")
    op.drop_index("ix_ai_document_analyses_status", table_name="ai_document_analyses")
    op.drop_index("ix_ai_document_analyses_document_id", table_name="ai_document_analyses")
    op.drop_table("ai_document_analyses")
