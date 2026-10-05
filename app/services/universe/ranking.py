"""Analysis priority. The result orders work. It does not recommend a trade."""

from dataclasses import dataclass
from decimal import Decimal

_QUALITY_POINTS = Decimal("80")
_OPPORTUNITY_POINTS = Decimal("75")


@dataclass(frozen=True)
class PriorityInputs:
    universe_status: str
    quality_score: Decimal | None
    opportunity_score: Decimal | None
    ranking_eligible: bool
    active_universe_count: int
    discovery_source: str | None
    pea_eligible: bool


def score_priority(raw: int) -> int:
    return min(100, max(0, raw))


def priority_parts(inputs: PriorityInputs) -> dict[str, int]:
    parts = {
        "portfolio": 30 if inputs.universe_status == "PORTFOLIO" else 0,
        "deep_analysis": 20 if inputs.universe_status == "DEEP_ANALYSIS" else 0,
        "quality": 15 if _at_least(inputs.quality_score, _QUALITY_POINTS) else 0,
        "opportunity": 15
        if inputs.ranking_eligible and _at_least(inputs.opportunity_score, _OPPORTUNITY_POINTS)
        else 0,
        "multi_universe": 10 if inputs.active_universe_count > 1 else 0,
        "discovery": 10 if inputs.discovery_source in {"SUPPLY_CHAIN", "BOTTLENECK"} else 0,
        "pea": 5 if inputs.pea_eligible else 0,
    }
    raw = sum(parts.values())
    parts["raw"] = raw
    parts["total"] = score_priority(raw)
    return parts


def _at_least(value: Decimal | None, minimum: Decimal) -> bool:
    return value is not None and value >= minimum
