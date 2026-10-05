"""Explicit V1 weights and bands for the opportunity score.

Opportunity asks whether structural growth can still compound over 10 to 20
years. It does not reuse the quality score. A component that cannot be scored
is left empty, dropped from the total, and the remaining weights are
renormalized.
"""

from decimal import Decimal

METHOD_VERSION = "opportunity_v1"

WEIGHTS: dict[str, Decimal] = {
    "growth_runway": Decimal("20"),
    "size_runway": Decimal("10"),
    "megatrend": Decimal("15"),
    "market": Decimal("10"),
    "strategic_position": Decimal("10"),
    "bottleneck": Decimal("10"),
    "innovation": Decimal("10"),
    "moat": Decimal("10"),
    "expansion": Decimal("5"),
}

# Long-horizon growth rates. 0.30 means 30 percent. Distinct from quality bands.
GROWTH_RUNWAY_BANDS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("0"), Decimal("0")),
    (Decimal("0.10"), Decimal("0.40")),
    (Decimal("0.20"), Decimal("0.70")),
    (Decimal("0.30"), Decimal("1")),
)

# Research and development expense / revenue. Used only when both figures exist.
RD_INTENSITY_BANDS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("0"), Decimal("0")),
    (Decimal("0.05"), Decimal("0.40")),
    (Decimal("0.10"), Decimal("0.70")),
    (Decimal("0.15"), Decimal("0.90")),
    (Decimal("0.20"), Decimal("1")),
)

# Market-cap ceilings are exclusive, except the 1 trillion boundary.
# Below 1bn is strong but not the maximum: a tiny company is not an opportunity
# by size alone. 1bn to 5bn is the strongest size runway. Above 1tn is weak.
# Exactly 1tn stays in the 500bn-1tn band.
SIZE_UNDER_1BN = Decimal("80")
SIZE_1BN_TO_5BN = Decimal("100")
SIZE_5BN_TO_20BN = Decimal("85")
SIZE_20BN_TO_100BN = Decimal("70")
SIZE_100BN_TO_500BN = Decimal("50")
SIZE_500BN_TO_1TN = Decimal("30")
SIZE_ABOVE_1TN = Decimal("15")

BILLION = Decimal("1000000000")
TRILLION = Decimal("1000000000000")

# A negative acceleration of 10 percentage points removes 15 score points.
DECELERATION_SCALE = Decimal("1.5")
DECELERATION_CAP = Decimal("40")
STRONG_HISTORY = Decimal("0.20")
WEAK_RECENT = Decimal("0.05")
WEAK_RECENT_PENALTY = Decimal("35")
MARGIN_SHIFT = Decimal("0.05")
MARGIN_ADJUSTMENT = Decimal("5")

SOURCE_CONFIDENCE: dict[str, int] = {
    "AUTO_FINANCIAL": 100,
    "DOCUMENT_EXTRACTED": 85,
    "MANUAL_STRUCTURED": 80,
    "ESTIMATED": 50,
    "UNKNOWN": 0,
}

MEGATRENDS: tuple[str, ...] = (
    "AI",
    "Robotics",
    "Semiconductors",
    "Data Centers",
    "Energy",
    "Power Grid",
    "Nuclear",
    "Space",
    "Drones",
    "Defense",
    "Cybersecurity",
    "Biotechnology",
    "Quantum",
    "Industrial Automation",
    "Energy Storage",
    "Water Infrastructure",
)

STRATEGIC_ROLES: tuple[str, ...] = (
    "END_PRODUCT",
    "CRITICAL_SUPPLIER",
    "BOTTLENECK_SUPPLIER",
    "INFRASTRUCTURE_PROVIDER",
    "EQUIPMENT_PROVIDER",
    "MATERIAL_PROVIDER",
    "SOFTWARE_PLATFORM",
)

# Planned SEC concept. Opportunity V1 does not require it to be stored yet.
RD_XBRL_CONCEPT = "ResearchAndDevelopmentExpense"
