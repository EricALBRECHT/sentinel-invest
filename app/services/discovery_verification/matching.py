"""Match a candidate to companies already stored in Sentinel."""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.intelligence import CompanyAlias
from app.models.supply_chain import DiscoveredCompany
from app.services.intelligence.company_matching import AMBIGUOUS_TOKENS

_SUFFIXES = frozenset(
    {
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "ltd",
        "limited",
        "plc",
        "nv",
        "sa",
        "ag",
        "holdings",
        "group",
        "the",
    }
)


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    text = value.casefold().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [token for token in text.split() if token and token not in _SUFFIXES]
    return " ".join(tokens)


def names_match(candidate_name: str | None, other_name: str | None) -> bool:
    left = normalize_name(candidate_name)
    right = normalize_name(other_name)
    if not left or not right or left in AMBIGUOUS_TOKENS:
        return False
    if left == right:
        return True
    if len(left) >= 5 and (right.startswith(left + " ") or left.startswith(right + " ")):
        return True
    return False


async def find_existing_company(session: AsyncSession, candidate: DiscoveredCompany) -> list[Company]:
    companies = list((await session.scalars(select(Company).order_by(Company.id))).all())
    aliases = list((await session.scalars(select(CompanyAlias).where(CompanyAlias.is_active.is_(True)))).all())
    alias_names: dict[int, list[str]] = {}
    for alias in aliases:
        alias_names.setdefault(alias.company_id, []).append(alias.alias)
    found: list[Company] = []
    for company in companies:
        if _matches(candidate, company, alias_names.get(company.id, [])):
            found.append(company)
    return found


def _matches(candidate: DiscoveredCompany, company: Company, aliases: list[str]) -> bool:
    if candidate.promoted_company_id == company.id:
        return True
    if _same_token(candidate.ticker, company.ticker):
        return True
    if _same_token(candidate.isin, company.isin):
        return True
    if _same_cik(candidate.sec_cik, company.sec_cik):
        return True
    labels = [candidate.name, candidate.canonical_name]
    targets = [company.name, company.ticker, *aliases]
    return any(names_match(label, target) for label in labels for target in targets if label and target)


def _same_token(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return left.strip().casefold() == right.strip().casefold()


def _same_cik(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return left.strip().lstrip("0") == right.strip().lstrip("0")
