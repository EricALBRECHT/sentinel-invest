from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.base import Base
from app.db.urls import database_url
from app.models.company import Company  # noqa: F401
from app.models.company_score import CompanyScore  # noqa: F401
from app.models.company_sync_status import CompanySyncStatus  # noqa: F401
from app.models.financial_metric import FinancialMetric  # noqa: F401
from app.models.opportunity_profile import OpportunityProfile  # noqa: F401
from app.models.opportunity_score import OpportunityScore  # noqa: F401
from app.models.market_price import MarketPrice  # noqa: F401
from app.models.company_market_snapshot import CompanyMarketSnapshot  # noqa: F401
from app.models.market_provider_symbol import MarketProviderSymbol  # noqa: F401
from app.models.technical_snapshot import TechnicalSnapshot  # noqa: F401
from app.models.investment_view import InvestmentView  # noqa: F401
from app.models.intelligence import (  # noqa: F401
    CompanyAlias,
    DocumentCompany,
    EventCompany,
    ExternalDocument,
    ExternalSource,
    IntelligenceEvent,
)
from app.models.supply_chain import (  # noqa: F401
    CompanyRelationship,
    DiscoveredCompany,
    RelationshipEvidence,
)
from app.models.universe_membership import UniverseMembership  # noqa: F401
from app.models.user import User  # noqa: F401

config = context.config

config.set_main_option("sqlalchemy.url", database_url("postgresql+psycopg"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=database_url("postgresql+psycopg"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
