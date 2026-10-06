"""Names and checks for the company universe.

Priority only decides which companies are analyzed more often.
It is not a buy or sell signal.
"""

import re

UNIVERSE_STATUSES = (
    "DISCOVERED",
    "WATCHED",
    "SCREENED",
    "DEEP_ANALYSIS",
    "PORTFOLIO",
    "ARCHIVED",
)

DISCOVERY_SOURCES = (
    "MANUAL",
    "SP500",
    "NASDAQ100",
    "PEA",
    "EUROPE",
    "SUPPLY_CHAIN",
    "BOTTLENECK",
    "INSTITUTIONAL",
    "OTHER",
)

CANONICAL_UNIVERSES = (
    "SP500",
    "NASDAQ100",
    "PEA",
    "EURO_STOXX",
    "EUROPE",
    "MANUAL",
    "SUPPLY_CHAIN",
    "BOTTLENECK",
)

_UNIVERSE_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
SUPPLY_DISCOVERY = frozenset({"SUPPLY_CHAIN", "BOTTLENECK"})


class UniverseRuleError(ValueError):
    """A universe name, source, or status is not usable."""


def normalize_token(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UniverseRuleError(f"{label} is required")
    return value.strip().upper()


def normalize_universe_name(value: str) -> str:
    name = normalize_token(value, label="Universe name")
    if _UNIVERSE_NAME.fullmatch(name) is None:
        raise UniverseRuleError("Universe name must be 2-32 uppercase letters, digits, or underscores")
    return name


def normalize_source(value: str) -> str:
    source = normalize_token(value, label="Source")
    if source not in DISCOVERY_SOURCES:
        raise UniverseRuleError("Unknown discovery source")
    return source


def normalize_status(value: str) -> str:
    status = normalize_token(value, label="Universe status")
    if status not in UNIVERSE_STATUSES:
        raise UniverseRuleError("Unknown universe status")
    return status


_STATUS_RANK = {
    "ARCHIVED": 0,
    "DISCOVERED": 1,
    "SCREENED": 2,
    "WATCHED": 3,
    "DEEP_ANALYSIS": 4,
    "PORTFOLIO": 5,
}


def status_after_add(current: str, requested: str | None) -> str:
    """Adding a company never lowers a higher pipeline status."""
    if requested:
        wanted = normalize_status(requested)
        if _STATUS_RANK[wanted] >= _STATUS_RANK.get(current, 1):
            return wanted
        return current
    if current in {"DISCOVERED", "ARCHIVED"}:
        return "WATCHED"
    return current
