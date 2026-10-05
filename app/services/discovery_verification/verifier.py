"""Decide whether a candidate identity is unique enough to trust."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.company import Company
from app.models.supply_chain import DiscoveredCompany
from app.services.discovery_verification.matching import find_existing_company, names_match
from app.services.discovery_verification.resolver import Listing, OfficialIdentity, SecListingDirectory, build_directory

_VERIFIED_AT = 75


async def verify_candidate(
    session: AsyncSession,
    candidate_id: int,
    directory: SecListingDirectory | None = None,
) -> DiscoveredCompany | None:
    row = await session.get(DiscoveredCompany, candidate_id)
    if row is None:
        return None
    if row.verification_status == "REJECTED" or row.status == "REJECTED":
        return row
    source = directory or build_directory()
    companies = await find_existing_company(session, row)
    listings = [item for item in await source.listings_for_name(row.name) if names_match(row.name, item.name)]
    officials = [
        item
        for item in await source.official_identities(row.name)
        if names_match(row.name, item.canonical_name)
    ]
    if len(companies) > 1 or len(listings) > 1 or len(officials) > 1:
        _partial(row, companies, listings, officials, "Several structured identities match this name")
    elif len(companies) == 1 and len(listings) == 1 and not _ticker_agrees(companies[0].ticker, listings[0].ticker):
        _partial(row, companies, listings, officials, "The stored company and the SEC listing use different tickers")
    elif len(companies) == 1:
        await _verify_company(row, companies[0], listings[0] if listings else None, source)
    elif len(listings) == 1:
        await _verify_listing(row, listings[0], source)
    elif len(officials) == 1:
        _verify_official(row, officials[0])
    else:
        _partial(row, companies, listings, officials, "No unique company, SEC listing, or official identity")
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return row


async def select_due_candidate_ids(session: AsyncSession, *, now: datetime | None = None) -> list[int]:
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(hours=max(1, settings.discovery_verify_scan_hours))
    rows = list(
        (
            await session.scalars(
                select(DiscoveredCompany)
                .where(
                    DiscoveredCompany.status == "CANDIDATE",
                    DiscoveredCompany.confidence >= settings.discovery_verify_min_confidence,
                    DiscoveredCompany.evidence_count >= 1,
                    DiscoveredCompany.verification_status.in_(("UNVERIFIED", "PARTIAL")),
                )
                .order_by(DiscoveredCompany.confidence.desc(), DiscoveredCompany.id.asc())
            )
        ).all()
    )
    due: list[int] = []
    for row in rows:
        updated = row.updated_at
        if row.verification_status == "PARTIAL" and updated is not None and _aware(updated) >= cutoff:
            continue
        due.append(row.id)
        if len(due) >= max(0, settings.discovery_verify_max_per_run):
            break
    return due


async def verification_counts(session: AsyncSession) -> dict:
    rows = list((await session.scalars(select(DiscoveredCompany))).all())
    last = None
    for row in rows:
        if row.verified_at is None:
            continue
        moment = _aware(row.verified_at)
        if last is None or moment > last:
            last = moment
    return {
        "unverified": sum(1 for row in rows if row.verification_status == "UNVERIFIED"),
        "partial": sum(1 for row in rows if row.verification_status == "PARTIAL"),
        "verified": sum(1 for row in rows if row.verification_status == "VERIFIED"),
        "promoted": sum(1 for row in rows if row.promoted_company_id is not None),
        "rejected": sum(1 for row in rows if row.verification_status == "REJECTED"),
        "last_verification": last,
    }


async def _verify_company(
    row: DiscoveredCompany,
    company: Company,
    listing: Listing | None,
    source: SecListingDirectory,
) -> None:
    enrichment = await source.enrich(listing) if listing is not None else None
    if listing is not None and enrichment and not _confirms(listing, enrichment):
        _partial(row, [company], [listing], [], "SEC submissions do not confirm the listing ticker")
        return
    row.canonical_name = company.name
    row.ticker = company.ticker
    row.isin = company.isin
    row.country = company.country
    row.exchange = company.exchange
    row.sec_cik = company.sec_cik
    row.entity_type = "PUBLIC_COMPANY" if company.ticker else "UNKNOWN"
    row.verification_status = "VERIFIED"
    row.verification_confidence = 95 if company.ticker else 85
    row.verified_at = datetime.now(timezone.utc)
    if row.status == "CANDIDATE":
        row.status = "VERIFIED"
    row.verification_evidence_json = {
        "decision": "existing_company",
        "sources": [_company_source(company), *([_listing_source(listing, enrichment)] if listing else [])],
    }


async def _verify_listing(row: DiscoveredCompany, listing: Listing, source: SecListingDirectory) -> None:
    enrichment = await source.enrich(listing)
    if enrichment and not _confirms(listing, enrichment):
        _partial(row, [], [listing], [], "SEC submissions do not confirm the listing ticker")
        return
    confirmed = enrichment is not None and _confirms(listing, enrichment)
    row.canonical_name = str((enrichment or {}).get("name") or listing.name)
    row.ticker = listing.ticker
    row.exchange = ((enrichment or {}).get("exchanges") or [listing.exchange])[0]
    row.sec_cik = listing.cik
    row.country = (enrichment or {}).get("country")
    website = (enrichment or {}).get("website")
    if website:
        row.website = str(website)[:255]
    row.entity_type = "PUBLIC_COMPANY"
    row.verification_status = "VERIFIED"
    row.verification_confidence = 100 if confirmed else 90
    row.verified_at = datetime.now(timezone.utc)
    if row.status == "CANDIDATE":
        row.status = "VERIFIED"
    row.verification_evidence_json = {
        "decision": "sec_listing",
        "sources": [_listing_source(listing, enrichment)],
    }


def _verify_official(row: DiscoveredCompany, official: OfficialIdentity) -> None:
    if official.confidence < _VERIFIED_AT or official.entity_type not in {
        "PUBLIC_COMPANY",
        "PRIVATE_COMPANY",
        "SUBSIDIARY",
        "BRAND",
        "UNKNOWN",
    }:
        _partial(row, [], [], [official], "The official identity is not strong enough")
        return
    row.canonical_name = official.canonical_name
    row.ticker = official.ticker
    row.isin = official.isin
    row.country = official.country
    row.exchange = official.exchange
    row.website = official.website
    row.sec_cik = official.sec_cik
    row.entity_type = official.entity_type
    row.verification_status = "VERIFIED"
    row.verification_confidence = official.confidence
    row.verified_at = datetime.now(timezone.utc)
    if row.status == "CANDIDATE":
        row.status = "VERIFIED"
    row.verification_evidence_json = {
        "decision": "official_identity",
        "sources": [
            {
                "source": "OFFICIAL",
                "name": official.canonical_name,
                "entity_type": official.entity_type,
                "ticker": official.ticker,
                "website": official.website,
            }
        ],
    }


def _partial(
    row: DiscoveredCompany,
    companies: list[Company],
    listings: list[Listing],
    officials: list[OfficialIdentity],
    reason: str,
) -> None:
    row.verification_status = "PARTIAL"
    row.verification_confidence = 50
    row.entity_type = "UNKNOWN"
    if row.status == "VERIFIED":
        row.status = "CANDIDATE"
    row.verification_evidence_json = {
        "decision": "partial",
        "reason": reason,
        "possible_matches": [
            *[_company_source(company) for company in companies],
            *[_listing_source(listing, None) for listing in listings],
            *[
                {"source": "OFFICIAL", "name": item.canonical_name, "ticker": item.ticker, "entity_type": item.entity_type}
                for item in officials
            ],
        ],
    }


def _company_source(company: Company) -> dict:
    return {
        "source": "COMPANY",
        "company_id": company.id,
        "name": company.name,
        "ticker": company.ticker,
        "isin": company.isin,
        "sec_cik": company.sec_cik,
    }


def _listing_source(listing: Listing, enrichment: dict | None) -> dict:
    return {
        "source": "SEC",
        "name": listing.name,
        "ticker": listing.ticker,
        "exchange": listing.exchange,
        "sec_cik": listing.cik,
        "url": "https://www.sec.gov/files/company_tickers_exchange.json",
        "submissions_confirmed": bool(enrichment and _confirms(listing, enrichment)),
        "country": None if enrichment is None else enrichment.get("country"),
        "website": None if enrichment is None else enrichment.get("website"),
    }


def _confirms(listing: Listing, enrichment: dict) -> bool:
    tickers = [str(item).upper() for item in enrichment.get("tickers") or []]
    return listing.ticker.upper() in tickers


def _ticker_agrees(company_ticker: str | None, listing_ticker: str | None) -> bool:
    if not company_ticker or not listing_ticker:
        return True
    return company_ticker.strip().upper() == listing_ticker.strip().upper()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
