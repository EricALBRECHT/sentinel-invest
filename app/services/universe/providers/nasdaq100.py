"""Nasdaq-100 constituents from the public StockAnalysis list page.

Source (primary):
  https://stockanalysis.com/list/nasdaq-100-stocks/

The HTML constituents table supplies Symbol and Company Name for the current
Nasdaq-100 membership (typically ~101 rows including recent additions). This
is a public paraphrase of index membership, not an official Nasdaq Index
Services feed. Requests use the configured User-Agent, timeout, retries and
minimum interval. No aggressive crawling: one GET per refresh.
"""

from app.services.universe.providers.base import NormalizedMember, UniverseProvider
from app.services.universe.providers.http import PublicHttpClient, find_table, html_tables

_PAGE_URL = "https://stockanalysis.com/list/nasdaq-100-stocks/"
_REQUIRED = {"ticker", "name"}


class Nasdaq100Provider(UniverseProvider):
    provider_name = "stockanalysis_nasdaq100"
    universe_name = "NASDAQ100"
    source = "NASDAQ100"
    documentation = (
        "Public HTML table on StockAnalysis Nasdaq-100 list "
        f"({_PAGE_URL}). Columns used: Symbol, Company Name."
    )

    def __init__(self, client: PublicHttpClient | None = None, *, page_url: str = _PAGE_URL) -> None:
        self._client = client
        self._page_url = page_url

    async def fetch_members(self) -> list[NormalizedMember]:
        owned = self._client is None
        client = self._client or PublicHttpClient()
        try:
            html = await client.get_text(self._page_url, headers={"Accept": "text/html"})
        finally:
            if owned:
                await client.aclose()
        rows = find_table(html_tables(html), _REQUIRED)
        members: list[NormalizedMember] = []
        seen: set[str] = set()
        for row in rows:
            member = self.normalize_member(
                {
                    "ticker": row.get("ticker"),
                    "name": row.get("name"),
                    "country": "US",
                    "exchange": "NASDAQ",
                    "sector": row.get("sector"),
                    "industry": row.get("industry"),
                    "market_symbol": row.get("ticker"),
                    "source_metadata": {
                        "provider": self.provider_name,
                        "source_url": self._page_url,
                        "rank": row.get("rank"),
                    },
                }
            )
            if member.ticker in seen:
                continue
            seen.add(member.ticker)
            members.append(member)
        if len(members) < 80:
            raise RuntimeError(f"Nasdaq-100 import returned only {len(members)} members")
        return members
