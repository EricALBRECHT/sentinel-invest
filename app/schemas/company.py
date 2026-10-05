from datetime import datetime
from decimal import Decimal

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.sec.cik import normalize_cik


def _strip_text(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


def _empty_to_none(value: object) -> object:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _normalize_cik_field(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return normalize_cik(value)


_MARKET_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")


def _normalize_market_symbol(value: object) -> object:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    cleaned = value.strip().upper()
    if not cleaned:
        return None
    if _MARKET_SYMBOL.fullmatch(cleaned) is None:
        raise ValueError("Market symbol must contain letters, digits, dots, underscores, or hyphens")
    return cleaned


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    ticker: str = Field(min_length=1, max_length=32)
    isin: str | None = Field(default=None, max_length=12)
    country: str | None = Field(default=None, max_length=64)
    exchange: str | None = Field(default=None, max_length=64)
    sector: str | None = Field(default=None, max_length=128)
    industry: str | None = Field(default=None, max_length=128)
    market_cap: Decimal | None = Field(default=None, ge=0)
    pea_eligible: bool = False
    sec_cik: str | None = Field(default=None, max_length=10)
    market_symbol: str | None = Field(default=None, max_length=32)

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value: object) -> object:
        return _strip_text(value)

    @field_validator("ticker", mode="before")
    @classmethod
    def clean_ticker(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("isin", "country", "exchange", "sector", "industry", mode="before")
    @classmethod
    def clean_optional_text(cls, value: object) -> object:
        return _empty_to_none(value)

    @field_validator("isin")
    @classmethod
    def uppercase_isin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.upper()

    @field_validator("sec_cik", mode="before")
    @classmethod
    def clean_cik(cls, value: object) -> object:
        return _normalize_cik_field(value)

    @field_validator("market_symbol", mode="before")
    @classmethod
    def clean_market_symbol(cls, value: object) -> object:
        return _normalize_market_symbol(value)


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    ticker: str | None = Field(default=None, min_length=1, max_length=32)
    isin: str | None = Field(default=None, max_length=12)
    country: str | None = Field(default=None, max_length=64)
    exchange: str | None = Field(default=None, max_length=64)
    sector: str | None = Field(default=None, max_length=128)
    industry: str | None = Field(default=None, max_length=128)
    market_cap: Decimal | None = Field(default=None, ge=0)
    pea_eligible: bool | None = None
    sec_cik: str | None = Field(default=None, max_length=10)
    market_symbol: str | None = Field(default=None, max_length=32)

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value: object) -> object:
        return _strip_text(value)

    @field_validator("ticker", mode="before")
    @classmethod
    def clean_ticker(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("isin", "country", "exchange", "sector", "industry", mode="before")
    @classmethod
    def clean_optional_text(cls, value: object) -> object:
        return _empty_to_none(value)

    @field_validator("isin")
    @classmethod
    def uppercase_isin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.upper()

    @field_validator("sec_cik", mode="before")
    @classmethod
    def clean_cik(cls, value: object) -> object:
        return _normalize_cik_field(value)

    @field_validator("market_symbol", mode="before")
    @classmethod
    def clean_market_symbol(cls, value: object) -> object:
        return _normalize_market_symbol(value)

    @model_validator(mode="after")
    def reject_null_identifiers(self) -> "CompanyUpdate":
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Company name is required")
        if "ticker" in self.model_fields_set and self.ticker is None:
            raise ValueError("Ticker is required")
        return self


class CompanyRead(BaseModel):
    id: int
    name: str
    ticker: str
    isin: str | None
    country: str | None
    exchange: str | None
    sector: str | None
    industry: str | None
    market_cap: Decimal | None
    pea_eligible: bool
    sec_cik: str | None
    market_symbol: str | None
    universe_status: str
    universe_priority: int
    discovery_source: str | None
    discovery_reason: str | None
    is_active: bool
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
