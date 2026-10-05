"""Structured identity lookups. The SEC listing file is the public-company source."""

from dataclasses import dataclass
import time

import httpx

from app.core.config import settings
from app.services.discovery_verification.matching import names_match
from app.services.sec.cik import normalize_cik

SEC_LISTING_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_CACHE_SECONDS = 24 * 3600
_US_STATES = frozenset(
    {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI", "IA", "ID",
        "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MS", "MT", "NC",
        "ND", "NE", "NH", "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
        "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY",
    }
)


@dataclass(frozen=True)
class Listing:
    cik: str
    name: str
    ticker: str
    exchange: str | None


@dataclass(frozen=True)
class OfficialIdentity:
    canonical_name: str
    entity_type: str
    ticker: str | None = None
    isin: str | None = None
    country: str | None = None
    exchange: str | None = None
    website: str | None = None
    sec_cik: str | None = None
    confidence: int = 80


class SecListingDirectory:
    """One cached download of the SEC ticker file, then at most one submissions read."""

    def __init__(self, listings: list[Listing] | None = None) -> None:
        self._listings = listings

    async def listings_for_name(self, name: str) -> list[Listing]:
        rows = self._listings if self._listings is not None else await _cached_listings()
        return [row for row in rows if row.ticker and names_match(name, row.name)]

    async def enrich(self, listing: Listing) -> dict | None:
        if self._listings is not None:
            return None
        url = SEC_SUBMISSIONS_URL.format(cik=listing.cik.zfill(10))
        payload = await _get_json(url)
        if not isinstance(payload, dict):
            return None
        tickers = [str(item).upper() for item in payload.get("tickers") or [] if item]
        exchanges = [str(item) for item in payload.get("exchanges") or [] if item]
        state = str(payload.get("stateOfIncorporation") or "").strip().upper()
        website = str(payload.get("website") or "").strip() or None
        return {
            "name": payload.get("name") or listing.name,
            "tickers": tickers,
            "exchanges": exchanges,
            "website": website,
            "country": "US" if state in _US_STATES else None,
            "state_of_incorporation": state or None,
        }

    async def official_identities(self, name: str) -> list[OfficialIdentity]:
        return []


def build_directory() -> SecListingDirectory:
    return SecListingDirectory()


_cached: tuple[float, list[Listing]] | None = None


async def _cached_listings() -> list[Listing]:
    global _cached
    now = time.monotonic()
    if _cached is not None and now - _cached[0] < _CACHE_SECONDS:
        return _cached[1]
    payload = await _get_json(SEC_LISTING_URL)
    listings = _parse_listings(payload)
    _cached = (now, listings)
    return listings


def _parse_listings(payload) -> list[Listing]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("data")
    if not isinstance(rows, list):
        return []
    listings: list[Listing] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, list) or len(row) < 4:
            continue
        name = str(row[1] or "").strip()
        ticker = str(row[2] or "").strip().upper()
        exchange = str(row[3] or "").strip() or None
        if not name or not ticker:
            continue
        try:
            cik = normalize_cik(row[0])
        except ValueError:
            continue
        key = (cik, ticker)
        if key in seen:
            continue
        seen.add(key)
        listings.append(Listing(cik=cik, name=name, ticker=ticker, exchange=exchange))
    return listings


async def _get_json(url: str):
    headers = {"User-Agent": settings.sec_user_agent, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        return response.json()
    except ValueError:
        return None
