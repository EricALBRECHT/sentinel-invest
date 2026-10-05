import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
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


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield factory
    app.dependency_overrides.clear()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def client(session_factory):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
