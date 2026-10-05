"""Market symbols that can be derived without guessing the venue."""

import re

# US listings whose Yahoo symbol is the ticker itself.
_US_EXCHANGES = frozenset(
    {
        "nasdaq",
        "nasdaqgs",
        "nasdaqgm",
        "nasdaqcm",
        "nms",
        "ngm",
        "ncm",
        "nyse",
        "nyse american",
        "nyse arca",
        "nyse mkt",
        "amex",
        "cboe",
        "bats",
    }
)
_TICKER = re.compile(r"^[A-Z]{1,5}$")


def reliable_market_symbol(ticker: str | None, exchange: str | None) -> str | None:
    """Return the ticker when the exchange is a known US venue. Otherwise return None."""
    symbol = (ticker or "").strip().upper()
    venue = " ".join((exchange or "").strip().casefold().split())
    if _TICKER.fullmatch(symbol) is None or venue not in _US_EXCHANGES:
        return None
    return symbol
