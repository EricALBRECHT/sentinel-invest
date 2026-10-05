"""Source registry and the small V1 catalog of legitimate feeds."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.intelligence import CompanyAlias, ExternalSource

SOURCE_TYPES = frozenset(
    {"NEWS", "COMPANY_IR", "REGULATOR", "SEC", "RSS", "MARKET", "INDUSTRY", "GOVERNMENT", "OTHER"}
)
TRUST_LEVELS = frozenset({"HIGH", "MEDIUM", "LOW"})
NEWS_SYNC_SOURCE = "news"

# Official feeds checked against robots.txt. sec.gov disallows /cgi-bin, so filings
# come from data.sec.gov. STM's newsroom feed did not answer, so STM uses SEC filings.
V1_CATALOG = (
    {
        "name": "NVIDIA newsroom",
        "source_type": "COMPANY_IR",
        "provider": "rss",
        "base_url": "https://nvidianews.nvidia.com/releases.xml",
        "trust_level": "HIGH",
        "poll_interval_minutes": 120,
        "ticker": "NVDA",
        "document_type": "PRESS_RELEASE",
        "aliases": (("NVIDIA", "COMMON_NAME"), ("NVIDIA Corporation", "LEGAL_NAME"), ("NVDA", "TICKER")),
    },
    {
        "name": "NVIDIA investor releases",
        "source_type": "COMPANY_IR",
        "provider": "rss",
        "base_url": "https://investor.nvidia.com/rss/PressRelease.aspx",
        "trust_level": "HIGH",
        "poll_interval_minutes": 120,
        "ticker": "NVDA",
        "document_type": "PRESS_RELEASE",
        "aliases": (),
    },
    {
        "name": "SEC submissions NVDA",
        "source_type": "SEC",
        "provider": "sec_submissions",
        "base_url": "https://data.sec.gov/submissions/CIK0001045810.json",
        "trust_level": "HIGH",
        "poll_interval_minutes": 720,
        "ticker": "NVDA",
        "document_type": "SEC_FILING",
        "cik": "0001045810",
        "aliases": (),
    },
    {
        "name": "SEC submissions STM",
        "source_type": "SEC",
        "provider": "sec_submissions",
        "base_url": "https://data.sec.gov/submissions/CIK0000932787.json",
        "trust_level": "HIGH",
        "poll_interval_minutes": 720,
        "ticker": "STM",
        "document_type": "SEC_FILING",
        "cik": "0000932787",
        "aliases": (
            ("STMicroelectronics", "COMMON_NAME"),
            ("STMicroelectronics N.V.", "LEGAL_NAME"),
            ("STM", "TICKER"),
        ),
    },
)


async def create_source(
    session: AsyncSession,
    *,
    name: str,
    source_type: str,
    base_url: str,
    provider: str,
    trust_level: str = "MEDIUM",
    poll_interval_minutes: int = 720,
    metadata: dict | None = None,
    is_active: bool = True,
) -> ExternalSource:
    if source_type not in SOURCE_TYPES:
        raise ValueError("Unknown source type")
    if trust_level not in TRUST_LEVELS:
        raise ValueError("Unknown trust level")
    row = ExternalSource(
        name=name,
        source_type=source_type,
        base_url=base_url,
        provider=provider,
        trust_level=trust_level,
        poll_interval_minutes=poll_interval_minutes,
        is_active=is_active,
        metadata_json=metadata or {},
        updated_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def register_v1_catalog(session: AsyncSession) -> dict:
    created = 0
    linked = 0
    for item in V1_CATALOG:
        company = await session.scalar(select(Company).where(Company.ticker == item["ticker"]))
        if company is None:
            continue
        linked += 1
        await ensure_aliases(session, company, item["aliases"])
        existing = await session.scalar(select(ExternalSource).where(ExternalSource.name == item["name"]))
        metadata = {
            "company_id": company.id,
            "ticker": company.ticker,
            "document_type": item["document_type"],
            "retention": "text_only_v1",
        }
        if item.get("cik"):
            metadata["cik"] = item["cik"]
        if existing is None:
            session.add(
                ExternalSource(
                    name=item["name"],
                    source_type=item["source_type"],
                    base_url=item["base_url"],
                    provider=item["provider"],
                    trust_level=item["trust_level"],
                    poll_interval_minutes=item["poll_interval_minutes"],
                    is_active=True,
                    metadata_json=metadata,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            created += 1
        else:
            existing.metadata_json = metadata
            existing.is_active = True
            existing.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"created": created, "linked": linked}


async def ensure_aliases(session: AsyncSession, company: Company, extras: tuple[tuple[str, str], ...] = ()) -> None:
    wanted = []
    if company.ticker:
        wanted.append((company.ticker, "TICKER"))
    if company.name:
        wanted.append((company.name, "LEGAL_NAME"))
    wanted.extend(extras)
    existing = {
        row.alias
        for row in (
            await session.scalars(select(CompanyAlias).where(CompanyAlias.company_id == company.id))
        ).all()
    }
    for alias, alias_type in wanted:
        cleaned = " ".join(alias.split())
        if not cleaned or cleaned in existing:
            continue
        session.add(
            CompanyAlias(
                company_id=company.id,
                alias=cleaned,
                alias_type=alias_type,
                source="CATALOG" if (cleaned, alias_type) in extras else "IDENTITY",
                is_active=True,
            )
        )
        existing.add(cleaned)
    await session.commit()
