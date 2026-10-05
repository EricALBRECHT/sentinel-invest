"""Short failure text that cannot carry a database or Redis URL."""

import re

_URL = re.compile(r"(postgres(?:ql)?(?:\+\w+)?://|redis://)\S+", re.IGNORECASE)
_PASSWORD = re.compile(r"(password=)\S+", re.IGNORECASE)


def brief_error(value: object) -> str:
    if value is None:
        return "Job failed"
    text = value if isinstance(value, str) else str(value)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    chosen = lines[-1] if lines else type(value).__name__
    chosen = _URL.sub(r"\1***", chosen)
    chosen = _PASSWORD.sub(r"\1***", chosen)
    lowered = chosen.lower()
    if "password" in lowered or "://" in chosen:
        return "Job failed"
    return chosen[:300] or "Job failed"
