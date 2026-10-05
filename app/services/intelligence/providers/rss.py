"""RSS and Atom feeds. The feed XML is parsed, then only plain text is kept."""

from datetime import datetime, timezone
from urllib.parse import urlsplit
from xml.etree import ElementTree
import logging

from app.core.config import settings
from app.services.intelligence.providers.base import FetchBatch, NormalizedDocument
from app.services.intelligence.providers.http import PoliteClient
from app.services.intelligence.providers.robots import robots_policy
from app.services.intelligence.providers.text import parse_datetime, plain_text

logger = logging.getLogger("sentinel.intelligence")


class RssAtomProvider:
    provider_name = "rss"

    def __init__(self, http: PoliteClient | None = None) -> None:
        self.http = http or PoliteClient()

    async def fetch_since(self, source, since: datetime) -> FetchBatch:
        feed_url = source.base_url
        agent = _agent(source)
        allowed, delay = await self._robots(feed_url, agent)
        if not allowed:
            return FetchBatch([], "robots.txt disallows this feed")
        self.http.set_crawl_delay(feed_url, delay)
        fetched = await self.http.get_text(feed_url, user_agent=agent)
        if fetched.error:
            return FetchBatch([], fetched.error)
        try:
            raw_items = parse_feed(fetched.text)
        except ElementTree.ParseError:
            return FetchBatch([], "Feed was not valid XML")
        documents = []
        for raw in raw_items:
            document = self.normalize_document(raw, source)
            if document.published_at is not None and document.published_at < since:
                continue
            documents.append(document)
        return FetchBatch(documents)

    def normalize_document(self, raw: dict, source=None) -> NormalizedDocument:
        limit = settings.intelligence_max_text_chars
        content = plain_text(raw.get("content"), limit)
        summary = plain_text(raw.get("summary") or raw.get("content"), 500)
        document_type = "NEWS_ARTICLE"
        if source is not None:
            document_type = str((source.metadata_json or {}).get("document_type") or document_type)
        return NormalizedDocument(
            external_id=_clean(raw.get("external_id"), 255),
            url=_clean(raw.get("url"), 1000),
            title=_clean(plain_text(raw.get("title"), 500), 500),
            published_at=raw.get("published_at"),
            language=_clean(raw.get("language"), 16),
            author=_clean(plain_text(raw.get("author"), 255), 255),
            summary=summary,
            content_text=content,
            document_type=document_type,
            metadata={
                "provider": self.provider_name,
                "retention": "text_only_v1",
                "feed_url": None if source is None else source.base_url,
            },
        )

    async def health_check(self, source) -> bool:
        fetched = await self.http.get_text(source.base_url, user_agent=_agent(source))
        if fetched.error or fetched.status_code >= 400:
            return False
        lowered = fetched.text[:500].lower()
        return "<rss" in lowered or "<feed" in lowered

    async def _robots(self, feed_url: str, user_agent: str) -> tuple[bool, float | None]:
        parts = urlsplit(feed_url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        fetched = await self.http.get_text(robots_url, user_agent=user_agent)
        if fetched.error or fetched.status_code >= 400:
            return True, None
        return robots_policy(fetched.text, user_agent, feed_url)


def parse_feed(xml_text: str) -> list[dict]:
    root = ElementTree.fromstring(xml_text)
    language = _child_text(root, "language")
    items = []
    for element in root.iter():
        name = _local(element.tag)
        if name not in {"item", "entry"}:
            continue
        items.append(_item(element, language))
    return items


def _item(element: ElementTree.Element, feed_language: str | None) -> dict:
    title = _child_text(element, "title")
    external_id = _child_text(element, "guid") or _child_text(element, "id")
    url = _link(element)
    published = parse_datetime(
        _child_text(element, "published")
        or _child_text(element, "pubDate")
        or _child_text(element, "updated")
        or _child_text(element, "date")
    )
    summary = _child_text(element, "description") or _child_text(element, "summary")
    content = _child_text(element, "encoded") or _child_text(element, "content") or summary
    author = _child_text(element, "creator") or _child_text(element, "author") or _child_text(element, "name")
    return {
        "external_id": external_id,
        "url": url,
        "title": title,
        "published_at": published,
        "language": _child_text(element, "language") or feed_language,
        "author": author,
        "summary": summary,
        "content": content,
    }


def _link(element: ElementTree.Element) -> str | None:
    for child in list(element):
        if _local(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        rel = child.attrib.get("rel")
        if href and rel in {None, "alternate"}:
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    return None


def _child_text(element: ElementTree.Element, name: str) -> str | None:
    for child in list(element):
        if _local(child.tag) != name:
            continue
        if name == "author":
            nested = _child_text(child, "name")
            if nested:
                return nested
        if child.text and child.text.strip():
            return child.text.strip()
    return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _agent(source) -> str:
    marker = str((source.metadata_json or {}).get("user_agent") or "")
    if marker == "sec":
        return settings.sec_user_agent
    return settings.intelligence_user_agent


def _clean(value, limit: int):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
