"""create opportunity profile and scores

Revision ID: d7f2b9c41e18
Revises: c4e8a1b73d20
Create Date: 2026-10-05 18:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d7f2b9c41e18"
down_revision: Union[str, Sequence[str], None] = "c4e8a1b73d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_opportunity_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("megatrend_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("market_growth_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("market_size_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("market_penetration_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("strategic_position_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("bottleneck_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("supplier_leverage_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("customer_diversification_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("innovation_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("rd_intensity_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("capacity_expansion_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("geographic_expansion_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("size_runway_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("competitive_moat_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("competition_risk_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("megatrends_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("strategic_roles_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("customers_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("suppliers_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("data_confidence", sa.Integer(), nullable=True),
        sa.Column("analysis_date", sa.Date(), nullable=True),
        sa.Column("method_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id"),
    )
    op.create_index("ix_company_opportunity_profiles_id", "company_opportunity_profiles", ["id"])
    op.create_index(
        "ix_company_opportunity_profiles_company_id",
        "company_opportunity_profiles",
        ["company_id"],
        unique=True,
    )
    op.create_table(
        "company_opportunity_scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("score_date", sa.Date(), nullable=False),
        sa.Column("opportunity_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("opportunity_confidence_score", sa.Integer(), nullable=False),
        sa.Column("growth_runway_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("size_runway_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("megatrend_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("market_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("strategic_position_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("bottleneck_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("innovation_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("moat_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("expansion_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("components_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("method_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "score_date",
            "method_version",
            name="uq_company_opportunity_scores_company_date_version",
        ),
    )
    op.create_index("ix_company_opportunity_scores_id", "company_opportunity_scores", ["id"])
    op.create_index("ix_company_opportunity_scores_company_id", "company_opportunity_scores", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_company_opportunity_scores_company_id", table_name="company_opportunity_scores")
    op.drop_index("ix_company_opportunity_scores_id", table_name="company_opportunity_scores")
    op.drop_table("company_opportunity_scores")
    op.drop_index("ix_company_opportunity_profiles_company_id", table_name="company_opportunity_profiles")
    op.drop_index("ix_company_opportunity_profiles_id", table_name="company_opportunity_profiles")
    op.drop_table("company_opportunity_profiles")
