"""Logging for worker and scheduler processes. Connection strings are redacted."""

import logging
import re

_URL = re.compile(r"(postgres(?:ql)?(?:\+\w+)?://|redis://)\S+", re.IGNORECASE)
_PASSWORD = re.compile(r"(password=)\S+", re.IGNORECASE)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _redact(super().format(record))


def configure_job_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_sentinel_job_logging", False):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(levelname)s %(name)s %(message)s"))
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root._sentinel_job_logging = True  # type: ignore[attr-defined]


def _redact(text: str) -> str:
    text = _URL.sub(r"\1***", text)
    return _PASSWORD.sub(r"\1***", text)
