"""S&P 500 constituents from the public datasets/s-and-p-500-companies CSV.

Source (primary):
  https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv

This is the maintained DataHub / datasets mirror of the Wikipedia S&P 500
constituents table (Symbol, Security, GICS Sector, GICS Sub-Industry, CIK).
It is a public paraphrase of index membership, not an official S&P Dow Jones
Indices feed. Requests use the configured User-Agent, timeout, retries and
minimum interval.
"""

from app.services.universe.providers.base import NormalizedMember, UniverseProvider
from app.services.universe.providers.http import PublicHttpClient

_CSV_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"


class Sp500Provider(UniverseProvider):
    provider_name = "datasets_sp500_csv"
    universe_name = "SP500"
    source = "SP500"
    documentation = (
        "Public CSV from datasets/s-and-p-500-companies "
        f"({_CSV_URL}). Columns: Symbol, Security, GICS Sector, "
        "GICS Sub-Industry, Headquarters Location, Date added, CIK, Founded."
    )

    def __init__(self, client: PublicHttpClient | None = None, *, csv_url: str = _CSV_URL) -> None:
        self._client = client
        self._csv_url = csv_url

    async def fetch_members(self) -> list[NormalizedMember]:
        owned = self._client is None
        client = self._client or PublicHttpClient()
        try:
            rows = await client.get_csv_rows(self._csv_url)
        finally:
            if owned:
                await client.aclose()
        members: list[NormalizedMember] = []
        seen: set[str] = set()
        for row in rows:
            ticker = row.get("Symbol") or row.get("symbol") or row.get("ticker")
            name = row.get("Security") or row.get("security") or row.get("name")
            if not ticker or not name:
                continue
            member = self.normalize_member(
                {
                    "ticker": ticker,
                    "name": name,
                    "country": "US",
                    "exchange": None,
                    "sector": row.get("GICS Sector") or row.get("sector"),
                    "industry": row.get("GICS Sub-Industry") or row.get("industry"),
                    "sec_cik": row.get("CIK") or row.get("cik"),
                    "market_symbol": ticker,
                    "source_metadata": {
                        "provider": self.provider_name,
                        "source_url": self._csv_url,
                        "headquarters": row.get("Headquarters Location"),
                        "date_added": row.get("Date added"),
                        "founded": row.get("Founded"),
                    },
                }
            )
            if member.ticker in seen:
                continue
            seen.add(member.ticker)
            members.append(member)
        if len(members) < 400:
            raise RuntimeError(f"S&P 500 import returned only {len(members)} members")
        return members
