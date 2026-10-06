"""ai analysis runtime provenance

Revision ID: c5e1b8d24a70
Revises: b2d4e9a71c03
Create Date: 2026-10-06 16:50:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c5e1b8d24a70"
down_revision = "b2d4e9a71c03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_document_analyses",
        sa.Column("runtime_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_document_analyses", "runtime_json")
