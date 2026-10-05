"""Annual derived metrics. Only FY snapshots should be passed in.

CAGR is the classic compound rate:

    (final / initial) ** (1 / years) - 1

If the initial value or the final value is not strictly positive, the rate is
null. A loss turning into a profit is not reported as a positive CAGR, and a
positive series falling through zero is not forced into a number either.
One-year growth uses the same rule on the initial value: a non-positive base
is null. A positive base falling to a negative result stays a negative growth
rate, because that decline is well defined.
"""

from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.services.analysis.thresholds import SPLIT_FACTORS, SPLIT_TOLERANCE

RATIO_PLACES = Decimal("0.00000001")


@dataclass(frozen=True)
class AnnualSnapshot:
    fiscal_year: int
    revenue: Decimal | None = None
    gross_profit: Decimal | None = None
    operating_income: Decimal | None = None
    net_income: Decimal | None = None
    free_cash_flow: Decimal | None = None
    cash_and_equivalents: Decimal | None = None
    total_assets: Decimal | None = None
    total_debt: Decimal | None = None
    shareholders_equity: Decimal | None = None
    shares_outstanding: Decimal | None = None
    revenue_source_concept: str | None = None
    net_income_source_concept: str | None = None
    operating_cash_flow_source_concept: str | None = None
    capital_expenditure_source_concept: str | None = None
    debt_source_concept: str | None = None
    shares_source_concept: str | None = None


@dataclass(frozen=True)
class ShareSplit:
    from_year: int
    to_year: int
    ratio: Decimal
    factor: int


@dataclass(frozen=True)
class AnnualMetrics:
    latest_fiscal_year: int | None
    revenue_growth_1y: Decimal | None
    revenue_cagr_3y: Decimal | None
    revenue_cagr_5y: Decimal | None
    net_income_growth_1y: Decimal | None
    net_income_cagr_3y: Decimal | None
    net_income_cagr_5y: Decimal | None
    fcf_growth_1y: Decimal | None
    fcf_cagr_3y: Decimal | None
    fcf_cagr_5y: Decimal | None
    gross_margin: Decimal | None
    operating_margin: Decimal | None
    net_margin: Decimal | None
    fcf_margin: Decimal | None
    debt_to_equity: Decimal | None
    debt_to_fcf: Decimal | None
    cash_to_debt: Decimal | None
    roe: Decimal | None
    roa: Decimal | None
    shares_growth_1y: Decimal | None
    shares_cagr_3y: Decimal | None
    shares_cagr_5y: Decimal | None
    shares_comparable: bool
    share_splits: tuple[ShareSplit, ...]


def cagr(initial: Decimal | None, final: Decimal | None, years: int) -> Decimal | None:
    """Compound annual growth. Null when either endpoint is not positive."""
    if years <= 0 or initial is None or final is None:
        return None
    if initial <= 0 or final <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        rate = (final / initial) ** (Decimal(1) / Decimal(years)) - Decimal(1)
    return rate.quantize(RATIO_PLACES)


def growth_rate(initial: Decimal | None, final: Decimal | None) -> Decimal | None:
    """One-year change. A non-positive starting value is not a usable base."""
    if initial is None or final is None or initial <= 0:
        return None
    return ((final - initial) / initial).quantize(RATIO_PLACES)


def ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    """Quotient. A missing or non-positive denominator is null, including zero."""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return (numerator / denominator).quantize(RATIO_PLACES)


