"""Nearby support and resistance from local highs and lows.

The window is the last 120 sessions. A pivot low is a low strictly below the
three bars on each side. A pivot high is the same test on highs. Levels within
1.5 percent of each other are averaged. support_1 is the closest clustered
low strictly under the traded close, then support_2. Resistances are the
closest clustered highs strictly above the close. This is not a trendline.
"""

from decimal import Decimal, ROUND_HALF_UP

LOOKBACK = 120
PIVOT_RADIUS = 3
CLUSTER_RATIO = Decimal("0.015")
_PRICE = Decimal("0.000001")


def nearest_levels(
    highs: list[Decimal | None],
    lows: list[Decimal | None],
    price: Decimal | None,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    if price is None or price <= 0:
        return None, None, None, None
    start = max(0, len(highs) - LOOKBACK)
    high_window = highs[start:]
    low_window = lows[start:]
    if len(high_window) < PIVOT_RADIUS * 2 + 1:
        return None, None, None, None
    supports = _cluster(
        _pivots(low_window, find_low=True),
    )
    resistances = _cluster(
        _pivots(high_window, find_low=False),
    )
    below = sorted((level for level in supports if level < price), reverse=True)
    above = sorted(level for level in resistances if level > price)
    return (
        _at(below, 0),
        _at(below, 1),
        _at(above, 0),
        _at(above, 1),
    )


def _pivots(values: list[Decimal | None], *, find_low: bool) -> list[Decimal]:
    found: list[Decimal] = []
    last = len(values) - PIVOT_RADIUS
    for index in range(PIVOT_RADIUS, last):
        center = values[index]
        if center is None:
            continue
        window = values[index - PIVOT_RADIUS : index + PIVOT_RADIUS + 1]
        if any(item is None for item in window):
            continue
        others = [item for offset, item in enumerate(window) if offset != PIVOT_RADIUS and item is not None]
        if find_low and all(center < item for item in others):
            found.append(center)
        if not find_low and all(center > item for item in others):
            found.append(center)
    return found


def _cluster(levels: list[Decimal]) -> list[Decimal]:
    if not levels:
        return []
    ordered = sorted(levels)
    groups: list[list[Decimal]] = [[ordered[0]]]
    for level in ordered[1:]:
        anchor = sum(groups[-1], Decimal(0)) / Decimal(len(groups[-1]))
        if anchor != 0 and abs(level - anchor) / abs(anchor) <= CLUSTER_RATIO:
            groups[-1].append(level)
        else:
            groups.append([level])
    clustered = []
    for group in groups:
        average = sum(group, Decimal(0)) / Decimal(len(group))
        clustered.append(average.quantize(_PRICE, rounding=ROUND_HALF_UP))
    return clustered


def _at(levels: list[Decimal], index: int) -> Decimal | None:
    if index >= len(levels):
        return None
    return levels[index]
