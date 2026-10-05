"""Parse SEC companyfacts into one canonical row per fiscal period.

companyfacts tags every column of a filing with the filing's own ``fy`` and
``fp``. A 10-K therefore repeats prior years under the current fiscal year.
Those comparative columns are not new fiscal years.

The current period of a filing is the fact with the latest period end for the
anchor concept (revenue, otherwise net income) among facts of the right length.
Annual length is about one year. Quarterly length is about one quarter.
Year-to-date facts are ignored. The fiscal year stored by Sentinel is the
``fy`` of that current column. Later filings may restate the same period end;
the latest ``filed`` date wins, and a disagreement is reported.
"""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
import re

from app.services.sec.cik import cik_for_archive_path
from app.services.sec.free_cash_flow import compute_free_cash_flow
from app.services.sec.mappings import (
    ALLOWED_FORMS,
    ANNUAL_DAYS,
    ANNUAL_FORMS,
    ANCHOR_CONCEPTS,
    CONCEPT_NAMESPACES,
    CONCEPT_UNITS,
    DEBT_CONCEPTS,
    DURATION_CONCEPTS,
    DURATION_METRICS,
    INSTANT_CONCEPTS,
    INSTANT_METRICS,
    QUARTER_DAYS,
    QUARTERLY_FORMS,
    SEC_SOURCE,
    ConceptSpec,
)

Q4_FRAME = re.compile(r"CY\d{4}Q4$")
PERIOD_RANK = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 5}
MONEY_TOLERANCE = Decimal("1")


@dataclass(frozen=True)
class SourcedValue:
    """One retained fact: the number and the XBRL concept that supplied it."""

    value: Decimal
    concept: str


@dataclass(frozen=True)
class RawFact:
    concept: str
    value: Decimal
    start: date | None
    end: date
    filed: date
    form: str
    fp: str
    fy: int
    accession: str
    frame: str | None
    span: str


@dataclass(frozen=True)
class PeriodIdentity:
    fiscal_year: int
    fiscal_period: str
    period_end: date
    period_start: date | None
    span: str
    primary_accessions: frozenset[str]


@dataclass(frozen=True)
class ParsedPeriod:
    fiscal_year: int
    fiscal_period: str
    period_start: date | None
    period_end: date
    filed_at: date | None
    revenue: Decimal | None
    gross_profit: Decimal | None
    operating_income: Decimal | None
    net_income: Decimal | None
    eps_basic: Decimal | None
    eps_diluted: Decimal | None
    operating_cash_flow: Decimal | None
    capital_expenditure: Decimal | None
    free_cash_flow: Decimal | None
    cash_and_equivalents: Decimal | None
    total_assets: Decimal | None
    total_liabilities: Decimal | None
    total_debt: Decimal | None
    shareholders_equity: Decimal | None
    shares_outstanding: Decimal | None
    source: str
    source_url: str | None
    filing_type: str | None
    accession_number: str | None
    revenue_source_concept: str | None = None
    gross_profit_source_concept: str | None = None
    operating_income_source_concept: str | None = None
    net_income_source_concept: str | None = None
    eps_basic_source_concept: str | None = None
    eps_diluted_source_concept: str | None = None
    operating_cash_flow_source_concept: str | None = None
    capital_expenditure_source_concept: str | None = None
    cash_source_concept: str | None = None
    assets_source_concept: str | None = None
    liabilities_source_concept: str | None = None
    debt_source_concept: str | None = None
    equity_source_concept: str | None = None
    shares_source_concept: str | None = None


@dataclass(frozen=True)
class ParseResult:
    periods: tuple[ParsedPeriod, ...]
    warnings: tuple[str, ...]


@dataclass
class _Selection:
    fact: RawFact | None = None
    concept: str | None = None
    conflicts: list[str] | None = None


@dataclass
class _Built:
    period: ParsedPeriod | None
    conflicts: list[str]
    fallbacks: list[str]
    capex_fallback: int
    debt_inclusive: int


