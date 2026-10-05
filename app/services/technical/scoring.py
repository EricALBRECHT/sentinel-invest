"""Timing score from the daily indicators.

The score asks whether the recent price action is a reasonable moment to
start or add to a position that already passed quality and opportunity. It
is not a buy or sell signal, and it does not replace those scores.

Weights sum to 100: long trend 25, medium 20, short 15, RSI 10, MACD 10,
distance to SMA200 10, volume confirmation 5, support or resistance 5.
A missing input is left out and the remaining weights are renormalized.
The result is clamped to 0-100.

An uptrend with a very high RSI scores less than the same trend with a
neutral RSI. Price under SMA200 scores none of the SMA200 points. A positive
accelerating MACD scores all of the MACD points. Volume above average on an
up day scores the volume points. Price within 3 percent of support scores
most of the level points. Price within 2 percent of resistance scores few.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.services.technical.indicators import IndicatorSet
from app.services.technical.trends import DOWN, NEUTRAL, STRONG_DOWN, STRONG_UP, UP

WEIGHTS: dict[str, Decimal] = {
    "trend_long": Decimal(25),
    "trend_medium": Decimal(20),
    "trend_short": Decimal(15),
    "rsi": Decimal(10),
    "macd": Decimal(10),
    "sma200_position": Decimal(10),
    "volume": Decimal(5),
    "support_resistance": Decimal(5),
}
_SCORE = Decimal("0.01")
_TREND_QUALITY = {
    STRONG_UP: Decimal(100),
    UP: Decimal(75),
    NEUTRAL: Decimal(50),
    DOWN: Decimal(25),
    STRONG_DOWN: Decimal(0),
}
_TIMING_NOTE = (
    "Timing score only. It does not replace quality or opportunity and it is not a buy or sell decision."
)


@dataclass(frozen=True)
class TechnicalScoreResult:
    technical_score: Decimal | None
    technical_confidence: int
    components: dict


def score_snapshot(
    indicators: IndicatorSet,
    *,
    trend_short: str | None,
    trend_medium: str | None,
    trend_long: str | None,
    support_1: Decimal | None,
    resistance_1: Decimal | None,
) -> TechnicalScoreResult:
    components = {
        "trend_long": _trend_component("trend_long", trend_long, "Long trend"),
        "trend_medium": _trend_component("trend_medium", trend_medium, "Medium trend"),
        "trend_short": _trend_component("trend_short", trend_short, "Short trend"),
        "rsi": _rsi_component(indicators.rsi_14),
        "macd": _macd_component(indicators.macd, indicators.macd_histogram),
        "sma200_position": _sma_position_component(indicators.distance_sma_200_pct),
        "volume": _volume_component(indicators),
        "support_resistance": _level_component(indicators.close, support_1, resistance_1),
    }
    included = [item for item in components.values() if item["included"]]
    available = sum((Decimal(item["weight"]) for item in included), Decimal(0))
    earned = sum((Decimal(item["points"]) for item in included if item["points"] is not None), Decimal(0))
    technical_score = None
    if available > 0:
        technical_score = bounded_score(earned / available * Decimal(100))
    components["available_weight"] = _text(available)
    components["sessions"] = indicators.sessions
    components["note"] = _TIMING_NOTE
    return TechnicalScoreResult(
        technical_score=technical_score,
        technical_confidence=_confidence(
            indicators,
            support_1=support_1,
            resistance_1=resistance_1,
        ),
        components=components,
    )


def bounded_score(value: Decimal) -> Decimal:
    quantized = value.quantize(_SCORE, rounding=ROUND_HALF_UP)
    if quantized < 0:
        return Decimal("0.00")
    if quantized > 100:
        return Decimal("100.00")
    return quantized


def _trend_component(name: str, trend: str | None, label: str) -> dict:
    if trend not in _TREND_QUALITY:
        return _empty(name, f"{label} is unavailable because a required average is missing.")
    quality = _TREND_QUALITY[trend]
    return _filled(
        name,
        quality,
        value=trend,
        evidence=f"{label} is {trend}.",
    )


def _rsi_component(rsi: Decimal | None) -> dict:
    if rsi is None:
        return _empty("rsi", "RSI14 is unavailable because fewer than 15 closes are stored.")
    if rsi < 30:
        quality = Decimal(85)
        reason = "oversold, which can improve entry timing but is not a buy signal"
    elif rsi < 45:
        quality = Decimal(100)
        reason = "in a pullback zone that does not look stretched"
    elif rsi < 60:
        quality = Decimal(80)
        reason = "neutral"
    elif rsi < 70:
        quality = Decimal(55)
        reason = "firm but not yet a strong timing penalty"
    elif rsi < 80:
        quality = Decimal(30)
        reason = "overbought, so the timing score is reduced"
    else:
        quality = Decimal(10)
        reason = "very overbought, so the timing score is reduced sharply"
    return _filled("rsi", quality, value=_text(rsi), evidence=f"RSI14 is {rsi}, {reason}.")


def _macd_component(macd: Decimal | None, histogram: Decimal | None) -> dict:
    if macd is None or histogram is None:
        return _empty("macd", "MACD is unavailable because the EMA12/EMA26/signal history is too short.")
    if macd > 0 and histogram > 0:
        quality = Decimal(100)
        reason = "positive and the histogram is positive, so momentum is accelerating"
    elif macd > 0:
        quality = Decimal(60)
        reason = "positive, but the histogram is not accelerating"
    elif histogram > 0:
        quality = Decimal(45)
        reason = "negative, while the histogram is improving"
    else:
        quality = Decimal(15)
        reason = "negative and the histogram is not improving"
    return _filled("macd", quality, value=_text(macd), evidence=f"MACD is {macd} and is {reason}.")


def _sma_position_component(distance: Decimal | None) -> dict:
    if distance is None:
        return _empty("sma200_position", "Distance to SMA200 is unavailable.")
    if distance < 0:
        quality = Decimal(0)
        reason = "below SMA200, which takes none of the SMA200 timing points"
    elif distance <= 3:
        quality = Decimal(70)
        reason = "just above SMA200"
    elif distance <= 15:
        quality = Decimal(100)
        reason = "above SMA200 without a large extension"
    elif distance <= 30:
        quality = Decimal(60)
        reason = "extended above SMA200"
    elif distance <= 50:
        quality = Decimal(35)
        reason = "far above SMA200"
    else:
        quality = Decimal(15)
        reason = "extremely extended above SMA200"
    return _filled(
        "sma200_position",
        quality,
        value=_text(distance),
        evidence=f"Price is {distance}% versus SMA200, {reason}.",
    )


def _volume_component(indicators: IndicatorSet) -> dict:
    ratio = indicators.volume_ratio
    price = indicators.performance
    previous = indicators.previous_performance
    if ratio is None or price is None or previous is None:
        return _empty("volume", "Volume confirmation needs 20 volumes and a previous close.")
    if price > previous and ratio >= Decimal("1.2"):
        quality = Decimal(100)
        reason = "an up day on above-average volume"
    elif price > previous and ratio >= 1:
        quality = Decimal(70)
        reason = "an up day on at least average volume"
    elif price > previous:
        quality = Decimal(40)
        reason = "an up day on below-average volume"
    elif price < previous and ratio >= Decimal("1.2"):
        quality = Decimal(20)
        reason = "a down day on above-average volume"
    elif price < previous:
        quality = Decimal(50)
        reason = "a down day on light volume"
    else:
        quality = Decimal(50)
        reason = "an unchanged close"
    return _filled("volume", quality, value=_text(ratio), evidence=f"Volume ratio is {ratio} on {reason}.")


def _level_component(
    price: Decimal | None,
    support: Decimal | None,
    resistance: Decimal | None,
) -> dict:
    if price is None or price <= 0 or (support is None and resistance is None):
        return _empty("support_resistance", "No clustered pivot support or resistance is close enough to score.")
    distance_support = None if support is None else (price - support) / price * Decimal(100)
    distance_resistance = None if resistance is None else (resistance - price) / price * Decimal(100)
    near_resistance = distance_resistance is not None and Decimal(0) <= distance_resistance <= 2
    near_support = distance_support is not None and Decimal(0) <= distance_support <= 3
    if near_resistance and (not near_support or distance_resistance <= distance_support):
        quality = Decimal(20)
        reason = f"within {distance_resistance.quantize(Decimal('0.01'))}% of resistance {resistance}"
    elif near_support:
        quality = Decimal(85)
        reason = f"within {distance_support.quantize(Decimal('0.01'))}% of support {support}"
    elif distance_support is not None and distance_resistance is not None:
        quality = Decimal(70) if distance_support <= distance_resistance else Decimal(45)
        reason = "between the nearest support and resistance"
    elif distance_support is not None:
        quality = Decimal(70)
        reason = f"above support {support} with no resistance in the window"
    else:
        quality = Decimal(40)
        reason = f"below resistance {resistance} with no support in the window"
    return _filled("support_resistance", quality, value=reason, evidence=f"Price is {reason}.")


def _confidence(
    indicators: IndicatorSet,
    *,
    support_1: Decimal | None,
    resistance_1: Decimal | None,
) -> int:
    checks = (
        indicators.sessions >= 200,
        indicators.sma_200 is not None,
        indicators.rsi_14 is not None,
        indicators.macd is not None and indicators.macd_signal is not None,
        indicators.volume is not None and indicators.average_volume_20d is not None,
        support_1 is not None or resistance_1 is not None,
    )
    passed = sum(1 for check in checks if check)
    percent = (Decimal(passed) / Decimal(len(checks)) * Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(percent)


def _filled(name: str, quality: Decimal, *, value, evidence: str) -> dict:
    weight = WEIGHTS[name]
    points = (quality / Decimal(100) * weight).quantize(_SCORE, rounding=ROUND_HALF_UP)
    return {
        "score": _text(quality),
        "weight": _text(weight),
        "points": _text(points),
        "included": True,
        "value": value,
        "evidence": evidence,
    }


def _empty(name: str, evidence: str) -> dict:
    return {
        "score": None,
        "weight": _text(WEIGHTS[name]),
        "points": None,
        "included": False,
        "value": None,
        "evidence": evidence,
    }


def _text(value: Decimal) -> str:
    return format(value, "f")
