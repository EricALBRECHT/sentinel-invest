"""Opportunity Score V1.

    opportunity_score = earned_points / available_weight * 100

A null component is excluded. It is not scored as zero. Size runway is also
excluded when it is the only scored component, so market cap alone cannot
produce a high score. Quality is not an input.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.services.analysis.annual_metrics import AnnualMetrics, AnnualSnapshot
from app.services.analysis.growth_runway import growth_runway_score, rd_intensity_score
from app.services.analysis.opportunity_thresholds import METHOD_VERSION, SOURCE_CONFIDENCE, WEIGHTS
from app.services.analysis.size_runway import size_runway_score

POINTS = Decimal("0.01")


@dataclass(frozen=True)
class MegatrendInput:
    trend: str
    exposure_score: Decimal
    confidence: int
    evidence: str | None = None
    source: str = "MANUAL_STRUCTURED"


@dataclass(frozen=True)
class CustomerInput:
    name: str
    revenue_share: Decimal | None = None


@dataclass(frozen=True)
class OpportunityInputs:
    snapshots: list[AnnualSnapshot]
    metrics: AnnualMetrics
    market_cap: Decimal | None = None
    market_growth_score: Decimal | None = None
    market_size_score: Decimal | None = None
    market_penetration_score: Decimal | None = None
    strategic_position_score: Decimal | None = None
    supplier_leverage_score: Decimal | None = None
    customer_diversification_score: Decimal | None = None
    bottleneck_score: Decimal | None = None
    innovation_score: Decimal | None = None
    rd_intensity_score: Decimal | None = None
    research_and_development: Decimal | None = None
    capacity_expansion_score: Decimal | None = None
    geographic_expansion_score: Decimal | None = None
    competitive_moat_score: Decimal | None = None
    competition_risk_score: Decimal | None = None
    megatrends: tuple[MegatrendInput, ...] = ()
    strategic_roles: tuple[str, ...] = ()
    customers: tuple[CustomerInput, ...] = ()
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpportunityComponent:
    score: Decimal | None
    weight: Decimal
    points: Decimal | None
    confidence: int | None
    source: str | None
    evidence: str
    included: bool


@dataclass(frozen=True)
class OpportunityAssessment:
    opportunity_score: Decimal | None
    opportunity_confidence_score: int
    coverage_score: int
    coverage_status: str
    ranking_eligible: bool
    components: dict[str, OpportunityComponent]
    available_weight: Decimal
    excluded: tuple[str, ...]
    acceleration: Decimal | None
    notes: tuple[str, ...]
    method_version: str = METHOD_VERSION


def build_opportunity_score(inputs: OpportunityInputs) -> OpportunityAssessment:
    growth = growth_runway_score(inputs.snapshots, inputs.metrics)
    size = size_runway_score(inputs.market_cap)
    megatrend_value, megatrend_confidence, megatrend_source, megatrend_evidence = _megatrend(inputs)
    market_value, market_evidence = _average_named(
        (
            ("market growth", inputs.market_growth_score),
            ("market size", inputs.market_size_score),
            ("market penetration", inputs.market_penetration_score),
        ),
        empty="No market growth, size, or penetration score was provided.",
    )
    strategic_value, strategic_evidence = _strategic(inputs)
    innovation_value, innovation_source, innovation_evidence = _innovation(inputs)
    moat_value, moat_evidence = _moat(inputs)
    expansion_value, expansion_evidence = _average_named(
        (
            ("capacity expansion", inputs.capacity_expansion_score),
            ("geographic expansion", inputs.geographic_expansion_score),
        ),
        empty="No capacity or geographic expansion score was provided.",
    )
    components = {
        "growth_runway": _component(
            growth.score,
            "growth_runway",
            confidence=_source_confidence("AUTO_FINANCIAL") if growth.score is not None else None,
            source="AUTO_FINANCIAL" if growth.score is not None else None,
            evidence=growth.evidence,
        ),
        "size_runway": _component(
            size.score,
            "size_runway",
            confidence=_source_confidence("AUTO_FINANCIAL") if size.score is not None else None,
            source="AUTO_FINANCIAL" if size.score is not None else None,
            evidence=size.evidence,
        ),
        "megatrend": _component(
            megatrend_value,
            "megatrend",
            confidence=megatrend_confidence,
            source=megatrend_source,
            evidence=megatrend_evidence,
        ),
        "market": _manual("market", market_value, market_evidence),
        "strategic_position": _manual("strategic_position", strategic_value, strategic_evidence),
        "bottleneck": _manual(
            "bottleneck",
            inputs.bottleneck_score,
            _bottleneck_evidence(inputs),
        ),
        "innovation": _component(
            innovation_value,
            "innovation",
            confidence=_source_confidence(innovation_source) if innovation_value is not None else None,
            source=innovation_source,
            evidence=innovation_evidence,
        ),
        "moat": _manual("moat", moat_value, moat_evidence),
        "expansion": _manual("expansion", expansion_value, expansion_evidence),
    }
    notes = _notes(inputs, components)
    components = _drop_size_when_alone(components, notes)
    included = [item for item in components.values() if item.included]
    available = sum((item.weight for item in included), Decimal(0))
    earned = sum((item.points for item in included if item.points is not None), Decimal(0))
    headline = None
    if available > 0:
        headline = (earned / available * Decimal(100)).quantize(POINTS, rounding=ROUND_HALF_UP)
    coverage_score, coverage_status, ranking_eligible = classify_coverage(available)
    if available < sum(WEIGHTS.values(), Decimal(0)):
        notes.append(
            "Missing components were excluded and the score was renormalized over the available weights. "
            "This is not a complete opportunity analysis."
        )
    return OpportunityAssessment(
        opportunity_score=headline,
        opportunity_confidence_score=_confidence(included, available),
        coverage_score=coverage_score,
        coverage_status=coverage_status,
        ranking_eligible=ranking_eligible,
        components=components,
        available_weight=available,
        excluded=tuple(name for name, item in components.items() if not item.included),
        acceleration=growth.acceleration,
        notes=tuple(notes),
    )


def classify_coverage(available_weight: Decimal) -> tuple[int, str, bool]:
    """How much of the opportunity weight is filled. This does not change the score.

    coverage_score = available_weight / 100 * 100.
    0–39 INCOMPLETE, 40–69 PARTIAL, 70–89 USABLE, 90–100 COMPLETE.
    A score can be ranked only when coverage is at least 70.
    """
    coverage_score = int(
        (available_weight / Decimal(100) * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    if coverage_score <= 39:
        status = "INCOMPLETE"
    elif coverage_score <= 69:
        status = "PARTIAL"
    elif coverage_score <= 89:
        status = "USABLE"
    else:
        status = "COMPLETE"
    return coverage_score, status, coverage_score >= 70


def _megatrend(
    inputs: OpportunityInputs,
) -> tuple[Decimal | None, int | None, str | None, str]:
    if not inputs.megatrends:
        return None, None, None, "No megatrend exposure was provided."
    weights = [Decimal(item.confidence) for item in inputs.megatrends]
    exposures = [item.exposure_score for item in inputs.megatrends]
    if sum(weights, Decimal(0)) == 0:
        score = sum(exposures, Decimal(0)) / Decimal(len(exposures))
    else:
        weighted = sum(
            (item.exposure_score * Decimal(item.confidence) for item in inputs.megatrends),
            Decimal(0),
        )
        score = weighted / sum(weights, Decimal(0))
    sources = {item.source for item in inputs.megatrends}
    source = sources.pop() if len(sources) == 1 else "ESTIMATED"
    item_confidence = int(
        (sum(weights, Decimal(0)) / Decimal(len(weights))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    confidence = min(item_confidence, _source_confidence(source))
    names = ", ".join(item.trend for item in inputs.megatrends)
    return (
        score.quantize(POINTS, rounding=ROUND_HALF_UP),
        confidence,
        source,
        f"Megatrend exposure across {names}, weighted by each trend confidence.",
    )


def _strategic(inputs: OpportunityInputs) -> tuple[Decimal | None, str]:
    roles = ", ".join(inputs.strategic_roles)
    role_note = f" Recorded roles: {roles}." if roles else ""
    if inputs.strategic_position_score is not None:
        return inputs.strategic_position_score, f"Strategic position was entered directly.{role_note}"
    diversification = inputs.customer_diversification_score
    diversification_note = ""
    if diversification is None:
        diversification = _diversification_from_customers(inputs)
        if diversification is not None:
            diversification_note = " Customer diversification was derived from the largest customer share."
    parts = [
        ("supplier leverage", inputs.supplier_leverage_score),
        ("customer diversification", diversification),
    ]
    score, evidence = _average_named(parts, empty="No numeric strategic position was provided.")
    if score is None:
        return None, f"No numeric strategic position was provided.{role_note}"
    return score, evidence + diversification_note + role_note


def _diversification_from_customers(inputs: OpportunityInputs) -> Decimal | None:
    shares = [customer.revenue_share for customer in inputs.customers if customer.revenue_share is not None]
    if not shares:
        return None
    return (Decimal(100) - max(shares)).quantize(POINTS, rounding=ROUND_HALF_UP)


def _innovation(inputs: OpportunityInputs) -> tuple[Decimal | None, str | None, str]:
    financial = rd_intensity_score(inputs.research_and_development, _latest_revenue(inputs.snapshots))
    manual = inputs.rd_intensity_score
    rd_value = financial if financial is not None else manual
    parts: list[tuple[str, Decimal | None]] = [("innovation", inputs.innovation_score)]
    if rd_value is not None:
        parts.append(("R&D intensity", rd_value))
    score, evidence = _average_named(parts, empty="No innovation or R&D intensity score was provided.")
    if score is None:
        return None, None, evidence
    if financial is not None and inputs.innovation_score is None:
        return score, "AUTO_FINANCIAL", evidence + " R&D intensity uses research and development divided by revenue."
    if financial is not None:
        return score, "MANUAL_STRUCTURED", evidence + " Financial R&D intensity is included with the manual innovation score."
    return score, "MANUAL_STRUCTURED", evidence


def _moat(inputs: OpportunityInputs) -> tuple[Decimal | None, str]:
    parts: list[tuple[str, Decimal | None]] = []
    if inputs.competitive_moat_score is not None:
        parts.append(("competitive moat", inputs.competitive_moat_score))
    if inputs.competition_risk_score is not None:
        parts.append(("inverted competition risk", Decimal(100) - inputs.competition_risk_score))
    return _average_named(
        parts,
        empty="No competitive moat or competition risk score was provided.",
    )


def _bottleneck_evidence(inputs: OpportunityInputs) -> str:
    if inputs.bottleneck_score is None:
        return "No bottleneck score was provided."
    extra = " ".join(inputs.evidence).strip()
    base = f"Bottleneck score {inputs.bottleneck_score} was entered as structured evidence."
    return f"{base} {extra}".strip()


def _average_named(
    parts: tuple[tuple[str, Decimal | None], ...] | list[tuple[str, Decimal | None]],
    *,
    empty: str,
) -> tuple[Decimal | None, str]:
    available = [(name, value) for name, value in parts if value is not None]
    if not available:
        return None, empty
    score = sum((value for _name, value in available), Decimal(0)) / Decimal(len(available))
    names = ", ".join(name for name, _value in available)
    return score.quantize(POINTS, rounding=ROUND_HALF_UP), f"Averaged {names}."


def _manual(name: str, score: Decimal | None, evidence: str) -> OpportunityComponent:
    source = "MANUAL_STRUCTURED" if score is not None else None
    confidence = _source_confidence(source) if source is not None else None
    return _component(score, name, confidence=confidence, source=source, evidence=evidence)


def _component(
    score: Decimal | None,
    name: str,
    *,
    confidence: int | None,
    source: str | None,
    evidence: str,
) -> OpportunityComponent:
    included = score is not None
    points = None
    if included:
        points = (score / Decimal(100) * WEIGHTS[name]).quantize(POINTS, rounding=ROUND_HALF_UP)
    return OpportunityComponent(
        score=score.quantize(POINTS, rounding=ROUND_HALF_UP) if score is not None else None,
        weight=WEIGHTS[name],
        points=points,
        confidence=confidence,
        source=source,
        evidence=evidence,
        included=included,
    )


def _drop_size_when_alone(
    components: dict[str, OpportunityComponent],
    notes: list[str],
) -> dict[str, OpportunityComponent]:
    included = [name for name, item in components.items() if item.included]
    if included != ["size_runway"]:
        return components
    size = components["size_runway"]
    notes.append(
        "Size runway was excluded because it is the only scored component. "
        "A small company is not an opportunity by size alone."
    )
    components["size_runway"] = OpportunityComponent(
        score=size.score,
        weight=size.weight,
        points=None,
        confidence=size.confidence,
        source=size.source,
        evidence=size.evidence,
        included=False,
    )
    return components


def _notes(inputs: OpportunityInputs, components: dict[str, OpportunityComponent]) -> list[str]:
    notes: list[str] = []
    if inputs.strategic_roles and components["strategic_position"].score is None:
        notes.append(
            "Strategic roles were recorded without a numeric position score, so strategic position is excluded."
        )
    return notes


def _confidence(included: list[OpportunityComponent], available: Decimal) -> int:
    if not included or available <= 0:
        return 0
    average = sum((Decimal(item.confidence or 0) for item in included), Decimal(0)) / Decimal(len(included))
    coverage = available / Decimal(100)
    return int((average * coverage).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _source_confidence(source: str | None) -> int:
    if source is None:
        return 0
    return SOURCE_CONFIDENCE.get(source, 0)


def _latest_revenue(snapshots: list[AnnualSnapshot]) -> Decimal | None:
    ordered = sorted(snapshots, key=lambda row: row.fiscal_year)
    if not ordered:
        return None
    return ordered[-1].revenue