def parse_company_facts(payload: dict, *, cik: str) -> ParseResult:
    facts = _collect_facts(payload)
    catalog, calendar_warnings = _apply_fiscal_calendar(_build_catalog(facts), facts)
    periods: list[ParsedPeriod] = []
    conflicts: list[str] = []
    fallbacks: list[str] = []
    capex_fallback = 0
    debt_inclusive = 0

    for identity in sorted(catalog.values(), key=_identity_key):
        built = _build_period(facts, identity, cik)
        if built.period is not None:
            periods.append(built.period)
        conflicts.extend(built.conflicts)
        fallbacks.extend(built.fallbacks)
        capex_fallback += built.capex_fallback
        debt_inclusive += built.debt_inclusive

    warnings = _summarize_warnings(conflicts, fallbacks, capex_fallback, debt_inclusive)
    warnings.extend(calendar_warnings)
    warnings.extend(_series_warnings(periods))
    warnings.extend(_unit_warnings(payload))
    return ParseResult(tuple(periods), tuple(warnings))


def filing_index_url(cik: str, accession: str) -> str:
    cik_path = cik_for_archive_path(cik)
    compact = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_path}/{compact}/{accession}-index.html"
    )


def _collect_facts(payload: dict) -> list[RawFact]:
    facts_root = payload.get("facts")
    if not isinstance(facts_root, dict):
        return []

    collected: list[RawFact] = []
    for concept in (*DURATION_CONCEPTS, *INSTANT_CONCEPTS):
        for namespace in CONCEPT_NAMESPACES.get(concept, ("us-gaap",)):
            body = (facts_root.get(namespace) or {}).get(concept)
            if not isinstance(body, dict):
                continue
            units = body.get("units")
            if not isinstance(units, dict):
                continue
            for item in units.get(CONCEPT_UNITS[concept], []):
                fact = _parse_fact(concept, item)
                if fact is not None:
                    collected.append(fact)
    return collected


def _parse_fact(concept: str, item: object) -> RawFact | None:
    if not isinstance(item, dict):
        return None
    form = item.get("form")
    fp = item.get("fp")
    accession = item.get("accn")
    if form not in ALLOWED_FORMS or fp not in PERIOD_RANK or not accession:
        return None
    if "val" not in item or "end" not in item or "filed" not in item or "fy" not in item:
        return None

    end = _parse_date(item.get("end"))
    filed = _parse_date(item.get("filed"))
    if end is None or filed is None:
        return None

    start = _parse_date(item.get("start")) if item.get("start") else None
    span = _span_for(concept, start, end)
    if span is None:
        return None

    try:
        fiscal_year = int(item["fy"])
        value = _to_decimal(item["val"])
    except (TypeError, ValueError, ArithmeticError):
        return None

    frame = item.get("frame") or None
    return RawFact(
        concept=concept,
        value=value,
        start=start,
        end=end,
        filed=filed,
        form=str(form),
        fp=str(fp),
        fy=fiscal_year,
        accession=str(accession),
        frame=str(frame) if frame else None,
        span=span,
    )


def _span_for(concept: str, start: date | None, end: date) -> str | None:
    if concept in INSTANT_CONCEPTS:
        if start is not None:
            return None
        return "instant"
    if start is None:
        return None
    days = (end - start).days
    if ANNUAL_DAYS[0] <= days <= ANNUAL_DAYS[1]:
        return "year"
    if QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]:
        return "quarter"
    return None


def _build_catalog(facts: list[RawFact]) -> dict[tuple[date, str], PeriodIdentity]:
    by_accession: dict[str, list[RawFact]] = defaultdict(list)
    for fact in facts:
        by_accession[fact.accession].append(fact)

    catalog: dict[tuple[date, str], PeriodIdentity] = {}
    for group in by_accession.values():
        _register_span(catalog, group, span="year", periods={"FY"}, forms=ANNUAL_FORMS)
        _register_span(
            catalog,
            group,
            span="quarter",
            periods={"Q1", "Q2", "Q3", "Q4"},
            forms=QUARTERLY_FORMS,
        )
        _register_q4_frames(catalog, group)
    return catalog


def _register_span(
    catalog: dict[tuple[date, str], PeriodIdentity],
    group: list[RawFact],
    *,
    span: str,
    periods: set[str],
    forms: frozenset[str],
) -> None:
    subset = [
        fact
        for fact in group
        if fact.span == span and fact.fp in periods and fact.form in forms
    ]
    if not subset:
        return
    primary_end = _anchor_end(subset)
    current = [fact for fact in subset if fact.end == primary_end]
    if not current:
        return
    anchored = [fact for fact in current if fact.concept in ANCHOR_CONCEPTS] or current
    anchor = _prefer(anchored)
    identity = PeriodIdentity(
        fiscal_year=anchor.fy,
        fiscal_period="FY" if span == "year" else anchor.fp,
        period_end=primary_end,
        period_start=anchor.start,
        span=span,
        primary_accessions=frozenset({anchor.accession}),
    )
    _put_identity(catalog, identity)