def build_annual_metrics(snapshots: list[AnnualSnapshot]) -> AnnualMetrics:
    periods = _dedupe_years(snapshots)
    if not periods:
        return _empty_metrics()
    by_year = {period.fiscal_year: period for period in periods}
    latest = periods[-1]
    splits = _detect_splits(periods)
    comparable = not splits
    shares_1y, shares_3y, shares_5y = _share_growth(by_year, latest.fiscal_year, comparable)
    return AnnualMetrics(
        latest_fiscal_year=latest.fiscal_year,
        revenue_growth_1y=_change(by_year, latest.fiscal_year, 1, "revenue", growth_rate),
        revenue_cagr_3y=_change(by_year, latest.fiscal_year, 3, "revenue", cagr),
        revenue_cagr_5y=_change(by_year, latest.fiscal_year, 5, "revenue", cagr),
        net_income_growth_1y=_change(by_year, latest.fiscal_year, 1, "net_income", growth_rate),
        net_income_cagr_3y=_change(by_year, latest.fiscal_year, 3, "net_income", cagr),
        net_income_cagr_5y=_change(by_year, latest.fiscal_year, 5, "net_income", cagr),
        fcf_growth_1y=_change(by_year, latest.fiscal_year, 1, "free_cash_flow", growth_rate),
        fcf_cagr_3y=_change(by_year, latest.fiscal_year, 3, "free_cash_flow", cagr),
        fcf_cagr_5y=_change(by_year, latest.fiscal_year, 5, "free_cash_flow", cagr),
        gross_margin=ratio(latest.gross_profit, latest.revenue),
        operating_margin=ratio(latest.operating_income, latest.revenue),
        net_margin=ratio(latest.net_income, latest.revenue),
        fcf_margin=ratio(latest.free_cash_flow, latest.revenue),
        debt_to_equity=ratio(latest.total_debt, latest.shareholders_equity),
        debt_to_fcf=ratio(latest.total_debt, latest.free_cash_flow),
        cash_to_debt=ratio(latest.cash_and_equivalents, latest.total_debt),
        roe=ratio(latest.net_income, latest.shareholders_equity),
        roa=ratio(latest.net_income, latest.total_assets),
        shares_growth_1y=shares_1y,
        shares_cagr_3y=shares_3y,
        shares_cagr_5y=shares_5y,
        shares_comparable=comparable,
        share_splits=tuple(splits),
    )


def _dedupe_years(snapshots: list[AnnualSnapshot]) -> list[AnnualSnapshot]:
    ordered = sorted(snapshots, key=lambda period: period.fiscal_year)
    by_year: dict[int, AnnualSnapshot] = {}
    for period in ordered:
        by_year[period.fiscal_year] = period
    return [by_year[year] for year in sorted(by_year)]


def _change(by_year, latest_year: int, years: int, field: str, calculator):
    current = by_year.get(latest_year)
    previous = by_year.get(latest_year - years)
    if current is None or previous is None:
        return None
    initial = getattr(previous, field)
    final = getattr(current, field)
    if years == 1 and calculator is growth_rate:
        return calculator(initial, final)
    if calculator is cagr:
        return calculator(initial, final, years)
    return calculator(initial, final)


def _share_growth(by_year, latest_year: int, comparable: bool):
    if not comparable:
        return None, None, None
    return (
        _change(by_year, latest_year, 1, "shares_outstanding", growth_rate),
        _change(by_year, latest_year, 3, "shares_outstanding", cagr),
        _change(by_year, latest_year, 5, "shares_outstanding", cagr),
    )


def _detect_splits(periods: list[AnnualSnapshot]) -> list[ShareSplit]:
    found: list[ShareSplit] = []
    for previous, current in zip(periods, periods[1:]):
        if current.fiscal_year != previous.fiscal_year + 1:
            continue
        if (
            previous.shares_outstanding is None
            or current.shares_outstanding is None
            or previous.shares_outstanding <= 0
            or current.shares_outstanding <= 0
        ):
            continue
        later = current.shares_outstanding
        earlier = previous.shares_outstanding
        observed = later / earlier if later >= earlier else earlier / later
        factor = _split_factor(observed)
        if factor is None:
            continue
        found.append(
            ShareSplit(
                from_year=previous.fiscal_year,
                to_year=current.fiscal_year,
                ratio=(later / earlier).quantize(RATIO_PLACES),
                factor=factor,
            )
        )
    return found


def _split_factor(ratio: Decimal) -> int | None:
    if ratio < Decimal("1.8"):
        return None
    for factor in SPLIT_FACTORS:
        target = Decimal(factor)
        if abs(ratio - target) / target <= SPLIT_TOLERANCE:
            return factor
    return None


def _empty_metrics() -> AnnualMetrics:
    return AnnualMetrics(
        latest_fiscal_year=None,
        revenue_growth_1y=None,
        revenue_cagr_3y=None,
        revenue_cagr_5y=None,
        net_income_growth_1y=None,
        net_income_cagr_3y=None,
        net_income_cagr_5y=None,
        fcf_growth_1y=None,
        fcf_cagr_3y=None,
        fcf_cagr_5y=None,
        gross_margin=None,
        operating_margin=None,
        net_margin=None,
        fcf_margin=None,
        debt_to_equity=None,
        debt_to_fcf=None,
        cash_to_debt=None,
        roe=None,
        roa=None,
        shares_growth_1y=None,
        shares_cagr_3y=None,
        shares_cagr_5y=None,
        shares_comparable=True,
        share_splits=(),
    )
