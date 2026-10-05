"""Rule-based trend labels. There is no model and no forecast.

Short term compares the performance price with SMA20 and the change in SMA20
over the previous five sessions. Medium term compares price with SMA50 and
SMA20 with SMA50. Long term compares price with SMA200 and SMA50 with SMA200.
A missing average leaves the label empty.
"""

from decimal import Decimal

STRONG_UP = "STRONG_UP"
UP = "UP"
NEUTRAL = "NEUTRAL"
DOWN = "DOWN"
STRONG_DOWN = "STRONG_DOWN"

TRENDS = (STRONG_UP, UP, NEUTRAL, DOWN, STRONG_DOWN)
_SHORT_SLOPE = Decimal("0.5")
_STACK_GAP = Decimal("1")


def classify_short(
    price: Decimal | None,
    sma20: Decimal | None,
    sma20_five_ago: Decimal | None,
) -> str | None:
    if price is None or sma20 is None or sma20_five_ago is None or sma20_five_ago == 0:
        return None
    slope = (sma20 - sma20_five_ago) / sma20_five_ago * Decimal(100)
    if price > sma20 and slope > _SHORT_SLOPE:
        return STRONG_UP
    if price > sma20 and slope >= 0:
        return UP
    if price < sma20 and slope < -_SHORT_SLOPE:
        return STRONG_DOWN
    if price < sma20 and slope <= 0:
        return DOWN
    return NEUTRAL


def classify_medium(price: Decimal | None, sma20: Decimal | None, sma50: Decimal | None) -> str | None:
    if price is None or sma20 is None or sma50 is None or sma50 == 0:
        return None
    distance = (price - sma50) / sma50 * Decimal(100)
    if price > sma50 and sma20 > sma50 and distance >= _STACK_GAP:
        return STRONG_UP
    if price > sma50 and sma20 >= sma50:
        return UP
    if price < sma50 and sma20 < sma50 and distance <= -_STACK_GAP:
        return STRONG_DOWN
    if price < sma50 and sma20 <= sma50:
        return DOWN
    return NEUTRAL


def classify_long(price: Decimal | None, sma50: Decimal | None, sma200: Decimal | None) -> str | None:
    if price is None or sma50 is None or sma200 is None or sma200 == 0:
        return None
    gap = (sma50 - sma200) / sma200 * Decimal(100)
    if price > sma200 and sma50 > sma200 and gap >= _STACK_GAP:
        return STRONG_UP
    if price >= sma200 and sma50 >= sma200:
        return UP
    if price < sma200 and sma50 < sma200 and gap <= -_STACK_GAP:
        return STRONG_DOWN
    if price < sma200 and sma50 <= sma200:
        return DOWN
    return NEUTRAL