def _register_q4_frames(
    catalog: dict[tuple[date, str], PeriodIdentity],
    group: list[RawFact],
) -> None:
    """Older 10-K files expose a single-quarter Q4 only through a CY####Q4 frame.

    The fact is tagged ``fp=FY`` because it sits in the annual filing.
    Recent NVIDIA filings do not include this frame, so Q4 stays absent
    instead of being invented from full-year minus year-to-date figures.
    """
    subset = [
        fact
        for fact in group
        if fact.span == "quarter"
        and fact.fp == "FY"
        and fact.form in ANNUAL_FORMS
        and fact.frame
        and Q4_FRAME.search(fact.frame)
    ]
    by_end: dict[date, list[RawFact]] = defaultdict(list)
    for fact in subset:
        by_end[fact.end].append(fact)
    for end, facts_at_end in by_end.items():
        anchor = _prefer(facts_at_end)
        _put_identity(
            catalog,
            PeriodIdentity(
                fiscal_year=anchor.fy,
                fiscal_period="Q4",
                period_end=end,
                period_start=anchor.start,
                span="quarter",
                primary_accessions=frozenset({anchor.accession}),
            ),
        )


def _put_identity(
    catalog: dict[tuple[date, str], PeriodIdentity],
    identity: PeriodIdentity,
) -> None:
    key = (identity.period_end, identity.span)
    current = catalog.get(key)
    if current is None:
        catalog[key] = identity
        return
    if (current.fiscal_year, current.fiscal_period) != (
        identity.fiscal_year,
        identity.fiscal_period,
    ):
        return
    catalog[key] = replace(
        current,
        primary_accessions=current.primary_accessions | identity.primary_accessions,
    )


def _apply_fiscal_calendar(
    catalog: dict[tuple[date, str], PeriodIdentity],
    facts: list[RawFact],
) -> tuple[dict[tuple[date, str], PeriodIdentity], list[str]]:
    """Relabel periods from the observed fiscal year-end month.

    In companyfacts, ``fy`` and ``fp`` describe the filing, so a comparative
    column and a later quarter can share one label. Annual period ends reveal
    the year-end month (January for NVIDIA). Each period end is then placed
    in that calendar. Labels that still collide keep the later period end.
    """
    year_end_month = _year_end_month(facts)
    if year_end_month is None:
        return catalog, []

    relabeled: dict[tuple[int, str], PeriodIdentity] = {}
    mismatches: list[str] = []
    collisions: list[str] = []
    for identity in catalog.values():
        fiscal_year = _fiscal_year(identity.period_end, year_end_month)
        fiscal_period = (
            "FY" if identity.span == "year" else _quarter(identity.period_end, year_end_month)
        )
        updated = replace(
            identity,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
        )
        if (identity.fiscal_year, identity.fiscal_period) != (fiscal_year, fiscal_period):
            mismatches.append(
                f"SEC {identity.fiscal_year} {identity.fiscal_period} end {identity.period_end} "
                f"relabeled {fiscal_year} {fiscal_period}"
            )
        key = (fiscal_year, fiscal_period)
        current = relabeled.get(key)
        if current is None:
            relabeled[key] = updated
            continue
        kept, dropped = (
            (updated, current) if updated.period_end > current.period_end else (current, updated)
        )
        relabeled[key] = replace(
            kept,
            primary_accessions=kept.primary_accessions | dropped.primary_accessions,
        )
        gap = abs((kept.period_end - dropped.period_end).days)
        if gap > 45:
            collisions.append(
                f"{fiscal_year} {fiscal_period}: kept period end {kept.period_end} "
                f"and dropped {dropped.period_end}"
            )

    warnings = [
        "Fiscal year-end month inferred from annual period ends: "
        f"{year_end_month}. Periods are labeled from that calendar when the SEC "
        "fy/fp tag refers to the filing rather than the column."
    ]
    if mismatches:
        preview = "; ".join(mismatches[:4])
        extra = len(mismatches) - min(len(mismatches), 4)
        suffix = f"; {extra} more relabels" if extra else ""
        warnings.append(f"Relabeled {len(mismatches)} SEC period tags. Examples: {preview}{suffix}")
    warnings.extend(collisions[:4])
    keyed = {(identity.period_end, identity.span): identity for identity in relabeled.values()}
    return keyed, warnings


