"""enrich companies

Revision ID: b7e2a4c91d08
Revises: c3f8a91e4b27
Create Date: 2026-10-04 22:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7e2a4c91d08'
down_revision: Union[str, Sequence[str], None] = 'c3f8a91e4b27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('companies', sa.Column('ticker', sa.String(length=32), nullable=True))
    op.add_column('companies', sa.Column('isin', sa.String(length=12), nullable=True))
    op.add_column('companies', sa.Column('country', sa.String(length=64), nullable=True))
    op.add_column('companies', sa.Column('exchange', sa.String(length=64), nullable=True))
    op.add_column('companies', sa.Column('sector', sa.String(length=128), nullable=True))
    op.add_column('companies', sa.Column('industry', sa.String(length=128), nullable=True))
    op.add_column('companies', sa.Column('market_cap', sa.Numeric(precision=20, scale=2), nullable=True))
    op.add_column(
        'companies',
        sa.Column('pea_eligible', sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        'companies',
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
    )
    op.execute("UPDATE companies SET ticker = 'LEGACY' || id WHERE ticker IS NULL")
    op.alter_column('companies', 'ticker', nullable=False)
    op.create_index(op.f('ix_companies_ticker'), 'companies', ['ticker'], unique=True)
    op.create_index(op.f('ix_companies_isin'), 'companies', ['isin'], unique=True)
    op.drop_index(op.f('ix_companies_domain'), table_name='companies')
    op.drop_column('companies', 'domain')


def downgrade() -> None:
    op.add_column('companies', sa.Column('domain', sa.String(length=255), nullable=True))
    op.create_index(op.f('ix_companies_domain'), 'companies', ['domain'], unique=False)
    op.drop_index(op.f('ix_companies_isin'), table_name='companies')
    op.drop_index(op.f('ix_companies_ticker'), table_name='companies')
    op.drop_column('companies', 'updated_at')
    op.drop_column('companies', 'pea_eligible')
    op.drop_column('companies', 'market_cap')
    op.drop_column('companies', 'industry')
    op.drop_column('companies', 'sector')
    op.drop_column('companies', 'exchange')
    op.drop_column('companies', 'country')
    op.drop_column('companies', 'isin')
    op.drop_column('companies', 'ticker')
