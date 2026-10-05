"""Resolve the symbol a market provider should be called with.

Company.market_symbol is the symbol stored for the company. A row in
market_provider_symbols replaces it for one provider only.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.market_provider_symbol import MarketProviderSymbol


async def resolve_provider_symbol(session: AsyncSession, company: Company, provider: str) -> str:
    statement = select(MarketProviderSymbol.provider_symbol).where(
        MarketProviderSymbol.company_id == company.id,
        MarketProviderSymbol.provider == provider,
    )
    alias = (await session.execute(statement)).scalar_one_or_none()
    if isinstance(alias, str) and alias.strip():
        return alias.strip()
    return (company.market_symbol or "").strip()
