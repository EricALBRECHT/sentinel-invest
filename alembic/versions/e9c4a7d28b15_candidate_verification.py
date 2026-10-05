"""candidate verification and promotion

Revision ID: e9c4a7d28b15
Revises: d8e4b2c91a07
Create Date: 2026-10-05 21:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e9c4a7d28b15"
down_revision: Union[str, Sequence[str], None] = "d8e4b2c91a07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENTITY_TYPES = "('PUBLIC_COMPANY', 'PRIVATE_COMPANY', 'SUBSIDIARY', 'BRAND', 'UNKNOWN')"
_VERIFICATION = "('UNVERIFIED', 'PARTIAL', 'VERIFIED', 'REJECTED')"


def upgrade() -> None:
    op.add_column("discovered_companies", sa.Column("canonical_name", sa.String(length=255), nullable=True))
    op.add_column("discovered_companies", sa.Column("isin", sa.String(length=12), nullable=True))
    op.add_column("discovered_companies", sa.Column("exchange", sa.String(length=64), nullable=True))
    op.add_column("discovered_companies", sa.Column("sec_cik", sa.String(length=10), nullable=True))
    op.add_column(
        "discovered_companies",
        sa.Column("entity_type", sa.String(length=32), server_default="UNKNOWN", nullable=False),
    )
    op.add_column(
        "discovered_companies",
        sa.Column("verification_status", sa.String(length=16), server_default="UNVERIFIED", nullable=False),
    )
    op.add_column(
        "discovered_companies",
        sa.Column("verification_confidence", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("discovered_companies", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "discovered_companies",
        sa.Column(
            "verification_evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("discovered_companies", sa.Column("promoted_company_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_discovered_companies_promoted_company_id",
        "discovered_companies",
        "companies",
        ["promoted_company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_discovered_companies_promoted_company_id",
        "discovered_companies",
        ["promoted_company_id"],
    )
    op.create_check_constraint(
        "ck_discovered_companies_entity_type",
        "discovered_companies",
        f"entity_type IN {_ENTITY_TYPES}",
    )
    op.create_check_constraint(
        "ck_discovered_companies_verification",
        "discovered_companies",
        f"verification_status IN {_VERIFICATION}",
    )
    op.create_check_constraint(
        "ck_discovered_companies_verification_confidence",
        "discovered_companies",
        "verification_confidence >= 0 AND verification_confidence <= 100",
    )


def downgrade() -> None:
    op.drop_constraint("ck_discovered_companies_verification_confidence", "discovered_companies", type_="check")
    op.drop_constraint("ck_discovered_companies_verification", "discovered_companies", type_="check")
    op.drop_constraint("ck_discovered_companies_entity_type", "discovered_companies", type_="check")
    op.drop_index("ix_discovered_companies_promoted_company_id", table_name="discovered_companies")
    op.drop_constraint("fk_discovered_companies_promoted_company_id", "discovered_companies", type_="foreignkey")
    op.drop_column("discovered_companies", "promoted_company_id")
    op.drop_column("discovered_companies", "verification_evidence_json")
    op.drop_column("discovered_companies", "verified_at")
    op.drop_column("discovered_companies", "verification_confidence")
    op.drop_column("discovered_companies", "verification_status")
    op.drop_column("discovered_companies", "entity_type")
    op.drop_column("discovered_companies", "sec_cik")
    op.drop_column("discovered_companies", "exchange")
    op.drop_column("discovered_companies", "isin")
    op.drop_column("discovered_companies", "canonical_name")
