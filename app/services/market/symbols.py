"""Resolve the symbol a market provider should be called with.

Company.market_symbol is the symbol stored for the company. A row in
market_provider_symbols replaces it for one provider only.
Yahoo uses a hyphen for US share-class suffixes (BRK-B); Sentinel keeps
the dotted ticker (BRK.B) in company rows.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.market_provider_symbol import MarketProviderSymbol

# US share class: TICKER.A / TICKER.B → Yahoo TICKER-A / TICKER-B.
# Exchange suffixes like STM.PA stay dotted (two+ letters after the dot).
_US_SHARE_CLASS = re.compile(r"^([A-Z][A-Z0-9]*)\.([A-Z])$")


async def resolve_provider_symbol(session: AsyncSession, company: Company, provider: str) -> str:
    statement = select(MarketProviderSymbol.provider_symbol).where(
        MarketProviderSymbol.company_id == company.id,
        MarketProviderSymbol.provider == provider,
    )
    alias = (await session.execute(statement)).scalar_one_or_none()
    if isinstance(alias, str) and alias.strip():
        return alias.strip()
    base = (company.market_symbol or "").strip()
    if provider == "yahoo":
        return normalize_yahoo_symbol(base)
    return base


def normalize_yahoo_symbol(symbol: str) -> str:
    """Map Sentinel/market_symbol to Yahoo's chart symbol without mutating storage."""
    text = (symbol or "").strip().upper()
    if not text:
        return text
    match = _US_SHARE_CLASS.match(text)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return text
