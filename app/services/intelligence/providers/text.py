"""Keep useful text. Tags and tracking parameters are removed."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import re

_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_TRACKING = ("utm_", "utm-", "fbclid", "gclid", "mc_cid", "mc_eid")


def plain_text(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    text = unescape(_TAGS.sub(" ", value))
    text = _SPACE.sub(" ", text).strip()
    if not text:
        return None
    if len(text) > limit:
        return text[:limit]
    return text


def content_hash(title: str | None, content: str | None) -> str:
    material = _SPACE.sub(" ", f"{title or ''}\n{content or ''}".casefold()).strip()
    return sha256(material.encode("utf-8")).hexdigest()


def canonical_url(url: str | None) -> str | None:
    if not url or not url.strip():
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(_TRACKING) and key.lower() not in _TRACKING
    ]
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def parse_datetime(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value.strip())
        except (TypeError, ValueError, IndexError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
