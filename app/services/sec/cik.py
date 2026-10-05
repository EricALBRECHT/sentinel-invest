"""SEC CIK normalization.

The CIK is stored without leading zeros (``1045810``).
EDGAR companyfacts URLs need a 10-digit CIK, so the padding is applied
only when the URL is built.
"""


def normalize_cik(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("SEC CIK must be a numeric identifier")
    raw = str(value).strip() if isinstance(value, str) else str(value)
    if not raw.isdigit():
        raise ValueError("SEC CIK must contain only digits")
    if len(raw) > 10:
        raise ValueError("SEC CIK must be at most 10 digits")
    normalized = raw.lstrip("0")
    if not normalized:
        raise ValueError("SEC CIK must contain a non-zero identifier")
    return normalized


def cik_for_companyfacts(cik: str) -> str:
    return normalize_cik(cik).zfill(10)


def cik_for_archive_path(cik: str) -> str:
    return normalize_cik(cik)
