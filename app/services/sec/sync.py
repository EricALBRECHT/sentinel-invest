"""Persist parsed SEC periods onto financial_metrics.

One row is kept per company, fiscal period, and source. A later filing that
restates a period updates that row and replaces the accession number with the
filing that supplied the kept values.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import logging
import re
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.financial_metric import FinancialMetric
from app.services.sec.company_facts import (
    ParsedPeriod,
    eps_value_plausible,
    parse_company_facts,
)
from app.services.sec.errors import MissingSecCik
from app.services.sec.mappings import SEC_SOURCE

logger = logging.getLogger("sentinel.sec")

_VALUE_FIELDS = (
    "period_start",
    "period_end",
    "filed_at",
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "eps_basic",
    "eps_diluted",
    "operating_cash_flow",
    "capital_expenditure",
    "free_cash_flow",
    "cash_and_equivalents",
    "total_assets",
    "total_liabilities",
    "total_debt",
    "shareholders_equity",
    "shares_outstanding",
    "source_url",
    "filing_type",
    "accession_number",
    "revenue_source_concept",
    "gross_profit_source_concept",
    "operating_income_source_concept",
    "net_income_source_concept",
    "eps_basic_source_concept",
    "eps_diluted_source_concept",
    "operating_cash_flow_source_concept",
    "capital_expenditure_source_concept",
    "cash_source_concept",
    "assets_source_concept",
    "liabilities_source_concept",
    "debt_source_concept",
    "equity_source_concept",
    "shares_source_concept",
)


class CompanyFactsSource(Protocol):
    async def get_company_facts(self, cik: str) -> dict: ...


@dataclass(frozen=True)
class SecSyncResult:
    company_id: int
    ticker: str
    periods_found: int
    created: int
    updated: int
    skipped: int
    deleted: int
    warnings: tuple[str, ...]


async def sync_sec_financials(
    db: AsyncSession,
    company: Company,
    client: CompanyFactsSource,
) -> SecSyncResult:
    if not company.sec_cik:
        raise MissingSecCik("Company has no SEC CIK")

    payload = await client.get_company_facts(company.sec_cik)
    parsed = parse_company_facts(payload, cik=company.sec_cik)
    warnings = list(parsed.warnings)
    entity_warning = _entity_name_warning(company.name, payload.get("entityName"))
    if entity_warning:
        warnings.insert(0, entity_warning)

    created = 0
    updated = 0
    skipped = 0
    sanitized_periods: list[ParsedPeriod] = []
    for period in parsed.periods:
        clean, field_warnings = _sanitize_period_for_persistence(company, period)
        warnings.extend(field_warnings)
        sanitized_periods.append(clean)
        outcome = await _upsert_period(db, company.id, clean)
        if outcome == "created":
            created += 1
        elif outcome == "updated":
            updated += 1
        else:
            skipped += 1
    deleted = await _delete_stale_periods(db, company.id, tuple(sanitized_periods))

    await db.commit()
    result = SecSyncResult(
        company_id=company.id,
        ticker=company.ticker,
        periods_found=len(parsed.periods),
        created=created,
        updated=updated,
        skipped=skipped,
        deleted=deleted,
        warnings=tuple(dict.fromkeys(warnings)),
    )
    logger.info(
        "sec_sync_finished company_id=%s ticker=%s cik=%s periods_found=%s created=%s updated=%s skipped=%s deleted=%s warning_count=%s",
        result.company_id,
        result.ticker,
        company.sec_cik,
        result.periods_found,
        result.created,
        result.updated,
        result.skipped,
        result.deleted,
        len(result.warnings),
    )
    for warning in result.warnings:
        logger.warning("sec_sync_warning company_id=%s %s", company.id, warning)
    return result


async def _upsert_period(db: AsyncSession, company_id: int, period: ParsedPeriod) -> str:
    statement = select(FinancialMetric).where(
        FinancialMetric.company_id == company_id,
        FinancialMetric.fiscal_year == period.fiscal_year,
        FinancialMetric.fiscal_period == period.fiscal_period,
        FinancialMetric.source == SEC_SOURCE,
    )
    existing = (await db.execute(statement)).scalar_one_or_none()
    if existing is None:
        db.add(_new_metric(company_id, period))
        return "created"
    if _same(existing, period):
        return "skipped"
    for field in _VALUE_FIELDS:
        setattr(existing, field, getattr(period, field))
    existing.updated_at = datetime.now(timezone.utc)
    return "updated"


async def _delete_stale_periods(
    db: AsyncSession,
    company_id: int,
    periods: tuple[ParsedPeriod, ...],
) -> int:
    kept = {(period.fiscal_year, period.fiscal_period) for period in periods}
    statement = select(FinancialMetric).where(
        FinancialMetric.company_id == company_id,
        FinancialMetric.source == SEC_SOURCE,
    )
    existing = (await db.execute(statement)).scalars().all()
    deleted = 0
    for metric in existing:
        if (metric.fiscal_year, metric.fiscal_period) in kept:
            continue
        await db.delete(metric)
        deleted += 1
    if deleted:
        logger.info(
            "sec_sync_deleted_stale company_id=%s deleted=%s",
            company_id,
            deleted,
        )
    return deleted


def _sanitize_period_for_persistence(
    company: Company,
    period: ParsedPeriod,
) -> tuple[ParsedPeriod, list[str]]:
    """Drop EPS fields that cannot be stored or are share-count mis-tags.

    Keeps the rest of the period so one bad EPS value does not fail the sync.
    """
    warnings: list[str] = []
    updates: dict[str, Decimal | str | None] = {}
    for field, concept_field in (
        ("eps_basic", "eps_basic_source_concept"),
        ("eps_diluted", "eps_diluted_source_concept"),
    ):
        value = getattr(period, field)
        if value is None:
            continue
        concept = getattr(period, concept_field)
        if eps_value_plausible(value, shares=period.shares_outstanding):
            continue
        logger.warning(
            "sec_eps_rejected company_id=%s ticker=%s cik=%s period=%s%s field=%s "
            "concept=%s value=%s shares_outstanding=%s",
            company.id,
            company.ticker,
            company.sec_cik,
            period.fiscal_year,
            period.fiscal_period,
            field,
            concept,
            value,
            period.shares_outstanding,
        )
        warnings.append(
            f"{period.fiscal_year} {period.fiscal_period} {field}: "
            f"rejected {concept}={value} before persistence"
        )
        updates[field] = None
        updates[concept_field] = None
    if not updates:
        return period, warnings
    return replace(period, **updates), warnings


def _new_metric(company_id: int, period: ParsedPeriod) -> FinancialMetric:
    values = {field: getattr(period, field) for field in _VALUE_FIELDS}
    return FinancialMetric(
        company_id=company_id,
        fiscal_year=period.fiscal_year,
        fiscal_period=period.fiscal_period,
        source=period.source,
        **values,
    )


def _same(existing: FinancialMetric, period: ParsedPeriod) -> bool:
    return all(getattr(existing, field) == getattr(period, field) for field in _VALUE_FIELDS)


def _entity_name_warning(company_name: str, entity_name: object) -> str | None:
    if not isinstance(entity_name, str) or not entity_name.strip():
        return None
    company_tokens = _tokens(company_name)
    entity_tokens = _tokens(entity_name)
    if company_tokens & entity_tokens:
        return None
    return (
        f"SEC entityName is '{entity_name}', which shares no name token with '{company_name}'. "
        "The facts were still imported for this company."
    )


def _tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^A-Za-z0-9]+", value.upper()) if len(token) > 2}
