"""Simple AI auto-analysis eligibility rules.

Short teasers and near-empty titles must not be sent to the GPU automatically.
Manual analysis (allow_ineligible / force) remains available.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.intelligence.providers.text import has_truncation_marker

# Default aligns with supervision SHORT_TEXT_CHARS / article enrichment floor.
DEFAULT_AI_MIN_CONTENT_CHARS = 500


def ai_min_content_chars() -> int:
    return max(1, int(getattr(settings, "ai_min_content_chars", DEFAULT_AI_MIN_CONTENT_CHARS) or DEFAULT_AI_MIN_CONTENT_CHARS))


def assess_ai_eligibility(
    content_text: str | None,
    *,
    title: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Return {eligible, reason} for automatic GPU analysis."""
    text = (content_text or "").strip()
    minimum = ai_min_content_chars()
    if not text:
        return {"eligible": False, "reason": "empty"}
    if len(text) < minimum:
        return {"eligible": False, "reason": "too_short"}
    title_text = (title or "").strip()
    if title_text and text.casefold() == title_text.casefold():
        return {"eligible": False, "reason": "title_only"}
    meta = metadata or {}
    if meta.get("content_source") == "rss_item" and len(text) < minimum * 2:
        return {"eligible": False, "reason": "rss_teaser"}
    # Teaser markers on short-or-medium bodies (full articles may quote "[...]").
    if has_truncation_marker(text) and len(text) < minimum * 2:
        return {"eligible": False, "reason": "truncated_teaser"}
    return {"eligible": True, "reason": "ok"}


def document_ai_eligible(
    content_text: str | None,
    *,
    title: str | None = None,
    metadata: dict | None = None,
) -> bool:
    return bool(assess_ai_eligibility(content_text, title=title, metadata=metadata)["eligible"])
