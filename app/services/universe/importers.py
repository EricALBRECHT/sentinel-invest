"""Load a small CSV or JSON universe file. Nothing is fetched from the internet."""

import csv
from io import StringIO
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.services.universe.manager import add_company_to_universe
from app.services.universe.rules import UniverseRuleError, normalize_source, normalize_universe_name

_COLUMNS = ("ticker", "name", "country", "exchange", "universe_name", "source")


def rows_from_csv(text: str) -> list[dict]:
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        raise UniverseRuleError("CSV header is missing")
    missing = [column for column in _COLUMNS if column not in reader.fieldnames]
    if missing:
        raise UniverseRuleError("CSV is missing columns")
    rows = []
    for raw in reader:
        if not any((raw.get(column) or "").strip() for column in _COLUMNS):
            continue
        rows.append({column: (raw.get(column) or "").strip() for column in _COLUMNS})
    return rows


def rows_from_json(payload: object) -> list[dict]:
    if isinstance(payload, dict) and isinstance(payload.get("csv"), str):
        return rows_from_csv(payload["csv"])
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        payload = payload["rows"]
    if not isinstance(payload, list):
        raise UniverseRuleError("JSON import must be a list of rows")
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            raise UniverseRuleError("Each import row must be an object")
        rows.append({column: str(item.get(column) or "").strip() for column in _COLUMNS})
    return rows


def parse_import(body: bytes, content_type: str) -> list[dict]:
    if "json" in content_type.lower():
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise UniverseRuleError("Import body is not valid JSON") from exc
        return rows_from_json(payload)
    try:
        text = body.decode("utf-8")
    except UnicodeError as exc:
        raise UniverseRuleError("Import body is not valid text") from exc
    return rows_from_csv(text)


async def import_rows(session: AsyncSession, rows: list[dict]) -> dict:
    prepared = [_prepare(index, row) for index, row in enumerate(rows, start=1)]
    created = 0
    existing = 0
    added = 0
    already = 0
    for row in prepared:
        company, was_created = await _company_for_row(session, row)
        if was_created:
            created += 1
        else:
            existing += 1
        _membership, is_new = await add_company_to_universe(
            session,
            company.id,
            row["universe_name"],
            row["source"],
            commit=False,
        )
        if is_new:
            added += 1
        else:
            already += 1
    await session.commit()
    return {
        "rows": len(prepared),
        "created_companies": created,
        "existing_companies": existing,
        "memberships_added": added,
        "memberships_existing": already,
    }


def _prepare(index: int, row: dict) -> dict:
    ticker = str(row.get("ticker") or "").strip().upper()
    name = str(row.get("name") or "").strip()
    if not ticker:
        raise UniverseRuleError(f"Row {index} has no ticker")
    try:
        universe_name = normalize_universe_name(str(row.get("universe_name") or ""))
        source = normalize_source(str(row.get("source") or ""))
    except UniverseRuleError as exc:
        raise UniverseRuleError(f"Row {index}: {exc}") from exc
    return {
        "ticker": ticker,
        "name": name,
        "country": _optional(row.get("country")),
        "exchange": _optional(row.get("exchange")),
        "universe_name": universe_name,
        "source": source,
    }


async def _company_for_row(session: AsyncSession, row: dict) -> tuple[Company, bool]:
    statement = select(Company).where(Company.ticker == row["ticker"])
    company = (await session.execute(statement)).scalar_one_or_none()
    if company is not None:
        return company, False
    if not row["name"]:
        raise UniverseRuleError(f"Ticker {row['ticker']} needs a name")
    company = Company(
        name=row["name"],
        ticker=row["ticker"],
        country=row["country"],
        exchange=row["exchange"],
    )
    session.add(company)
    await session.flush()
    return company, True


def _optional(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
