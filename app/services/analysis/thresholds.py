"""Explicit V1 thresholds for the quality score.

Each band is (metric threshold, score from 0 to 1), sorted by threshold.
Values between two thresholds are interpolated. Values outside the range
keep the nearest endpoint. A missing input does not use these bands: the
component is left empty and dropped from the score.
"""

from decimal import Decimal

WEIGHTS: dict[str, Decimal] = {
    "revenue_growth": Decimal("20"),
    "profit_growth": Decimal("15"),
    "fcf_growth": Decimal("15"),
    "margins": Decimal("15"),
    "profitability": Decimal("10"),
    "debt": Decimal("10"),
    "cash": Decimal("5"),
    "dilution": Decimal("5"),
    "stability": Decimal("5"),
}

# Growth rates and margins are decimals: 0.10 means 10%.
GROWTH_BANDS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("0"), Decimal("0")),
    (Decimal("0.05"), Decimal("0.25")),
    (Decimal("0.10"), Decimal("0.50")),
    (Decimal("0.20"), Decimal("0.75")),
    (Decimal("0.30"), Decimal("1")),
)

MARGIN_BANDS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("0"), Decimal("0")),
    (Decimal("0.05"), Decimal("0.25")),
    (Decimal("0.10"), Decimal("0.50")),
    (Decimal("0.20"), Decimal("0.75")),
    (Decimal("0.30"), Decimal("1")),
)

ROE_BANDS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("0"), Decimal("0")),
    (Decimal("0.08"), Decimal("0.25")),
    (Decimal("0.12"), Decimal("0.50")),
    (Decimal("0.18"), Decimal("0.75")),
    (Decimal("0.25"), Decimal("1")),
)

ROA_BANDS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("0"), Decimal("0")),
    (Decimal("0.03"), Decimal("0.25")),
    (Decimal("0.06"), Decimal("0.50")),
    (Decimal("0.10"), Decimal("0.75")),
    (Decimal("0.15"), Decimal("1")),
)

# Lower debt / FCF is better. 0 means no debt against positive free cash flow.
DEBT_TO_FCF_BANDS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("0"), Decimal("1")),
    (Decimal("1"), Decimal("0.75")),
    (Decimal("3"), Decimal("0.50")),
    (Decimal("5"), Decimal("0.25")),
    (Decimal("8"), Decimal("0")),
)

CASH_TO_DEBT_BANDS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("0"), Decimal("0")),
    (Decimal("0.25"), Decimal("0.25")),
    (Decimal("0.50"), Decimal("0.50")),
    (Decimal("1"), Decimal("0.75")),
    (Decimal("2"), Decimal("1")),
)

# Lower share growth is better. Buybacks at or below -2% a year score the maximum.
DILUTION_BANDS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("-0.02"), Decimal("1")),
    (Decimal("0"), Decimal("0.75")),
    (Decimal("0.01"), Decimal("0.50")),
    (Decimal("0.03"), Decimal("0.25")),
    (Decimal("0.05"), Decimal("0")),
)

# A year-to-year share count within 3% of one of these factors is treated as a split.
# The series is then marked not comparable. No split-adjusted history is rebuilt.
SPLIT_FACTORS: tuple[int, ...] = (2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50)
SPLIT_TOLERANCE = Decimal("0.03")

CONFIDENCE_POINTS: dict[str, Decimal] = {
    "HIGH": Decimal("100"),
    "MEDIUM": Decimal("70"),
    "LOW": Decimal("40"),
    "MISSING": Decimal("0"),
}

METHOD_VERSION = "quality_v1"
