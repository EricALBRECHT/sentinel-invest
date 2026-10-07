"""Keep useful text. Tags and tracking parameters are removed."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import re

_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_SCRIPT = re.compile(r"<script[\s\S]*?</script>", re.IGNORECASE)
_STYLE = re.compile(r"<style[\s\S]*?</style>", re.IGNORECASE)
_BLOCK = re.compile(
    r"<(?:div|section)[^>]*(?:class|id)=[\"'][^\"']*(?:entry-content|post-content|article-body|article__body)[^\"']*[\"'][^>]*>([\s\S]*?)</(?:div|section)>",
    re.IGNORECASE,
)
_ARTICLE = re.compile(r"<article\b[^>]*>([\s\S]*?)</article>", re.IGNORECASE)
_MAIN = re.compile(r"<main\b[^>]*>([\s\S]*?)</main>", re.IGNORECASE)
_ELLIPSIS = ("[…]", "[...]", "…")
_TRACKING = ("utm_", "utm-", "fbclid", "gclid", "mc_cid", "mc_eid")
# Below this length, RSS body is treated as a teaser and article fetch is attempted.
MIN_FULL_ARTICLE_CHARS = 500


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


def has_truncation_marker(text: str | None) -> bool:
    if not text:
        return False
    trimmed = text.strip()
    if any(trimmed.endswith(marker) for marker in _ELLIPSIS):
        return True
    return "[…]" in trimmed or "[...]" in trimmed


def looks_truncated(text: str | None) -> bool:
    """RSS teasers: ellipsis markers, or bodies too short to be a full article."""
    if not text:
        return True
    trimmed = text.strip()
    if has_truncation_marker(trimmed):
        return True
    # Short feed descriptions without […] still need an article-page fetch
    # (historical NVIDIA newsroom teasers were stuck at ~350 chars).
    return len(trimmed) < MIN_FULL_ARTICLE_CHARS


def extract_article_plain_text(html: str | None, limit: int) -> str | None:
    """Best-effort article body from an HTML page. Prefer entry/post content over whole page."""
    if not html:
        return None
    cleaned = _SCRIPT.sub(" ", html)
    cleaned = _STYLE.sub(" ", cleaned)
    for pattern in (_BLOCK, _ARTICLE, _MAIN):
        match = pattern.search(cleaned)
        if match:
            body = plain_text(match.group(1), limit)
            if body and len(body) >= 500:
                return body
    return plain_text(cleaned, limit)


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