def _year_end_month(facts: list[RawFact]) -> int | None:
    ends = [
        fact.end
        for fact in facts
        if fact.span == "year" and fact.form in ANNUAL_FORMS and fact.fp == "FY"
    ]
    if not ends:
        return None
    counts: dict[int, int] = defaultdict(int)
    for period_end in ends:
        counts[period_end.month] += 1
    return max(counts, key=lambda month: (counts[month], month))


def _fiscal_year(period_end: date, year_end_month: int) -> int:
    if period_end.month <= year_end_month:
        return period_end.year
    return period_end.year + 1


def _quarter(period_end: date, year_end_month: int) -> str:
    targets = {
        "Q1": ((year_end_month - 9 - 1) % 12) + 1,
        "Q2": ((year_end_month - 6 - 1) % 12) + 1,
        "Q3": ((year_end_month - 3 - 1) % 12) + 1,
        "Q4": year_end_month,
    }

    def distance(month: int, target: int) -> int:
        diff = abs(month - target)
        return min(diff, 12 - diff)

    return min(targets, key=lambda name: (distance(period_end.month, targets[name]), name))


def _anchor_end(facts: list[RawFact]) -> date:
    for concept in ANCHOR_CONCEPTS:
        matching = [fact for fact in facts if fact.concept == concept]
        if matching:
            return max(fact.end for fact in matching)
    return max(fact.end for fact in facts)


def _build_period(facts: list[RawFact], identity: PeriodIdentity, cik: str) -> _Built:
    selected: dict[str, RawFact] = {}
    conflicts: list[str] = []
    fallbacks: list[str] = []
    capex_fallback = 0

    for spec in (*DURATION_METRICS, *INSTANT_METRICS):
        selection = _select_concept(facts, spec, identity)
        if selection.fact is None or selection.concept is None:
            conflicts.extend(selection.conflicts or [])
            continue
        selected[spec.field] = selection.fact
        if spec.field == "capital_expenditure" and selection.concept == (
            "PaymentsToAcquireProductiveAssets"
        ):
            capex_fallback = 1
        elif selection.concept != spec.concepts[0]:
            fallbacks.append(
                f"{identity.fiscal_year} {identity.fiscal_period} {spec.field}: "
                f"used {selection.concept} because {spec.concepts[0]} had no fact"
            )
        conflicts.extend(selection.conflicts or [])

    _note_cover_page_shares(facts, identity, selected, conflicts, fallbacks)

    total_debt, debt_fact, debt_concept, debt_conflicts, debt_inclusive = _resolve_debt(
        facts, identity
    )
    conflicts.extend(debt_conflicts)
    if debt_fact is not None:
        selected["total_debt"] = debt_fact

    if not selected and total_debt is None:
        return _Built(None, conflicts, fallbacks, capex_fallback, debt_inclusive)

    anchor = _anchor_fact(selected)
    operating_cash_flow = _value(selected, "operating_cash_flow")
    capital_expenditure = _value(selected, "capital_expenditure")
    period = ParsedPeriod(
        fiscal_year=identity.fiscal_year,
        fiscal_period=identity.fiscal_period,
        period_start=_value_start(selected, identity),
        period_end=identity.period_end,
        filed_at=anchor.filed if anchor else None,
        revenue=_value(selected, "revenue"),
        gross_profit=_value(selected, "gross_profit"),
        operating_income=_value(selected, "operating_income"),
        net_income=_value(selected, "net_income"),
        eps_basic=_value(selected, "eps_basic"),
        eps_diluted=_value(selected, "eps_diluted"),
        operating_cash_flow=operating_cash_flow,
        capital_expenditure=capital_expenditure,
        free_cash_flow=compute_free_cash_flow(operating_cash_flow, capital_expenditure),
        cash_and_equivalents=_value(selected, "cash_and_equivalents"),
        total_assets=_value(selected, "total_assets"),
        total_liabilities=_value(selected, "total_liabilities"),
        total_debt=total_debt,
        shareholders_equity=_value(selected, "shareholders_equity"),
        shares_outstanding=_value(selected, "shares_outstanding"),
        **_source_concepts(selected, debt_concept),
        source=SEC_SOURCE,
        source_url=filing_index_url(cik, anchor.accession) if anchor else None,
        filing_type=anchor.form if anchor else None,
        accession_number=anchor.accession if anchor else None,
    )
    return _Built(period, conflicts, fallbacks, capex_fallback, debt_inclusive)


