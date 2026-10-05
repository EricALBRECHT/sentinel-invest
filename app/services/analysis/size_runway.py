"""Size runway from market capitalization.

A larger company has less room to multiply. The band is only one opportunity
component. Market cap alone never becomes the headline score: the assembler
drops size when no other component can be scored, so a weak micro-cap is not
rewarded just for being small.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.services.analysis.opportunity_thresholds import (
    BILLION,
    SIZE_100BN_TO_500BN,
    SIZE_1BN_TO_5BN,
    SIZE_20BN_TO_100BN,
    SIZE_500BN_TO_1TN,
    SIZE_5BN_TO_20BN,
    SIZE_ABOVE_1TN,
    SIZE_UNDER_1BN,
    TRILLION,
)


@dataclass(frozen=True)
class SizeRunway:
    score: Decimal | None
    evidence: str


def size_runway_score(market_cap: Decimal | None) -> SizeRunway:
    if market_cap is None or market_cap <= 0:
        return SizeRunway(None, "Market capitalization is missing, so size runway is not scored.")
    score, label = _band(market_cap)
    return SizeRunway(
        score,
        f"Market capitalization {market_cap} falls in the {label} size-runway band (score {score}).",
    )


def _band(market_cap: Decimal) -> tuple[Decimal, str]:
    if market_cap < BILLION:
        return SIZE_UNDER_1BN, "under 1 billion: strong"
    if market_cap < 5 * BILLION:
        return SIZE_1BN_TO_5BN, "1 to 5 billion: very strong"
    if market_cap < 20 * BILLION:
        return SIZE_5BN_TO_20BN, "5 to 20 billion: strong"
    if market_cap < 100 * BILLION:
        return SIZE_20BN_TO_100BN, "20 to 100 billion: medium-strong"
    if market_cap < 500 * BILLION:
        return SIZE_100BN_TO_500BN, "100 to 500 billion: medium"
    if market_cap <= TRILLION:
        return SIZE_500BN_TO_1TN, "500 billion to 1 trillion: weak-medium"
    return SIZE_ABOVE_1TN, "above 1 trillion: weak"
