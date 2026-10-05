"""Quality Score V1.

Each component is a 0-to-1 band score multiplied by its weight. A component
whose inputs are missing is null. It is not scored as zero. The headline
score renormalizes the earned points over the weights that could be scored:

    quality_score = earned_points / available_weight * 100

Growth prefers a 5-year CAGR, then a 3-year CAGR, then one-year growth.
Share growth is not scored when a probable split makes the series
non-comparable. Stability is the share of simple annual checks that pass.
Checks that cannot be evaluated are skipped, not failed. If none can be
evaluated, stability is null.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.services.analysis.annual_metrics import AnnualMetrics, AnnualSnapshot, ShareSplit
from app.services.analysis.thresholds import (
    CASH_TO_DEBT_BANDS,
    DEBT_TO_FCF_BANDS,
    DILUTION_BANDS,
    GROWTH_BANDS,
    MARGIN_BANDS,
    ROA_BANDS,
    ROE_BANDS,
    WEIGHTS,
)

POINTS = Decimal("0.01")
DEBT_JUMP = Decimal("1.5")


@dataclass(frozen=True)
class QualityScore:
    quality_score: Decimal | None
    components: dict[str, Decimal | None]
    available_weight: Decimal
    growth_basis: dict[str, str | None]
    anomalies: tuple[str, ...]


def score_band(value: Decimal | None, bands: tuple[tuple[Decimal, Decimal], ...]) -> Decimal | None:
    if value is None:
        return None
    if value <= bands[0][0]:
        return bands[0][1]
    if value >= bands[-1][0]:
        return bands[-1][1]
    for (left_x, left_y), (right_x, right_y) in zip(bands, bands[1:]):
        if left_x <= value <= right_x:
            span = right_x - left_x
            if span == 0:
                return right_y
            position = (value - left_x) / span
            return left_y + position * (right_y - left_y)
    return bands[-1][1]


def build_quality_score(snapshots: list[AnnualSnapshot], metrics: AnnualMetrics) -> QualityScore:
    revenue_value, revenue_basis = _prefer_growth(
        metrics.revenue_cagr_5y,
        metrics.revenue_cagr_3y,
        metrics.revenue_growth_1y,
    )
    profit_value, profit_basis = _prefer_growth(
        metrics.net_income_cagr_5y,
        metrics.net_income_cagr_3y,
        metrics.net_income_growth_1y,
    )
    fcf_value, fcf_basis = _prefer_growth(
        metrics.fcf_cagr_5y,
        metrics.fcf_cagr_3y,
        metrics.fcf_growth_1y,
    )
    components = {
        "revenue_growth": _points(score_band(revenue_value, GROWTH_BANDS), WEIGHTS["revenue_growth"]),
        "profit_growth": _points(score_band(profit_value, GROWTH_BANDS), WEIGHTS["profit_growth"]),
        "fcf_growth": _points(score_band(fcf_value, GROWTH_BANDS), WEIGHTS["fcf_growth"]),
        "margins": _points(_average_bands(
            (metrics.operating_margin, MARGIN_BANDS),
            (metrics.fcf_margin, MARGIN_BANDS),
        ), WEIGHTS["margins"]),
        "profitability": _points(_average_bands(
            (metrics.roe, ROE_BANDS),
            (metrics.roa, ROA_BANDS),
        ), WEIGHTS["profitability"]),
        "debt": _points(score_band(metrics.debt_to_fcf, DEBT_TO_FCF_BANDS), WEIGHTS["debt"]),
        "cash": _points(score_band(metrics.cash_to_debt, CASH_TO_DEBT_BANDS), WEIGHTS["cash"]),
        "dilution": _dilution_points(metrics),
        "stability": _points(_stability_fraction(snapshots), WEIGHTS["stability"]),
    }
    available = sum(
        (WEIGHTS[name] for name, points in components.items() if points is not None),
        Decimal(0),
    )
    earned = sum((points for points in components.values() if points is not None), Decimal(0))
    quality = None
    if available > 0:
        quality = (earned / available * Decimal(100)).quantize(POINTS, rounding=ROUND_HALF_UP)
    return QualityScore(
        quality_score=quality,
        components=components,
        available_weight=available,
        growth_basis={
            "revenue_growth": revenue_basis,
            "profit_growth": profit_basis,
            "fcf_growth": fcf_basis,
        },
        anomalies=tuple(_anomalies(snapshots, metrics, components)),
    )


def _prefer_growth(*candidates: Decimal | None) -> tuple[Decimal | None, str | None]:
    labels = ("cagr_5y", "cagr_3y", "growth_1y")
    for label, value in zip(labels, candidates):
        if value is not None:
            return value, label
    return None, None


def _average_bands(*items: tuple[Decimal | None, tuple[tuple[Decimal, Decimal], ...]]) -> Decimal | None:
    scored = [score_band(value, bands) for value, bands in items]
    available = [score for score in scored if score is not None]
    if not available:
        return None
    return sum(available, Decimal(0)) / Decimal(len(available))


def _dilution_points(metrics: AnnualMetrics) -> Decimal | None:
    if not metrics.shares_comparable:
        return None
    value, _basis = _prefer_growth(
        metrics.shares_cagr_5y,
        metrics.shares_cagr_3y,
        metrics.shares_growth_1y,
    )
    return _points(score_band(value, DILUTION_BANDS), WEIGHTS["dilution"])


def _points(fraction: Decimal | None, weight: Decimal) -> Decimal | None:
    if fraction is None:
        return None
    return (fraction * weight).quantize(POINTS, rounding=ROUND_HALF_UP)


def _stability_fraction(snapshots: list[AnnualSnapshot]) -> Decimal | None:
    periods = sorted(snapshots, key=lambda row: row.fiscal_year)
    checks = [
        _revenue_growth_is_positive(periods),
        _margin_is_stable_or_higher(periods),
        _fcf_is_repeatedly_positive(periods),
        _debt_did_not_jump(periods),
    ]
    evaluated = [check for check in checks if check is not None]
    if not evaluated:
        return None
    passed = sum(1 for check in evaluated if check)
    return Decimal(passed) / Decimal(len(evaluated))


def _revenue_growth_is_positive(periods: list[AnnualSnapshot]) -> bool | None:
    pairs = _consecutive_pairs(periods[-4:])
    usable = [
        (left, right)
        for left, right in pairs
        if left.revenue is not None and right.revenue is not None and left.revenue > 0
    ]
    if len(usable) < 3:
        return None
    return all(right.revenue is not None and right.revenue > left.revenue for left, right in usable[-3:])


def _margin_is_stable_or_higher(periods: list[AnnualSnapshot]) -> bool | None:
    latest = _latest_with_margin(periods)
    if latest is None:
        return None
    prior = _margin_period(periods, latest.fiscal_year - 3) or _margin_period(periods, latest.fiscal_year - 1)
    if prior is None:
        return None
    return _operating_margin(latest) >= _operating_margin(prior)


def _fcf_is_repeatedly_positive(periods: list[AnnualSnapshot]) -> bool | None:
    observed = [row.free_cash_flow for row in periods[-5:] if row.free_cash_flow is not None]
    if len(observed) < 3:
        return None
    positive = sum(1 for value in observed if value > 0)
    required = 4 if len(observed) >= 5 else len(observed)
    return positive >= required


def _debt_did_not_jump(periods: list[AnnualSnapshot]) -> bool | None:
    pairs = _debt_pairs(periods[-4:])
    if not pairs:
        return None
    return all(not _debt_exploded(left, right) for left, right in pairs)


def _anomalies(
    snapshots: list[AnnualSnapshot],
    metrics: AnnualMetrics,
    components: dict[str, Decimal | None],
) -> list[str]:
    notes: list[str] = []
    for split in metrics.share_splits:
        notes.append(
            f"Probable {split.factor}-for-1 share split between FY{split.from_year} and "
            f"FY{split.to_year} (share count ratio {split.ratio}). "
            "The share series is not comparable, so dilution is excluded."
        )
    if metrics.fcf_cagr_5y is None and metrics.fcf_cagr_3y is not None:
        notes.append("FCF 5-year CAGR is unavailable. The FCF growth score uses the 3-year CAGR.")
    if metrics.revenue_cagr_5y is None and metrics.revenue_cagr_3y is not None:
        notes.append("Revenue 5-year CAGR is unavailable. The revenue growth score uses the 3-year CAGR.")
    if metrics.net_income_cagr_5y is None and metrics.net_income_cagr_3y is not None:
        notes.append("Net income 5-year CAGR is unavailable. The profit growth score uses the 3-year CAGR.")
    notes.extend(_debt_jump_notes(snapshots))
    excluded = [name for name, points in components.items() if points is None]
    if excluded:
        notes.append(
            "Excluded from the quality score and from its weight: " + ", ".join(excluded) + "."
        )
    return notes


def _debt_jump_notes(snapshots: list[AnnualSnapshot]) -> list[str]:
    notes: list[str] = []
    periods = sorted(snapshots, key=lambda row: row.fiscal_year)
    for left, right in _debt_pairs(periods):
        if not _debt_exploded(left, right):
            continue
        notes.append(
            f"Total debt jumped from {left.total_debt} in FY{left.fiscal_year} "
            f"to {right.total_debt} in FY{right.fiscal_year}. "
            "The stability check only looks at the latest three annual steps."
        )
    return notes


def _consecutive_pairs(periods: list[AnnualSnapshot]):
    return [
        (left, right)
        for left, right in zip(periods, periods[1:])
        if right.fiscal_year == left.fiscal_year + 1
    ]


def _debt_pairs(periods: list[AnnualSnapshot]):
    return [
        (left, right)
        for left, right in _consecutive_pairs(periods)
        if left.total_debt is not None and right.total_debt is not None
    ]


def _debt_exploded(left: AnnualSnapshot, right: AnnualSnapshot) -> bool:
    previous = left.total_debt
    current = right.total_debt
    if previous is None or current is None:
        return False
    if previous <= 0:
        return current > 0
    return current / previous > DEBT_JUMP


def _latest_with_margin(periods: list[AnnualSnapshot]) -> AnnualSnapshot | None:
    for period in reversed(periods):
        if _operating_margin(period) is not None:
            return period
    return None


def _margin_period(periods: list[AnnualSnapshot], fiscal_year: int) -> AnnualSnapshot | None:
    for period in periods:
        if period.fiscal_year == fiscal_year and _operating_margin(period) is not None:
            return period
    return None


def _operating_margin(period: AnnualSnapshot) -> Decimal | None:
    if period.revenue is None or period.revenue <= 0 or period.operating_income is None:
        return None
    return period.operating_income / period.revenue


def splits_as_dicts(splits: tuple[ShareSplit, ...]) -> list[dict[str, str | int]]:
    return [
        {
            "from_year": split.from_year,
            "to_year": split.to_year,
            "ratio": format(split.ratio, "f"),
            "factor": split.factor,
        }
        for split in splits
    ]
