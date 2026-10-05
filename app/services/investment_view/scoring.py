"""Composite investment view.

Three readings stay separate. There is no single score and no buy or sell label.

Long-term conviction requires a quality score.
When opportunity is ranking eligible, quality is 45 percent and opportunity is 55 percent.
When it is not, the raw opportunity score is multiplied by coverage / 100 and the
weights stay 55 percent quality and 45 percent of that reduced score. The missing
coverage is not given back to quality.

Entry attractiveness starts from the technical score. Quality is not used.
A very high RSI, a price far above SMA200, or technical confidence below 70
removes a few points. An extreme extension also keeps the label cautious.

Analysis readiness adds the points that are actually available:
quality 30, opportunity coverage up to 40, technical 15, market data 10,
reliable market cap 5.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

METHOD = "investment_view_v1"
POINTS = Decimal("0.01")

ELIGIBLE_QUALITY_WEIGHT = Decimal("0.45")
ELIGIBLE_OPPORTUNITY_WEIGHT = Decimal("0.55")
PARTIAL_QUALITY_WEIGHT = Decimal("0.55")
PARTIAL_OPPORTUNITY_WEIGHT = Decimal("0.45")

RSI_VERY_OVERBOUGHT = Decimal(80)
SMA200_EXTENDED = Decimal(30)
SMA200_VERY_EXTENDED = Decimal(50)
PENALTY_RSI = Decimal(8)
PENALTY_EXTENDED = Decimal(5)
PENALTY_VERY_EXTENDED = Decimal(8)
PENALTY_LOW_CONFIDENCE = Decimal(5)
LOW_TECHNICAL_CONFIDENCE = 70
MAX_ENTRY_PENALTY = Decimal(15)

QUALITY_POINTS = Decimal(30)
OPPORTUNITY_POINTS = Decimal(40)
TECHNICAL_POINTS = Decimal(15)
MARKET_DATA_POINTS = Decimal(10)
MARKET_CAP_POINTS = Decimal(5)
RELIABLE_CAP = frozenset({"HIGH", "MEDIUM"})

WARNING_COVERAGE = "Opportunity coverage is below ranking threshold."
WARNING_CAP = "Market capitalization missing."
WARNING_EXTENDED = "Technical timing is extended above long-term average."
WARNING_QUALITY = "Quality score is missing."
WARNING_OPPORTUNITY = "Opportunity score is missing."
WARNING_TECHNICAL = "Technical score is missing."
WARNING_RSI = "RSI is very overbought."
WARNING_CONFIDENCE = "Technical confidence is below 70."


@dataclass(frozen=True)
class QualityInput:
    score: Decimal | None
    confidence: int | None
    as_of_date: date | None


@dataclass(frozen=True)
class OpportunityInput:
    raw_score: Decimal | None
    confidence: int | None
    coverage: int | None
    coverage_status: str | None
    ranking_eligible: bool | None
    as_of_date: date | None


@dataclass(frozen=True)
class TechnicalInput:
    score: Decimal | None
    confidence: int | None
    rsi_14: Decimal | None
    distance_sma_200_pct: Decimal | None
    as_of_date: date | None


@dataclass(frozen=True)
class MarketInput:
    has_market_data: bool
    market_cap: Decimal | None
    market_cap_confidence: str | None
    market_cap_reliable: bool


@dataclass(frozen=True)
class InvestmentAssessment:
    quality_score: Decimal | None
    quality_confidence: int | None
    opportunity_score: Decimal | None
    opportunity_confidence: int | None
    opportunity_coverage: int | None
    opportunity_coverage_status: str | None
    opportunity_ranking_eligible: bool | None
    technical_score: Decimal | None
    technical_confidence: int | None
    long_term_conviction_score: Decimal | None
    entry_attractiveness_score: Decimal | None
    analysis_readiness_score: Decimal
    long_term_conviction_label: str | None
    entry_attractiveness_label: str | None
    analysis_readiness_label: str
    components: dict


def build_investment_view(
    quality: QualityInput,
    opportunity: OpportunityInput,
    technical: TechnicalInput,
    market: MarketInput,
) -> InvestmentAssessment:
    eligible = bool(opportunity.ranking_eligible) and opportunity.raw_score is not None
    effective = _effective_opportunity(opportunity, eligible)
    conviction = _conviction(quality.score, effective, eligible)
    entry, penalty, extreme = _entry(technical)
    readiness, readiness_parts = _readiness(quality, opportunity, technical, market)
    warnings = _warnings(quality, opportunity, technical, market, extreme)
    components = {
        "quality": {
            "score": _text(quality.score),
            "confidence": quality.confidence,
            "as_of_date": _date(quality.as_of_date),
        },
        "opportunity": {
            "raw_score": _text(opportunity.raw_score),
            "coverage": opportunity.coverage,
            "coverage_status": opportunity.coverage_status,
            "ranking_eligible": opportunity.ranking_eligible,
            "confidence": opportunity.confidence,
            "effective_score": _text(effective),
            "as_of_date": _date(opportunity.as_of_date),
            "note": _opportunity_note(eligible, opportunity),
        },
        "technical": {
            "score": _text(technical.score),
            "confidence": technical.confidence,
            "rsi_14": _text(technical.rsi_14),
            "distance_sma_200_pct": _text(technical.distance_sma_200_pct),
            "penalty": _text(penalty),
            "as_of_date": _date(technical.as_of_date),
            "note": "Entry attractiveness uses technical timing only. Quality is not used for timing.",
        },
        "market_cap": {
            "reliable": market.market_cap_reliable,
            "confidence": market.market_cap_confidence,
            "value": None if market.market_cap is None else format(market.market_cap, "f"),
        },
        "market_data": {"available": market.has_market_data},
        "readiness": readiness_parts,
        "conviction": _conviction_note(quality.score, effective, eligible, conviction),
        "warnings": warnings,
    }
    return InvestmentAssessment(
        quality_score=_score(quality.score),
        quality_confidence=quality.confidence,
        opportunity_score=_score(opportunity.raw_score),
        opportunity_confidence=opportunity.confidence,
        opportunity_coverage=opportunity.coverage,
        opportunity_coverage_status=opportunity.coverage_status,
        opportunity_ranking_eligible=opportunity.ranking_eligible,
        technical_score=_score(technical.score),
        technical_confidence=technical.confidence,
        long_term_conviction_score=conviction,
        entry_attractiveness_score=entry,
        analysis_readiness_score=readiness,
        long_term_conviction_label=_long_label(conviction),
        entry_attractiveness_label=_entry_label(entry, extreme),
        analysis_readiness_label=_ready_label(readiness),
        components=components,
    )


def _effective_opportunity(opportunity: OpportunityInput, eligible: bool) -> Decimal:
    if opportunity.raw_score is None:
        return Decimal(0)
    raw = Decimal(opportunity.raw_score)
    if eligible:
        return _q(raw)
    coverage = Decimal(0 if opportunity.coverage is None else opportunity.coverage)
    return _q(raw * coverage / Decimal(100))


def _conviction(quality: Decimal | None, effective: Decimal, eligible: bool) -> Decimal | None:
    if quality is None:
        return None
    if eligible:
        value = ELIGIBLE_QUALITY_WEIGHT * Decimal(quality) + ELIGIBLE_OPPORTUNITY_WEIGHT * effective
    else:
        value = PARTIAL_QUALITY_WEIGHT * Decimal(quality) + PARTIAL_OPPORTUNITY_WEIGHT * effective
    return _clamp(value)


def _entry(technical: TechnicalInput) -> tuple[Decimal | None, Decimal, bool]:
    if technical.score is None:
        return None, Decimal(0), False
    penalty = Decimal(0)
    extreme = False
    if technical.rsi_14 is not None and technical.rsi_14 >= RSI_VERY_OVERBOUGHT:
        penalty += PENALTY_RSI
        extreme = True
    if technical.distance_sma_200_pct is not None and technical.distance_sma_200_pct >= SMA200_VERY_EXTENDED:
        penalty += PENALTY_VERY_EXTENDED
        extreme = True
    elif technical.distance_sma_200_pct is not None and technical.distance_sma_200_pct >= SMA200_EXTENDED:
        penalty += PENALTY_EXTENDED
        extreme = True
    if technical.confidence is not None and technical.confidence < LOW_TECHNICAL_CONFIDENCE:
        penalty += PENALTY_LOW_CONFIDENCE
    if penalty > MAX_ENTRY_PENALTY:
        penalty = MAX_ENTRY_PENALTY
    return _clamp(Decimal(technical.score) - penalty), _q(penalty), extreme


def _readiness(
    quality: QualityInput,
    opportunity: OpportunityInput,
    technical: TechnicalInput,
    market: MarketInput,
) -> tuple[Decimal, dict]:
    quality_points = QUALITY_POINTS if quality.score is not None else Decimal(0)
    if opportunity.coverage is None:
        opportunity_points = Decimal(0)
    else:
        opportunity_points = _q(Decimal(opportunity.coverage) / Decimal(100) * OPPORTUNITY_POINTS)
    technical_points = TECHNICAL_POINTS if technical.score is not None else Decimal(0)
    market_points = MARKET_DATA_POINTS if market.has_market_data else Decimal(0)
    cap_points = MARKET_CAP_POINTS if market.market_cap_reliable else Decimal(0)
    total = _clamp(quality_points + opportunity_points + technical_points + market_points + cap_points)
    parts = {
        "quality": _text(quality_points),
        "opportunity_coverage": _text(opportunity_points),
        "technical": _text(technical_points),
        "market_data": _text(market_points),
        "market_cap": _text(cap_points),
        "total": _text(total),
    }
    return total, parts


def _warnings(
    quality: QualityInput,
    opportunity: OpportunityInput,
    technical: TechnicalInput,
    market: MarketInput,
    extreme: bool,
) -> list[str]:
    warnings: list[str] = []
    if quality.score is None:
        warnings.append(WARNING_QUALITY)
    if opportunity.raw_score is None:
        warnings.append(WARNING_OPPORTUNITY)
    if not opportunity.ranking_eligible:
        warnings.append(WARNING_COVERAGE)
    if technical.score is None:
        warnings.append(WARNING_TECHNICAL)
    if technical.rsi_14 is not None and technical.rsi_14 >= RSI_VERY_OVERBOUGHT:
        warnings.append(WARNING_RSI)
    if extreme and technical.distance_sma_200_pct is not None and technical.distance_sma_200_pct >= SMA200_EXTENDED:
        warnings.append(WARNING_EXTENDED)
    if (
        technical.score is not None
        and technical.confidence is not None
        and technical.confidence < LOW_TECHNICAL_CONFIDENCE
    ):
        warnings.append(WARNING_CONFIDENCE)
    if not market.market_cap_reliable:
        warnings.append(WARNING_CAP)
    return warnings


def _opportunity_note(eligible: bool, opportunity: OpportunityInput) -> str:
    if eligible:
        return (
            "Opportunity is ranking eligible. Conviction uses 45 percent quality "
            "and 55 percent of the raw opportunity score."
        )
    if opportunity.raw_score is None:
        return (
            "No opportunity score is available at this date. "
            "Its effective score is 0 and conviction is not renormalized."
        )
    return (
        "Opportunity is not ranking eligible. "
        "The effective score is the raw score multiplied by coverage / 100. "
        "Conviction uses 55 percent quality and 45 percent of that effective score. "
        "Weights are not renormalized."
    )


def _conviction_note(
    quality: Decimal | None,
    effective: Decimal,
    eligible: bool,
    conviction: Decimal | None,
) -> dict:
    if quality is None:
        return {
            "calculated": False,
            "note": "Long-term conviction requires a quality score.",
        }
    quality_weight = ELIGIBLE_QUALITY_WEIGHT if eligible else PARTIAL_QUALITY_WEIGHT
    opportunity_weight = ELIGIBLE_OPPORTUNITY_WEIGHT if eligible else PARTIAL_OPPORTUNITY_WEIGHT
    return {
        "calculated": True,
        "quality_weight": format(quality_weight, "f"),
        "opportunity_weight": format(opportunity_weight, "f"),
        "opportunity_effective": _text(effective),
        "renormalized": False,
        "score": _text(conviction),
    }


def _long_label(score: Decimal | None) -> str | None:
    if score is None:
        return None
    if score < 40:
        return "LOW"
    if score < 60:
        return "MODERATE"
    if score < 75:
        return "GOOD"
    if score < 90:
        return "HIGH"
    return "VERY_HIGH"


def _entry_label(score: Decimal | None, extreme: bool) -> str | None:
    if score is None:
        return None
    if score < 40:
        label = "POOR"
    elif score < 60:
        label = "NEUTRAL"
    elif score < 75:
        label = "FAVORABLE"
    elif score < 90:
        label = "STRONG"
    else:
        label = "EXTENDED_OR_EXCEPTIONAL"
    if extreme and label == "STRONG":
        return "FAVORABLE"
    return label


def _ready_label(score: Decimal) -> str:
    if score < 40:
        return "INCOMPLETE"
    if score < 70:
        return "PARTIAL"
    if score < 90:
        return "USABLE"
    return "COMPLETE"


def _q(value: Decimal) -> Decimal:
    return value.quantize(POINTS, rounding=ROUND_HALF_UP)


def _clamp(value: Decimal) -> Decimal:
    return _q(min(Decimal(100), max(Decimal(0), value)))


def _score(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return _clamp(Decimal(value))


def _text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(_q(Decimal(value)), "f")


def _date(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
