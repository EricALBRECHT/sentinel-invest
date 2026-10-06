"""Public index providers. They only return normalized members."""

from app.services.universe.providers.base import NormalizedMember, UniverseProvider
from app.services.universe.providers.nasdaq100 import Nasdaq100Provider
from app.services.universe.providers.sp500 import Sp500Provider

__all__ = [
    "NormalizedMember",
    "UniverseProvider",
    "Sp500Provider",
    "Nasdaq100Provider",
    "provider_for",
]


def provider_for(universe_name: str) -> UniverseProvider:
    name = universe_name.strip().upper()
    if name == "SP500":
        return Sp500Provider()
    if name == "NASDAQ100":
        return Nasdaq100Provider()
    raise ValueError(f"Unsupported universe provider: {universe_name}")
