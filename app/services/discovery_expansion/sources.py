"""Register the structured sources a promoted company can already use."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.intelligence import CompanyExternalSource, ExternalSource
from app.services.sec.cik import normalize_cik


async def register_sec_source(session: AsyncSession, company: Company) -> ExternalSource | None:
    """Create the SEC submissions source once and link it to the company."""
    if not (company.sec_cik or "").strip():
        return None
    cik = normalize_cik(company.sec_cik)
    padded = cik.zfill(10)
    source = await _source_for_cik(session, padded)
    if source is None:
        source = ExternalSource(
            name=await _source_name(session, company.ticker, cik),
            source_type="SEC",
            base_url=f"https://data.sec.gov/submissions/CIK{padded}.json",
            provider="sec_submissions",
            trust_level="HIGH",
            poll_interval_minutes=720,
            is_active=True,
            metadata_json=_metadata(company, padded),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(source)
        await session.flush()
    elif _company_id(source) is None:
        source.metadata_json = _metadata(company, padded)
        source.is_active = True
        source.updated_at = datetime.now(timezone.utc)
    await _link(session, company.id, source.id)
    return source


async def linked_source_ids(session: AsyncSession, company_id: int) -> set[int]:
    rows = (
        await session.scalars(
            select(CompanyExternalSource.source_id).where(CompanyExternalSource.company_id == company_id)
        )
    ).all()
    return {int(source_id) for source_id in rows}


def sec_financial_eligible(company: Company) -> bool:
    return bool(company.is_active and (company.sec_cik or "").strip() and (company.ticker or "").strip())


async def _source_for_cik(session: AsyncSession, padded: str) -> ExternalSource | None:
    sources = (
        await session.scalars(select(ExternalSource).where(ExternalSource.provider == "sec_submissions"))
    ).all()
    for source in sources:
        raw = str((source.metadata_json or {}).get("cik") or "")
        digits = "".join(character for character in raw if character.isdigit())
        if digits.lstrip("0") == padded.lstrip("0") or source.base_url.endswith(f"CIK{padded}.json"):
            return source
    return None


async def _source_name(session: AsyncSession, ticker: str, cik: str) -> str:
    preferred = f"SEC submissions {ticker}"
    taken = await session.scalar(select(ExternalSource.id).where(ExternalSource.name == preferred))
    if taken is None:
        return preferred
    return f"SEC submissions {ticker} {cik}"


async def _link(session: AsyncSession, company_id: int, source_id: int) -> None:
    existing = await session.scalar(
        select(CompanyExternalSource).where(
            CompanyExternalSource.company_id == company_id,
            CompanyExternalSource.source_id == source_id,
        )
    )
    if existing is not None:
        return
    primary = await session.scalar(
        select(CompanyExternalSource.id).where(
            CompanyExternalSource.company_id == company_id,
            CompanyExternalSource.is_primary.is_(True),
        )
    )
    session.add(
        CompanyExternalSource(
            company_id=company_id,
            source_id=source_id,
            is_primary=primary is None,
        )
    )
    await session.flush()


def _metadata(company: Company, padded: str) -> dict:
    return {
        "company_id": company.id,
        "ticker": company.ticker,
        "document_type": "SEC_FILING",
        "cik": padded,
        "retention": "text_only_v1",
    }


def _company_id(source: ExternalSource) -> int | None:
    value = (source.metadata_json or {}).get("company_id")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
