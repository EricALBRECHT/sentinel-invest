"""company investment views

Revision ID: b6e1d9a42c70
Revises: a4c8e2b71f05
Create Date: 2026-10-05 20:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b6e1d9a42c70"
down_revision: Union[str, Sequence[str], None] = "a4c8e2b71f05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LONG = "('LOW', 'MODERATE', 'GOOD', 'HIGH', 'VERY_HIGH')"
_ENTRY = "('POOR', 'NEUTRAL', 'FAVORABLE', 'STRONG', 'EXTENDED_OR_EXCEPTIONAL')"
_READY = "('INCOMPLETE', 'PARTIAL', 'USABLE', 'COMPLETE')"


def upgrade() -> None:
    op.create_table(
        "company_investment_views",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("method", sa.String(length=32), server_default="investment_view_v1", nullable=False),
        sa.Column("quality_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("quality_confidence", sa.Integer(), nullable=True),
        sa.Column("opportunity_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("opportunity_confidence", sa.Integer(), nullable=True),
        sa.Column("opportunity_coverage", sa.Integer(), nullable=True),
        sa.Column("opportunity_coverage_status", sa.String(length=16), nullable=True),
        sa.Column("opportunity_ranking_eligible", sa.Boolean(), nullable=True),
        sa.Column("technical_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("technical_confidence", sa.Integer(), nullable=True),
        sa.Column("long_term_conviction_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("entry_attractiveness_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("analysis_readiness_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("long_term_conviction_label", sa.String(length=16), nullable=True),
        sa.Column("entry_attractiveness_label", sa.String(length=32), nullable=True),
        sa.Column("analysis_readiness_label", sa.String(length=16), nullable=True),
        sa.Column("components_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 100)",
            name="ck_investment_views_quality_score",
        ),
        sa.CheckConstraint(
            "opportunity_score IS NULL OR (opportunity_score >= 0 AND opportunity_score <= 100)",
            name="ck_investment_views_opportunity_score",
        ),
        sa.CheckConstraint(
            "technical_score IS NULL OR (technical_score >= 0 AND technical_score <= 100)",
            name="ck_investment_views_technical_score",
        ),
        sa.CheckConstraint(
            "long_term_conviction_score IS NULL OR (long_term_conviction_score >= 0 AND long_term_conviction_score <= 100)",
            name="ck_investment_views_long_term_conviction_score",
        ),
        sa.CheckConstraint(
            "entry_attractiveness_score IS NULL OR (entry_attractiveness_score >= 0 AND entry_attractiveness_score <= 100)",
            name="ck_investment_views_entry_attractiveness_score",
        ),
        sa.CheckConstraint(
            "analysis_readiness_score IS NULL OR (analysis_readiness_score >= 0 AND analysis_readiness_score <= 100)",
            name="ck_investment_views_analysis_readiness_score",
        ),
        sa.CheckConstraint(
            "quality_confidence IS NULL OR (quality_confidence >= 0 AND quality_confidence <= 100)",
            name="ck_investment_views_quality_confidence",
        ),
        sa.CheckConstraint(
            "opportunity_confidence IS NULL OR (opportunity_confidence >= 0 AND opportunity_confidence <= 100)",
            name="ck_investment_views_opportunity_confidence",
        ),
        sa.CheckConstraint(
            "technical_confidence IS NULL OR (technical_confidence >= 0 AND technical_confidence <= 100)",
            name="ck_investment_views_technical_confidence",
        ),
        sa.CheckConstraint(
            "opportunity_coverage IS NULL OR (opportunity_coverage >= 0 AND opportunity_coverage <= 100)",
            name="ck_investment_views_opportunity_coverage",
        ),
        sa.CheckConstraint(
            f"opportunity_coverage_status IS NULL OR opportunity_coverage_status IN {_READY}",
            name="ck_investment_views_coverage_status",
        ),
        sa.CheckConstraint(
            f"long_term_conviction_label IS NULL OR long_term_conviction_label IN {_LONG}",
            name="ck_investment_views_conviction_label",
        ),
        sa.CheckConstraint(
            f"entry_attractiveness_label IS NULL OR entry_attractiveness_label IN {_ENTRY}",
            name="ck_investment_views_entry_label",
        ),
        sa.CheckConstraint(
            f"analysis_readiness_label IS NULL OR analysis_readiness_label IN {_READY}",
            name="ck_investment_views_readiness_label",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "as_of_date",
            "method",
            name="uq_company_investment_views_company_date_method",
        ),
    )
    op.create_index("ix_company_investment_views_id", "company_investment_views", ["id"])
    op.create_index("ix_company_investment_views_company_id", "company_investment_views", ["company_id"])
    op.create_index("ix_company_investment_views_as_of_date", "company_investment_views", ["as_of_date"])


def downgrade() -> None:
    op.drop_index("ix_company_investment_views_as_of_date", table_name="company_investment_views")
    op.drop_index("ix_company_investment_views_company_id", table_name="company_investment_views")
    op.drop_index("ix_company_investment_views_id", table_name="company_investment_views")
    op.drop_table("company_investment_views")