def _select_concept(
    facts: list[RawFact],
    spec: ConceptSpec,
    identity: PeriodIdentity,
) -> _Selection:
    matched: list[tuple[str, RawFact, bool]] = []
    for concept in spec.concepts:
        candidates = _candidates(facts, concept, identity, spec.kind)
        if not candidates:
            continue
        chosen, disagreed = _choose(candidates)
        matched.append((concept, chosen, disagreed))

    if not matched:
        return _Selection()

    concept, fact, disagreed = matched[0]
    conflicts: list[str] = []
    label = f"{identity.fiscal_year} {identity.fiscal_period} {spec.field}"
    if disagreed:
        values = sorted({item.value for item in _candidates(facts, concept, identity, spec.kind)})
        conflicts.append(
            f"{label}: kept {concept}={fact.value} from {fact.accession} filed {fact.filed}; "
            f"other filings reported {values}"
        )
    if len(matched) > 1 and matched[1][1].value != fact.value:
        other_concept, other_fact, _other_disagreed = matched[1]
        conflicts.append(
            f"{label}: kept {concept}={fact.value} and ignored {other_concept}={other_fact.value}"
        )
    return _Selection(fact=fact, concept=concept, conflicts=conflicts)


def _candidates(
    facts: list[RawFact],
    concept: str,
    identity: PeriodIdentity,
    kind: str,
) -> list[RawFact]:
    span = "instant" if kind == "instant" else identity.span
    return [
        fact
        for fact in facts
        if fact.concept == concept
        and fact.span == span
        and fact.end == identity.period_end
        and _form_matches(fact, identity)
    ]


def _form_matches(fact: RawFact, identity: PeriodIdentity) -> bool:
    if identity.fiscal_period == "FY":
        return fact.form in ANNUAL_FORMS
    if identity.fiscal_period == "Q4" and fact.form in ANNUAL_FORMS:
        return True
    return fact.form in QUARTERLY_FORMS


def _resolve_debt(
    facts: list[RawFact],
    identity: PeriodIdentity,
) -> tuple[Decimal | None, RawFact | None, str | None, list[str], int]:
    chosen = {
        concept: _choose(candidates)[0]
        for concept in DEBT_CONCEPTS
        if (candidates := _candidates(facts, concept, identity, "instant"))
    }
    label = f"{identity.fiscal_year} {identity.fiscal_period} total_debt"
    comprehensive = chosen.get(
        "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"
    )
    if comprehensive is not None:
        return comprehensive.value, comprehensive, comprehensive.concept, [], 0

    long_term = chosen.get("LongTermDebt")
    noncurrent = chosen.get("LongTermDebtNoncurrent")
    current = chosen.get("LongTermDebtCurrent") or chosen.get("DebtCurrent")

    if long_term is not None and noncurrent is not None and current is not None:
        parts = noncurrent.value + current.value
        if abs(long_term.value - parts) <= MONEY_TOLERANCE:
            return long_term.value, long_term, long_term.concept, [], 1
        if abs(long_term.value - noncurrent.value) <= MONEY_TOLERANCE:
            return (
                long_term.value + current.value,
                long_term,
                _joined_concepts(long_term, current),
                [
                    f"{label}: summed LongTermDebt and the current portion because "
                    f"LongTermDebt={long_term.value} matched the noncurrent portion only"
                ],
                0,
            )
        return (
            long_term.value,
            long_term,
            f"{long_term.concept}:ambiguous",
            [
                f"{label}: ambiguous tags LongTermDebt={long_term.value}, "
                f"noncurrent={noncurrent.value}, current={current.value}; "
                "kept LongTermDebt and did not add the current portion"
            ],
            0,
        )

    if long_term is not None:
        return long_term.value, long_term, long_term.concept, [], 0
    if noncurrent is not None and current is not None:
        return (
            noncurrent.value + current.value,
            noncurrent,
            _joined_concepts(noncurrent, current),
            [f"{label}: summed noncurrent and current portions because LongTermDebt was absent"],
            0,
        )
    if noncurrent is not None:
        return (
            noncurrent.value,
            noncurrent,
            noncurrent.concept,
            [f"{label}: used LongTermDebtNoncurrent only; the current portion was absent"],
            0,
        )
    return None, None, None, [], 0


