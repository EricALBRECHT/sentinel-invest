"""technical snapshots

Revision ID: f3a9c6e24d81
Revises: d1e7a4c92b18
Create Date: 2026-10-05 19:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f3a9c6e24d81"
down_revision: Union[str, Sequence[str], None] = "d1e7a4c92b18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TRENDS = "('STRONG_UP', 'UP', 'NEUTRAL', 'DOWN', 'STRONG_DOWN')"


def upgrade() -> None:
    op.create_table(
        "technical_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("price", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("sma_20", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("sma_50", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("sma_100", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("sma_200", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("ema_12", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("ema_26", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("rsi_14", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("macd", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("macd_signal", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("macd_histogram", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("atr_14", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("volatility_20d", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("average_volume_20d", sa.BigInteger(), nullable=True),
        sa.Column("volume_ratio", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("distance_sma_20_pct", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("distance_sma_50_pct", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("distance_sma_200_pct", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("week_52_position_pct", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("trend_short", sa.String(length=16), nullable=True),
        sa.Column("trend_medium", sa.String(length=16), nullable=True),
        sa.Column("trend_long", sa.String(length=16), nullable=True),
        sa.Column("support_1", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("support_2", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("resistance_1", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("resistance_2", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("technical_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("technical_confidence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("components_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            f"trend_short IS NULL OR trend_short IN {_TRENDS}",
            name="ck_technical_snapshots_trend_short",
        ),
        sa.CheckConstraint(
            f"trend_medium IS NULL OR trend_medium IN {_TRENDS}",
            name="ck_technical_snapshots_trend_medium",
        ),
        sa.CheckConstraint(
            f"trend_long IS NULL OR trend_long IN {_TRENDS}",
            name="ck_technical_snapshots_trend_long",
        ),
        sa.CheckConstraint(
            "technical_score IS NULL OR (technical_score >= 0 AND technical_score <= 100)",
            name="ck_technical_snapshots_score",
        ),
        sa.CheckConstraint(
            "technical_confidence >= 0 AND technical_confidence <= 100",
            name="ck_technical_snapshots_confidence",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id"),
    )
    op.create_index("ix_technical_snapshots_id", "technical_snapshots", ["id"])
    op.create_index("ix_technical_snapshots_company_id", "technical_snapshots", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_technical_snapshots_company_id", table_name="technical_snapshots")
    op.drop_index("ix_technical_snapshots_id", table_name="technical_snapshots")
    op.drop_table("technical_snapshots")
