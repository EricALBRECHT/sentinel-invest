"""Shared shape for index constituents."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NormalizedMember:
    name: str
    ticker: str
    country: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    isin: str | None = None
    sec_cik: str | None = None
    market_symbol: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


class UniverseProvider(ABC):
    provider_name: str
    universe_name: str
    source: str
    documentation: str

    @abstractmethod
    async def fetch_members(self) -> list[NormalizedMember]:
        raise NotImplementedError

    def normalize_member(self, raw: dict[str, Any]) -> NormalizedMember:
        ticker = _text(raw.get("ticker") or raw.get("symbol")).upper()
        name = _text(raw.get("name") or raw.get("security") or raw.get("company"))
        if not ticker or not name:
            raise ValueError("Index member needs ticker and name")
        cik = _cik(raw.get("sec_cik") or raw.get("cik"))
        symbol = _text(raw.get("market_symbol")) or ticker
        return NormalizedMember(
            name=name,
            ticker=ticker,
            country=_text(raw.get("country")) or "US",
            exchange=_text(raw.get("exchange")),
            sector=_text(raw.get("sector")),
            industry=_text(raw.get("industry")),
            isin=_text(raw.get("isin")),
            sec_cik=cik,
            market_symbol=symbol,
            source_metadata=dict(raw.get("source_metadata") or {}),
        )


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _cik(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    return digits.zfill(10)[-10:]
