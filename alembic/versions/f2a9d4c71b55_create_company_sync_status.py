"""create company_sync_status

Revision ID: f2a9d4c71b55
Revises: e1b6c8d24a30
Create Date: 2026-10-05 18:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f2a9d4c71b55"
down_revision: Union[str, Sequence[str], None] = "e1b6c8d24a30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_sync_status",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_message", sa.String(length=300), nullable=True),
        sa.Column("last_result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "source", name="uq_company_sync_status_company_source"),
    )
    op.create_index("ix_company_sync_status_id", "company_sync_status", ["id"])
    op.create_index("ix_company_sync_status_company_id", "company_sync_status", ["company_id"])
    op.alter_column("company_sync_status", "consecutive_failures", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_company_sync_status_company_id", table_name="company_sync_status")
    op.drop_index("ix_company_sync_status_id", table_name="company_sync_status")
    op.drop_table("company_sync_status")
