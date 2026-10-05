"""Deterministic company detection.

A ticker of four letters or more may match in any case. A shorter ticker must
appear in uppercase, so STM matches and a lowercase fragment does not.
A single everyday word such as Apple is never enough on its own. A multi-word
legal name, or the ticker beside it, is enough.
"""

from dataclasses import dataclass
import re

# Everyday words that are also company names. The name alone is not a match.
AMBIGUOUS_TOKENS = frozenset(
    {
        "apple",
        "meta",
        "target",
        "visa",
        "block",
        "snap",
        "square",
        "cash",
        "general",
        "national",
        "united",
        "first",
        "american",
        "next",
        "on",
    }
)

_METHOD_RANK = {"TICKER": 4, "COMPANY_NAME": 3, "ALIAS": 2, "MANUAL": 1}


@dataclass(frozen=True)
class CompanyIdentity:
    company_id: int
    name: str | None
    ticker: str | None
    aliases: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CompanyMatch:
    company_id: int
    match_method: str
    confidence: int


def match_companies(text: str | None, companies: list[CompanyIdentity]) -> list[CompanyMatch]:
    if not text or not text.strip():
        return []
    found: dict[int, CompanyMatch] = {}
    for company in companies:
        hit = _match_one(text, company)
        if hit is not None:
            found[hit.company_id] = hit
    return list(found.values())


def _match_one(text: str, company: CompanyIdentity) -> CompanyMatch | None:
    best: CompanyMatch | None = None
    if company.ticker and _ticker_hit(text, company.ticker):
        best = CompanyMatch(company.company_id, "TICKER", 90)
    if company.name and _phrase_hit(text, company.name):
        best = _prefer(best, CompanyMatch(company.company_id, "COMPANY_NAME", _name_confidence(company.name)))
    for alias, alias_type in company.aliases:
        if alias_type == "TICKER":
            if _ticker_hit(text, alias):
                best = _prefer(best, CompanyMatch(company.company_id, "TICKER", 90))
            continue
        if not _phrase_hit(text, alias):
            continue
        method = "COMPANY_NAME" if alias_type == "LEGAL_NAME" else "ALIAS"
        confidence = _name_confidence(alias) if method == "COMPANY_NAME" else _alias_confidence(alias)
        best = _prefer(best, CompanyMatch(company.company_id, method, confidence))
    if best is not None and best.match_method == "TICKER" and _phrase_hit(text, company.name or ""):
        return CompanyMatch(company.company_id, "TICKER", 95)
    return best


def _prefer(current: CompanyMatch | None, candidate: CompanyMatch) -> CompanyMatch:
    if current is None:
        return candidate
    if candidate.confidence > current.confidence:
        return candidate
    if candidate.confidence == current.confidence and _METHOD_RANK[candidate.match_method] > _METHOD_RANK[current.match_method]:
        return candidate
    return current


def _ticker_hit(text: str, ticker: str) -> bool:
    symbol = ticker.strip().upper()
    if len(symbol) < 2 or not symbol.isalnum():
        return False
    if len(symbol) <= 3:
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])", text) is not None
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])", text, re.IGNORECASE) is not None


def _phrase_hit(text: str, phrase: str) -> bool:
    cleaned = " ".join(phrase.split())
    if not cleaned:
        return False
    tokens = cleaned.casefold().split(" ")
    if len(tokens) == 1 and (len(tokens[0]) < 5 or tokens[0] in AMBIGUOUS_TOKENS):
        return False
    pattern = r"(?<![A-Za-z0-9])" + r"\s+".join(re.escape(part) for part in tokens) + r"(?![A-Za-z0-9])"
    return re.search(pattern, text.casefold()) is not None


def _name_confidence(phrase: str) -> int:
    return 85 if len(phrase.split()) > 1 else 80


def _alias_confidence(phrase: str) -> int:
    return 80 if len(phrase.split()) > 1 else 75