def _joined_concepts(*facts: RawFact) -> str:
    return "+".join(fact.concept for fact in facts)


def _choose(candidates: list[RawFact]) -> tuple[RawFact, bool]:
    chosen = _prefer(candidates)
    disagreed = len({fact.value for fact in candidates}) > 1
    return chosen, disagreed


def _prefer(facts: list[RawFact]) -> RawFact:
    return max(facts, key=lambda fact: (fact.filed, bool(fact.frame), fact.accession))


def _anchor_fact(selected: dict[str, RawFact]) -> RawFact | None:
    for field in ("revenue", "net_income", "operating_cash_flow", "total_assets", "total_debt"):
        fact = selected.get(field)
        if fact is not None:
            return fact
    return next(iter(selected.values()), None)


_SOURCE_CONCEPT_FIELDS = {
    "revenue": "revenue_source_concept",
    "gross_profit": "gross_profit_source_concept",
    "operating_income": "operating_income_source_concept",
    "net_income": "net_income_source_concept",
    "eps_basic": "eps_basic_source_concept",
    "eps_diluted": "eps_diluted_source_concept",
    "operating_cash_flow": "operating_cash_flow_source_concept",
    "capital_expenditure": "capital_expenditure_source_concept",
    "cash_and_equivalents": "cash_source_concept",
    "total_assets": "assets_source_concept",
    "total_liabilities": "liabilities_source_concept",
    "shareholders_equity": "equity_source_concept",
    "shares_outstanding": "shares_source_concept",
}


def _source_concepts(selected: dict[str, RawFact], debt_concept: str | None) -> dict[str, str | None]:
    concepts = {
        column: fact.concept if (fact := selected.get(field)) is not None else None
        for field, column in _SOURCE_CONCEPT_FIELDS.items()
    }
    concepts["debt_source_concept"] = debt_concept
    return concepts


def sourced_value(period: ParsedPeriod, field: str) -> SourcedValue | None:
    """Return the retained value and the XBRL concept label stored for that field."""
    value = getattr(period, field)
    if field == "total_debt":
        concept = period.debt_source_concept
    else:
        column = _SOURCE_CONCEPT_FIELDS.get(field)
        concept = getattr(period, column) if column else None
    if value is None or not concept:
        return None
    return SourcedValue(value=value, concept=concept)


def _value(selected: dict[str, RawFact], field: str) -> Decimal | None:
    fact = selected.get(field)
    if fact is None:
        return None
    return fact.value


def _value_start(selected: dict[str, RawFact], identity: PeriodIdentity) -> date | None:
    for field in ("revenue", "net_income", "operating_cash_flow", "gross_profit"):
        fact = selected.get(field)
        if fact is not None and fact.start is not None:
            return fact.start
    return identity.period_start


def _note_cover_page_shares(
    facts: list[RawFact],
    identity: PeriodIdentity,
    selected: dict[str, RawFact],
    conflicts: list[str],
    fallbacks: list[str],
) -> None:
    cover_facts = [
        fact
        for fact in facts
        if fact.concept == "EntityCommonStockSharesOutstanding"
        and fact.accession in identity.primary_accessions
    ]
    if not cover_facts:
        return
    cover = _prefer(cover_facts)
    kept = selected.get("shares_outstanding")
    if kept is None:
        selected["shares_outstanding"] = cover
        fallbacks.append(
            f"{identity.fiscal_year} {identity.fiscal_period} shares_outstanding: "
            "used EntityCommonStockSharesOutstanding because "
            "CommonStockSharesOutstanding had no fact on the period end"
        )
        return
    if kept.concept == "EntityCommonStockSharesOutstanding" or kept.value == 0:
        return
    if abs(cover.value - kept.value) / abs(kept.value) <= Decimal("0.01"):
        return
    conflicts.append(
        f"{identity.fiscal_year} {identity.fiscal_period} shares_outstanding: "
        f"kept {kept.concept}={kept.value} at {kept.end} and ignored "
        f"EntityCommonStockSharesOutstanding={cover.value} as of {cover.end}"
    )


