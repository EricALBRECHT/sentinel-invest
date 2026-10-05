"""Structural growth runway from annual financials.

The level uses the longest available CAGR for revenue, net income, and free
cash flow. It is not the quality score. A still-fast series scores high.
Deceleration lowers it: the recent 3-year CAGR is compared with the 5-year
CAGR, and a strong 5-year history with a very weak latest year is reduced
further. Margin change can move the score by five points at most.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.services.analysis.annual_metrics import AnnualMetrics, AnnualSnapshot
from app.services.analysis.opportunity_thresholds import (
    DECELERATION_CAP,
    DECELERATION_SCALE,
    GROWTH_RUNWAY_BANDS,
    MARGIN_ADJUSTMENT,
    MARGIN_SHIFT,
    RD_INTENSITY_BANDS,
    STRONG_HISTORY,
    WEAK_RECENT,
    WEAK_RECENT_PENALTY,
)
from app.services.analysis.quality_score import score_band

POINTS = Decimal("0.01")


@dataclass(frozen=True)
class GrowthRunway:
    score: Decimal | None
    acceleration: Decimal | None
    evidence: str
    basis: dict[str, str | None]


def revenue_growth_acceleration(metrics: AnnualMetrics) -> Decimal | None:
    """Recent compound rate minus the longer one. Positive means acceleration."""
    if metrics.revenue_cagr_3y is not None and metrics.revenue_cagr_5y is not None:
        return (metrics.revenue_cagr_3y - metrics.revenue_cagr_5y).quantize(POINTS)
    if metrics.revenue_growth_1y is not None and metrics.revenue_cagr_3y is not None:
        return (metrics.revenue_growth_1y - metrics.revenue_cagr_3y).quantize(POINTS)
    return None


def growth_runway_score(snapshots: list[AnnualSnapshot], metrics: AnnualMetrics) -> GrowthRunway:
    revenue, revenue_basis = _long_rate(
        metrics.revenue_cagr_5y,
        metrics.revenue_cagr_3y,
        metrics.revenue_growth_1y,
    )
    profit, profit_basis = _long_rate(
        metrics.net_income_cagr_5y,
        metrics.net_income_cagr_3y,
        metrics.net_income_growth_1y,
    )
    fcf, fcf_basis = _long_rate(
        metrics.fcf_cagr_5y,
        metrics.fcf_cagr_3y,
        metrics.fcf_growth_1y,
    )
    basis = {"revenue": revenue_basis, "profit": profit_basis, "fcf": fcf_basis}
    scored = [
        score_band(value, GROWTH_RUNWAY_BANDS) * Decimal(100)
        for value in (revenue, profit, fcf)
        if value is not None and score_band(value, GROWTH_RUNWAY_BANDS) is not None
    ]
    acceleration = revenue_growth_acceleration(metrics)
    if not scored:
        return GrowthRunway(None, acceleration, "No usable annual growth rate is available.", basis)

    base = sum(scored, Decimal(0)) / Decimal(len(scored))
    penalty = _deceleration_penalty(metrics, acceleration)
    score = _clamp(base - penalty + _margin_adjustment(snapshots))
    evidence = (
        f"Growth level averages the longest available CAGR across {len(scored)} series "
        f"({_format(base)} before adjustments). "
        f"Revenue acceleration is {_format(acceleration)}. "
        f"Deceleration penalty is {_format(penalty)} points."
    )
    return GrowthRunway(score.quantize(POINTS, rounding=ROUND_HALF_UP), acceleration, evidence, basis)


def rd_intensity_score(research_and_development: Decimal | None, revenue: Decimal | None) -> Decimal | None:
    """Map R&D / revenue onto 0-100. A missing or non-positive revenue is null.

    The planned XBRL concept is ResearchAndDevelopmentExpense. V1 does not
    require that fact to be stored.
    """
    fraction = score_band(
        None if revenue is None or revenue <= 0 or research_and_development is None
        else research_and_development / revenue,
        RD_INTENSITY_BANDS,
    )
    if fraction is None:
        return None
    return (fraction * Decimal(100)).quantize(POINTS, rounding=ROUND_HALF_UP)


def _long_rate(
    cagr_5y: Decimal | None,
    cagr_3y: Decimal | None,
    growth_1y: Decimal | None,
) -> tuple[Decimal | None, str | None]:
    if cagr_5y is not None:
        return cagr_5y, "cagr_5y"
    if cagr_3y is not None:
        return cagr_3y, "cagr_3y"
    if growth_1y is not None:
        return growth_1y, "growth_1y"
    return None, None


def _deceleration_penalty(metrics: AnnualMetrics, acceleration: Decimal | None) -> Decimal:
    penalty = Decimal(0)
    if acceleration is not None and acceleration < 0:
        penalty = min(DECELERATION_CAP, abs(acceleration) * Decimal(100) * DECELERATION_SCALE)
    if (
        metrics.revenue_cagr_5y is not None
        and metrics.revenue_cagr_5y >= STRONG_HISTORY
        and metrics.revenue_growth_1y is not None
        and metrics.revenue_growth_1y < WEAK_RECENT
    ):
        penalty = max(penalty, WEAK_RECENT_PENALTY)
    return penalty


def _margin_adjustment(snapshots: list[AnnualSnapshot]) -> Decimal:
    ordered = sorted(snapshots, key=lambda row: row.fiscal_year)
    if not ordered:
        return Decimal(0)
    latest = ordered[-1]
    prior = next((row for row in ordered if row.fiscal_year == latest.fiscal_year - 3), None)
    latest_margin = _operating_margin(latest)
    prior_margin = _operating_margin(prior) if prior is not None else None
    if latest_margin is None or prior_margin is None:
        return Decimal(0)
    change = latest_margin - prior_margin
    if change >= MARGIN_SHIFT:
        return MARGIN_ADJUSTMENT
    if change <= -MARGIN_SHIFT:
        return -MARGIN_ADJUSTMENT
    return Decimal(0)


def _operating_margin(snapshot: AnnualSnapshot | None) -> Decimal | None:
    if snapshot is None or snapshot.revenue is None or snapshot.revenue <= 0:
        return None
    if snapshot.operating_income is None:
        return None
    return snapshot.operating_income / snapshot.revenue


def _clamp(value: Decimal) -> Decimal:
    return min(Decimal(100), max(Decimal(0), value))


def _format(value: Decimal | None) -> str:
    if value is None:
        return "unavailable"
    return format(value.quantize(POINTS, rounding=ROUND_HALF_UP), "f")
