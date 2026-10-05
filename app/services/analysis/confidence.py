"""Data-quality labels for one annual series.

Five or more usable years can be HIGH, three or four are MEDIUM, one or two
are LOW, and none is MISSING. Provenance can lower a family further.

Free cash flow is HIGH only when at least five years are usable and the latest
five fiscal years use ``NetCashProvidedByUsedInOperatingActivities`` plus an
exact PP&E capex concept. ``PaymentsToAcquireProductiveAssets``, or more than
one concept in that window, caps the family at MEDIUM. Missing or unrecognized
provenance is LOW.

Debt is HIGH only for one comprehensive concept on five or more coherent years.
``LongTermDebt`` and a sum of debt concepts are MEDIUM. A partial or ambiguous
label is LOW.

Shares stay LOW when a probable split is detected or when the cover-page
concept ``EntityCommonStockSharesOutstanding`` is used.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.services.analysis.annual_metrics import AnnualMetrics, AnnualSnapshot
from app.services.analysis.thresholds import CONFIDENCE_POINTS
from app.services.sec.mappings import (
    CAPEX_FALLBACK_CONCEPT,
    DEBT_COMPREHENSIVE_CONCEPT,
    DEBT_FALLBACK_CONCEPT,
    DEBT_PARTIAL_CONCEPTS,
    EXACT_CAPEX_CONCEPTS,
    OPERATING_CASH_FLOW_CONCEPT,
    SHARES_FALLBACK_CONCEPT,
    SHARES_PRIMARY_CONCEPT,
)

FAMILY_NAMES = (
    "revenue_confidence",
    "profit_confidence",
    "fcf_confidence",
    "debt_confidence",
    "shares_confidence",
)
_KNOWN_CAPEX = EXACT_CAPEX_CONCEPTS | {CAPEX_FALLBACK_CONCEPT}


@dataclass(frozen=True)
class FamilyConfidence:
    level: str
    score: int
    reason: str
    source_concepts: tuple[str, ...]
    mixed_source_concepts: bool

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "score": self.score,
            "reason": self.reason,
            "source_concepts": list(self.source_concepts),
            "mixed_source_concepts": self.mixed_source_concepts,
        }


@dataclass(frozen=True)
class ConfidenceAssessment:
    revenue: FamilyConfidence
    profit: FamilyConfidence
    fcf: FamilyConfidence
    debt: FamilyConfidence
    shares: FamilyConfidence
    notes: tuple[str, ...]

    @property
    def revenue_confidence(self) -> str:
        return self.revenue.level

    @property
    def profit_confidence(self) -> str:
        return self.profit.level

    @property
    def fcf_confidence(self) -> str:
        return self.fcf.level

    @property
    def debt_confidence(self) -> str:
        return self.debt.level

    @property
    def shares_confidence(self) -> str:
        return self.shares.level

    def levels(self) -> dict[str, str]:
        return {
            "revenue_confidence": self.revenue.level,
            "profit_confidence": self.profit.level,
            "fcf_confidence": self.fcf.level,
            "debt_confidence": self.debt.level,
            "shares_confidence": self.shares.level,
        }

    def as_json(self) -> dict:
        return {
            **self.levels(),
            "revenue": self.revenue.as_dict(),
            "profit": self.profit.as_dict(),
            "fcf": self.fcf.as_dict(),
            "debt": self.debt.as_dict(),
            "shares": self.shares.as_dict(),
            "notes": list(self.notes),
        }


def confidence_from_count(
    count: int,
    *,
    comparability_issue: bool = False,
    xbrl_fallback: bool = False,
) -> str:
    if count <= 0:
        return "MISSING"
    if comparability_issue or count <= 2:
        return "LOW"
    if xbrl_fallback or count < 5:
        return "MEDIUM"
    return "HIGH"


def build_confidence(
    snapshots: list[AnnualSnapshot],
    metrics: AnnualMetrics,
    *,
    capex_fallback: bool = False,
) -> ConfidenceAssessment:
    revenue = _count_family(snapshots, _has_revenue, "revenue_source_concept", "revenue")
    profit = _count_family(snapshots, _has_profit, "net_income_source_concept", "profit")
    fcf = _fcf_family(snapshots, capex_fallback=capex_fallback)
    debt = _debt_family(snapshots)
    shares = _shares_family(snapshots, metrics)
    notes = tuple(
        family.reason
        for family in (fcf, debt, shares)
        if family.level != "HIGH"
    )
    return ConfidenceAssessment(
        revenue=revenue,
        profit=profit,
        fcf=fcf,
        debt=debt,
        shares=shares,
        notes=notes,
    )


def overall_confidence_score(levels: dict[str, str], available_weight: Decimal) -> int:
    """Average of the five family scores, reduced by the share of excluded components."""
    total = sum((CONFIDENCE_POINTS[levels[name]] for name in FAMILY_NAMES), Decimal(0))
    data_score = total / Decimal(len(FAMILY_NAMES))
    coverage = available_weight / Decimal(100)
    return int((data_score * coverage).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _count_family(snapshots, predicate, concept_attr: str, noun: str) -> FamilyConfidence:
    count = _count(snapshots, predicate)
    level = confidence_from_count(count)
    concepts = _concepts(snapshots, predicate, concept_attr)
    return _family(
        level,
        _count_reason(level, count, noun),
        concepts,
        mixed=len(concepts) > 1,
    )


def _fcf_family(snapshots: list[AnnualSnapshot], *, capex_fallback: bool) -> FamilyConfidence:
    count = _count(snapshots, lambda row: row.free_cash_flow is not None)
    inspected = [row for row in _recent(snapshots) if row.free_cash_flow is not None]
    operating: list[str] = []
    capex: list[str] = []
    missing = False
    for row in inspected:
        operating_concept = row.operating_cash_flow_source_concept
        capex_concept = row.capital_expenditure_source_concept
        if capex_concept is None and capex_fallback:
            capex_concept = CAPEX_FALLBACK_CONCEPT
        if operating_concept:
            operating.append(operating_concept)
        else:
            missing = True
        if capex_concept:
            capex.append(capex_concept)
        else:
            missing = True
    level, reason = _fcf_level(count, operating, capex, missing=missing)
    return _family(level, reason, _unique(operating + capex), _mixed(operating, capex))


def _fcf_level(
    count: int,
    operating: list[str],
    capex: list[str],
    *,
    missing: bool,
) -> tuple[str, str]:
    if count <= 0:
        return "MISSING", "No annual free cash flow is available."
    if count <= 2:
        return "LOW", f"Only {count} annual free-cash-flow observations are usable."
    mixed = _mixed(operating, capex)
    productive = CAPEX_FALLBACK_CONCEPT in capex
    exact = bool(capex) and set(capex) <= EXACT_CAPEX_CONCEPTS
    reliable = bool(operating) and set(operating) == {OPERATING_CASH_FLOW_CONCEPT}
    unrecognized = any(item not in {OPERATING_CASH_FLOW_CONCEPT} for item in operating) or any(
        item not in _KNOWN_CAPEX for item in capex
    )
    if unrecognized or not capex:
        return "LOW", "Free-cash-flow provenance is incomplete or uses an unrecognized concept."
    if mixed:
        return (
            "MEDIUM",
            "Several capital-expenditure or operating-cash-flow concepts are mixed "
            "across the latest five fiscal years.",
        )
    if productive:
        return (
            "MEDIUM",
            "Capex derived from PaymentsToAcquireProductiveAssets in recent FY periods.",
        )
    if missing or not reliable or not exact:
        return "LOW", "Operating-cash-flow or capex provenance is missing or not an exact concept."
    if count < 5:
        return "MEDIUM", f"{count} usable annual free-cash-flow observations; five are required for HIGH."
    return (
        "HIGH",
        "Five or more annual periods use NetCashProvidedByUsedInOperatingActivities "
        "and an exact property, plant, and equipment capex concept.",
    )


def _debt_family(snapshots: list[AnnualSnapshot]) -> FamilyConfidence:
    count = _count(snapshots, lambda row: row.total_debt is not None)
    inspected = [row for row in _recent(snapshots) if row.total_debt is not None]
    labels = [row.debt_source_concept for row in inspected if row.debt_source_concept]
    missing = any(row.debt_source_concept is None for row in inspected)
    level, reason = _debt_level(count, labels, missing=missing)
    return _family(level, reason, _debt_concepts(labels), len(set(labels)) > 1)


def _debt_level(count: int, labels: list[str], *, missing: bool) -> tuple[str, str]:
    if count <= 0:
        return "MISSING", "No annual debt is available."
    if count <= 2:
        return "LOW", f"Only {count} annual debt observations are usable."
    if not labels:
        return "LOW", "Debt concept was not recorded, so the structure is uncertain."
    kinds = {_debt_kind(label) for label in labels}
    if "uncertain" in kinds:
        return "LOW", "Debt tags are ambiguous or unrecognized, so the structure is uncertain."
    if "partial" in kinds:
        return "LOW", "Debt series is partial: only a portion of the debt concepts is present."
    if len(set(labels)) > 1 or missing:
        return (
            "MEDIUM",
            "Several debt concepts are mixed, or the concept is missing on some recent periods.",
        )
    if kinds == {"comprehensive"} and count >= 5:
        return (
            "HIGH",
            "Debt uses one comprehensive long-term debt concept on five or more coherent annual periods.",
        )
    if kinds == {"fallback"}:
        return "MEDIUM", "Debt uses the LongTermDebt fallback rather than a comprehensive debt concept."
    if kinds == {"reconstructed"}:
        return "MEDIUM", "Debt is reconstructed from several XBRL concepts."
    if count < 5:
        return "MEDIUM", f"{count} usable annual debt observations; five coherent years are required for HIGH."
    return "MEDIUM", "Debt provenance does not meet the comprehensive-concept rule."


def _debt_kind(label: str) -> str:
    if label == DEBT_COMPREHENSIVE_CONCEPT:
        return "comprehensive"
    if ":ambiguous" in label:
        return "uncertain"
    if "+" in label:
        return "reconstructed"
    if label == DEBT_FALLBACK_CONCEPT:
        return "fallback"
    if label in DEBT_PARTIAL_CONCEPTS:
        return "partial"
    return "uncertain"


def _shares_family(snapshots: list[AnnualSnapshot], metrics: AnnualMetrics) -> FamilyConfidence:
    count = _count(
        snapshots,
        lambda row: row.shares_outstanding is not None and row.shares_outstanding > 0,
    )
    concepts = _concepts(
        snapshots,
        lambda row: row.shares_outstanding is not None and row.shares_outstanding > 0,
        "shares_source_concept",
    )
    level, reason = _shares_level(count, concepts, metrics.shares_comparable)
    return _family(level, reason, concepts, len(concepts) > 1)


def _shares_level(count: int, concepts: list[str], comparable: bool) -> tuple[str, str]:
    if count <= 0:
        return "MISSING", "No annual share count is available."
    uses_fallback = SHARES_FALLBACK_CONCEPT in concepts
    if not comparable and uses_fallback:
        return (
            "LOW",
            "A probable share split makes the series not comparable, and some periods use "
            "EntityCommonStockSharesOutstanding.",
        )
    if not comparable:
        return "LOW", "A probable share split makes the share series not comparable."
    if uses_fallback:
        return "LOW", "Shares use the EntityCommonStockSharesOutstanding cover-page fallback."
    if count <= 2:
        return "LOW", f"Only {count} annual share observations are usable."
    if len(concepts) > 1:
        return "MEDIUM", "Several share concepts are mixed across the latest five fiscal years."
    if concepts and concepts != [SHARES_PRIMARY_CONCEPT]:
        return "LOW", "Share provenance uses an unrecognized concept."
    if count < 5:
        return "MEDIUM", f"{count} annual share observations are usable."
    if concepts == [SHARES_PRIMARY_CONCEPT]:
        return (
            "HIGH",
            "Five or more annual periods use CommonStockSharesOutstanding and no probable split was detected.",
        )
    return "HIGH", "Five or more annual share observations are usable. The XBRL concept was not recorded."


def _family(level: str, reason: str, concepts: list[str], mixed: bool) -> FamilyConfidence:
    return FamilyConfidence(
        level=level,
        score=int(CONFIDENCE_POINTS[level]),
        reason=reason,
        source_concepts=tuple(concepts),
        mixed_source_concepts=mixed,
    )


def _count_reason(level: str, count: int, noun: str) -> str:
    if level == "MISSING":
        return f"No annual {noun} is available."
    if level == "LOW":
        return f"Only {count} annual {noun} observations are usable."
    if level == "MEDIUM":
        return f"{count} annual {noun} observations are usable."
    return f"{count} coherent annual {noun} observations are usable."


def _concepts(snapshots: list[AnnualSnapshot], predicate, concept_attr: str) -> list[str]:
    return _unique(
        getattr(row, concept_attr)
        for row in _recent(snapshots)
        if predicate(row)
    )


def _recent(snapshots: list[AnnualSnapshot], years: int = 5) -> list[AnnualSnapshot]:
    ordered = sorted(snapshots, key=lambda row: row.fiscal_year)
    if not ordered:
        return []
    start = ordered[-1].fiscal_year - (years - 1)
    return [row for row in ordered if row.fiscal_year >= start]


def _debt_concepts(labels: list[str]) -> list[str]:
    concepts: list[str] = []
    for label in labels:
        for part in label.split(":", 1)[0].split("+"):
            if part and part not in concepts:
                concepts.append(part)
    return concepts


def _mixed(left: list[str], right: list[str]) -> bool:
    return len(set(left)) > 1 or len(set(right)) > 1


def _unique(values) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def _has_revenue(row: AnnualSnapshot) -> bool:
    return row.revenue is not None and row.revenue > 0


def _has_profit(row: AnnualSnapshot) -> bool:
    return row.net_income is not None


def _count(snapshots: list[AnnualSnapshot], predicate) -> int:
    return sum(1 for row in snapshots if predicate(row))