def _summarize_warnings(
    conflicts: list[str],
    fallbacks: list[str],
    capex_fallback: int,
    debt_inclusive: int,
) -> list[str]:
    warnings: list[str] = []
    if capex_fallback:
        warnings.append(
            "capital_expenditure: PaymentsToAcquirePropertyPlantAndEquipment and "
            "PaymentsForAdditionsToPropertyPlantAndEquipment had no fact; "
            f"used PaymentsToAcquireProductiveAssets for {capex_fallback} periods. "
            "That concept can include intangible assets as well as PP&E."
        )
    if debt_inclusive:
        warnings.append(
            "total_debt: LongTermDebt already included the current portion "
            "(it matched LongTermDebtNoncurrent + current) "
            f"for {debt_inclusive} periods; the current portion was not added again. "
            "Operating lease liabilities are not included."
        )
    warnings.extend(_cap_conflicts(fallbacks, "fallback"))
    share_conflicts = sorted(
        (item for item in conflicts if " shares_outstanding:" in item),
        reverse=True,
    )
    other_conflicts = [item for item in conflicts if item not in share_conflicts]
    warnings.extend(_cap_conflicts(share_conflicts, "shares_outstanding"))
    warnings.extend(_cap_conflicts(other_conflicts, "metric"))
    return warnings


def _cap_conflicts(conflicts: list[str], label: str) -> list[str]:
    if not conflicts:
        return []
    shown = conflicts[:8]
    hidden = len(conflicts) - len(shown)
    if hidden:
        shown.append(f"{hidden} additional {label} disagreements were logged with the sync")
    return shown


def _series_warnings(periods: list[ParsedPeriod]) -> list[str]:
    warnings: list[str] = []
    quarterly_without_cash_flow = [
        period
        for period in periods
        if period.fiscal_period in {"Q2", "Q3"} and period.operating_cash_flow is None
    ]
    if quarterly_without_cash_flow:
        warnings.append(
            "operating_cash_flow: "
            f"{len(quarterly_without_cash_flow)} Q2/Q3 periods have no single-quarter fact. "
            "Year-to-date cash flow was not stored as a quarterly amount."
        )
    jumps: list[str] = []
    grouped: dict[str, list[ParsedPeriod]] = defaultdict(list)
    for period in periods:
        if period.shares_outstanding is not None:
            grouped[period.fiscal_period].append(period)
    for group in grouped.values():
        ordered = sorted(group, key=lambda period: period.period_end)
        for previous, period in zip(ordered, ordered[1:]):
            before = previous.shares_outstanding
            after = period.shares_outstanding
            if before is None or after is None or before == 0 or after == 0:
                continue
            ratio = max(before, after) / min(before, after)
            if ratio >= 5:
                jumps.append(
                    f"{previous.fiscal_year} {previous.fiscal_period} {previous.shares_outstanding} -> "
                    f"{period.fiscal_year} {period.fiscal_period} {period.shares_outstanding}"
                )
    if jumps:
        preview = "; ".join(jumps[:4])
        warnings.append(
            "shares_outstanding: the series jumps by 5x or more between consecutive periods "
            f"({preview}). A stock split was not restated on every period, so share counts "
            "are not comparable across the jump."
        )
    return warnings


def _unit_warnings(payload: dict) -> list[str]:
    facts_root = payload.get("facts")
    if not isinstance(facts_root, dict):
        return []
    warnings: list[str] = []
    for concept, unit in CONCEPT_UNITS.items():
        for namespace in CONCEPT_NAMESPACES.get(concept, ("us-gaap",)):
            body = (facts_root.get(namespace) or {}).get(concept)
            if not isinstance(body, dict):
                continue
            units = body.get("units")
            if isinstance(units, dict) and units and unit not in units:
                warnings.append(
                    f"{concept} is present under {sorted(units)} but Sentinel reads {unit}"
                )
    return warnings


def _identity_key(identity: PeriodIdentity) -> tuple[int, int, date]:
    return (
        identity.fiscal_year,
        PERIOD_RANK.get(identity.fiscal_period, 0),
        identity.period_end,
    )


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError("metric value must be numeric")
    return Decimal(str(value))
