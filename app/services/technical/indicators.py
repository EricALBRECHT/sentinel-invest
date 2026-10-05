"""Daily indicators from stored bars.

Moving averages, RSI, and MACD use adjusted_close when the bar has one,
otherwise close. ATR uses the traded high, low, and previous close, because
those fields share a price scale. A value stays empty when the series is
shorter than the indicator's window.

EMA is seeded with a simple average of the first period, then
EMA_t = EMA_(t-1) + (2 / (period + 1)) * (price_t - EMA_(t-1)).

RSI uses Wilder's smoothing. The first average gain and loss are the simple
means of the first 14 changes. Later values are
((previous * 13) + current) / 14. RSI is 100 when average loss is 0 and
average gain is positive, and 50 when both are 0.

MACD is EMA12 - EMA26. The signal is an EMA9 of that line, seeded the same
way. The histogram is MACD minus signal.

ATR14 seeds with the simple mean of the first 14 true ranges, then applies
the same Wilder smoothing. True range is the largest of high-low,
abs(high - previous close), and abs(low - previous close).

volatility_20d is the sample standard deviation of the last 20 simple
returns, annualized and stored as a percent:
r_t = price_t / price_(t-1) - 1
sigma = sqrt(sum((r - mean)^2) / 19)
volatility_20d = sigma * sqrt(252) * 100
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

_PRICE = Decimal("0.000001")
_PERCENT = Decimal("0.0001")
_RATIO = Decimal("0.0001")


@dataclass(frozen=True)
class PriceBar:
    trade_date: date
    close: Decimal | None
    high: Decimal | None
    low: Decimal | None
    performance: Decimal
    volume: int | None


@dataclass(frozen=True)
class IndicatorSet:
    as_of_date: date
    sessions: int
    price: Decimal | None
    performance: Decimal | None
    previous_performance: Decimal | None
    close: Decimal | None
    sma_20: Decimal | None
    sma_50: Decimal | None
    sma_100: Decimal | None
    sma_200: Decimal | None
    sma_20_five_ago: Decimal | None
    ema_12: Decimal | None
    ema_26: Decimal | None
    rsi_14: Decimal | None
    macd: Decimal | None
    macd_signal: Decimal | None
    macd_histogram: Decimal | None
    atr_14: Decimal | None
    volatility_20d: Decimal | None
    volume: int | None
    average_volume_20d: int | None
    volume_ratio: Decimal | None
    distance_sma_20_pct: Decimal | None
    distance_sma_50_pct: Decimal | None
    distance_sma_200_pct: Decimal | None
    week_52_position_pct: Decimal | None


def sma_last(values: list[Decimal], period: int) -> Decimal | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:], Decimal(0)) / Decimal(period)


def sma_at(values: list[Decimal], period: int, end: int) -> Decimal | None:
    """Simple average ending at index `end`, inclusive."""
    if end < 0 or end >= len(values):
        return None
    return sma_last(values[: end + 1], period)


def ema_series(values: list[Decimal], period: int) -> list[Decimal | None]:
    if period <= 0 or len(values) < period:
        return [None] * len(values)
    multiplier = Decimal(2) / Decimal(period + 1)
    seed = sum(values[:period], Decimal(0)) / Decimal(period)
    series: list[Decimal | None] = [None] * (period - 1)
    series.append(seed)
    previous = seed
    for value in values[period:]:
        previous = previous + multiplier * (value - previous)
        series.append(previous)
    return series


def ema_last(values: list[Decimal], period: int) -> Decimal | None:
    series = ema_series(values, period)
    return None if not series else series[-1]


def rsi_wilder(values: list[Decimal], period: int = 14) -> Decimal | None:
    if period <= 0 or len(values) <= period:
        return None
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for previous, current in zip(values, values[1:]):
        change = current - previous
        gains.append(change if change > 0 else Decimal(0))
        losses.append(-change if change < 0 else Decimal(0))
    average_gain = sum(gains[:period], Decimal(0)) / Decimal(period)
    average_loss = sum(losses[:period], Decimal(0)) / Decimal(period)
    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = (average_gain * Decimal(period - 1) + gain) / Decimal(period)
        average_loss = (average_loss * Decimal(period - 1) + loss) / Decimal(period)
    if average_gain == 0 and average_loss == 0:
        return Decimal(50)
    if average_loss == 0:
        return Decimal(100)
    relative = average_gain / average_loss
    return Decimal(100) - (Decimal(100) / (Decimal(1) + relative))


def macd_last(
    values: list[Decimal],
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    fast_series = ema_series(values, fast)
    slow_series = ema_series(values, slow)
    line = [
        None if fast_value is None or slow_value is None else fast_value - slow_value
        for fast_value, slow_value in zip(fast_series, slow_series)
    ]
    defined = [value for value in line if value is not None]
    if len(defined) < signal:
        return None, None, None
    signal_series = ema_series(defined, signal)
    macd_value = defined[-1]
    signal_value = signal_series[-1]
    if signal_value is None:
        return None, None, None
    return macd_value, signal_value, macd_value - signal_value


def atr_wilder(bars: list[PriceBar], period: int = 14) -> Decimal | None:
    if period <= 0 or len(bars) <= period:
        return None
    ranges: list[Decimal] = []
    for previous, current in zip(bars, bars[1:]):
        if current.high is None or current.low is None or previous.close is None:
            return None
        true_range = max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        ranges.append(true_range)
    if len(ranges) < period:
        return None
    average = sum(ranges[:period], Decimal(0)) / Decimal(period)
    for true_range in ranges[period:]:
        average = (average * Decimal(period - 1) + true_range) / Decimal(period)
    return average


def annualized_volatility(values: list[Decimal], window: int = 20) -> Decimal | None:
    if window < 2 or len(values) <= window:
        return None
    tail = values[-(window + 1) :]
    returns: list[Decimal] = []
    for previous, current in zip(tail, tail[1:]):
        if previous == 0:
            return None
        returns.append(current / previous - 1)
    mean = sum(returns, Decimal(0)) / Decimal(len(returns))
    variance = sum((item - mean) ** 2 for item in returns) / Decimal(len(returns) - 1)
    annualized = variance.sqrt() * Decimal(252).sqrt() * Decimal(100)
    return annualized


def average_volume(bars: list[PriceBar], window: int = 20) -> tuple[int | None, int | None, Decimal | None]:
    if len(bars) < window:
        return None, None, None
    volumes = [bar.volume for bar in bars[-window:]]
    if any(volume is None for volume in volumes):
        return None, None, None
    present = [int(volume) for volume in volumes if volume is not None]
    current = present[-1]
    average = int((sum(present, 0) / Decimal(window)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if average <= 0:
        return current, average, None
    ratio = (Decimal(current) / Decimal(average)).quantize(_RATIO, rounding=ROUND_HALF_UP)
    return current, average, ratio


def distance_pct(price: Decimal | None, average: Decimal | None) -> Decimal | None:
    if price is None or average is None or average == 0:
        return None
    return ((price - average) / average * Decimal(100)).quantize(_PERCENT, rounding=ROUND_HALF_UP)


def week_position(bars: list[PriceBar], sessions: int = 252) -> Decimal | None:
    if len(bars) < sessions:
        return None
    window = bars[-sessions:]
    highs = [bar.high for bar in window if bar.high is not None]
    lows = [bar.low for bar in window if bar.low is not None]
    close = window[-1].close
    if not highs or not lows or close is None:
        return None
    high = max(highs)
    low = min(lows)
    if high == low:
        return None
    return ((close - low) / (high - low) * Decimal(100)).quantize(_PERCENT, rounding=ROUND_HALF_UP)


def compute_indicators(bars: list[PriceBar]) -> IndicatorSet:
    if not bars:
        raise ValueError("compute_indicators requires at least one bar")
    prices = [bar.performance for bar in bars]
    latest = bars[-1]
    sma20 = _price(sma_last(prices, 20))
    sma50 = _price(sma_last(prices, 50))
    sma100 = _price(sma_last(prices, 100))
    sma200 = _price(sma_last(prices, 200))
    sma20_past = None
    if len(prices) >= 25:
        sma20_past = _price(sma_at(prices, 20, len(prices) - 6))
    ema12 = _price(ema_last(prices, 12))
    ema26 = _price(ema_last(prices, 26))
    macd_value, signal_value, histogram = macd_last(prices)
    current, average, ratio = average_volume(bars)
    performance = _price(latest.performance)
    previous = _price(bars[-2].performance) if len(bars) > 1 else None
    return IndicatorSet(
        as_of_date=latest.trade_date,
        sessions=len(bars),
        price=_price(latest.close if latest.close is not None else latest.performance),
        performance=performance,
        previous_performance=previous,
        close=_price(latest.close),
        sma_20=sma20,
        sma_50=sma50,
        sma_100=sma100,
        sma_200=sma200,
        sma_20_five_ago=sma20_past,
        ema_12=ema12,
        ema_26=ema26,
        rsi_14=_percent(rsi_wilder(prices)),
        macd=_price(macd_value),
        macd_signal=_price(signal_value),
        macd_histogram=_price(histogram),
        atr_14=_price(atr_wilder(bars)),
        volatility_20d=_percent(annualized_volatility(prices)),
        volume=current,
        average_volume_20d=average,
        volume_ratio=ratio,
        distance_sma_20_pct=distance_pct(performance, sma20),
        distance_sma_50_pct=distance_pct(performance, sma50),
        distance_sma_200_pct=distance_pct(performance, sma200),
        week_52_position_pct=week_position(bars),
    )


def chart_rows(bars: list[PriceBar], limit: int) -> list[dict]:
    prices = [bar.performance for bar in bars]
    rolling20 = _rolling_mean(prices, 20)
    rolling50 = _rolling_mean(prices, 50)
    rolling200 = _rolling_mean(prices, 200)
    start = max(0, len(bars) - max(1, limit))
    rows = []
    for index in range(start, len(bars)):
        bar = bars[index]
        rows.append(
            {
                "trade_date": bar.trade_date,
                "price": _price(bar.performance),
                "sma_20": _price(rolling20[index]),
                "sma_50": _price(rolling50[index]),
                "sma_200": _price(rolling200[index]),
                "volume": bar.volume,
            }
        )
    return rows


def _rolling_mean(values: list[Decimal], period: int) -> list[Decimal | None]:
    series: list[Decimal | None] = [None] * len(values)
    if period <= 0:
        return series
    running = Decimal(0)
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        if index >= period - 1:
            series[index] = running / Decimal(period)
    return series


def _price(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(_PRICE, rounding=ROUND_HALF_UP)


def _percent(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(_PERCENT, rounding=ROUND_HALF_UP)
