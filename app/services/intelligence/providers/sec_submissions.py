"""Recent SEC filings from the official submissions JSON.

The browse-edgar atom feed lives under /cgi-bin, which sec.gov disallows.
data.sec.gov publishes the same recent-filing list. Only filings inside the
requested window are turned into documents. The filing HTML is not downloaded.
"""

from datetime import datetime, timezone
import json
import logging

from app.core.config import settings
from app.services.intelligence.providers.base import FetchBatch, NormalizedDocument
from app.services.intelligence.providers.http import PoliteClient
from app.services.intelligence.providers.text import parse_datetime

logger = logging.getLogger("sentinel.intelligence")


class SecSubmissionsProvider:
    provider_name = "sec_submissions"

    def __init__(self, http: PoliteClient | None = None) -> None:
        self.http = http or PoliteClient(user_agent=settings.sec_user_agent, min_interval_seconds=settings.sec_min_interval_seconds)

    async def fetch_since(self, source, since: datetime) -> FetchBatch:
        cik = str((source.metadata_json or {}).get("cik") or "").strip()
        if not cik.isdigit():
            return FetchBatch([], "SEC source has no numeric CIK")
        url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
        fetched = await self.http.get_text(url, user_agent=settings.sec_user_agent)
        if fetched.error:
            return FetchBatch([], fetched.error)
        try:
            payload = json.loads(fetched.text)
        except json.JSONDecodeError:
            return FetchBatch([], "SEC submissions response was not valid JSON")
        recent = ((payload.get("filings") or {}).get("recent") or {})
        forms = recent.get("form") or []
        documents = []
        for index, form in enumerate(forms):
            raw = _row(payload, recent, index, form)
            published = raw["published_at"]
            if published is not None and published < since:
                continue
            documents.append(self.normalize_document(raw, source))
        return FetchBatch(documents)

    def normalize_document(self, raw: dict, source=None) -> NormalizedDocument:
        return NormalizedDocument(
            external_id=raw.get("external_id"),
            url=raw.get("url"),
            title=raw.get("title"),
            published_at=raw.get("published_at"),
            language="en",
            author=raw.get("author"),
            summary=raw.get("summary"),
            content_text=raw.get("content"),
            document_type="SEC_FILING",
            metadata={
                "provider": self.provider_name,
                "retention": "text_only_v1",
                "form": raw.get("form"),
                "accession": raw.get("external_id"),
                "html_stored": False,
            },
        )

    async def health_check(self, source) -> bool:
        cik = str((source.metadata_json or {}).get("cik") or "")
        if not cik.isdigit():
            return False
        url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
        fetched = await self.http.get_text(url, user_agent=settings.sec_user_agent)
        return fetched.error is None and '"filings"' in fetched.text


def _row(payload: dict, recent: dict, index: int, form: str) -> dict:
    accession = _at(recent, "accessionNumber", index) or ""
    primary = _at(recent, "primaryDocument", index) or ""
    filed = _at(recent, "filingDate", index)
    accepted = parse_datetime(_at(recent, "acceptanceDateTime", index))
    if accepted is None and filed:
        accepted = parse_datetime(f"{filed}T00:00:00+00:00")
    cik_int = str(payload.get("cik") or "").lstrip("0")
    compact = accession.replace("-", "")
    url = None
    if cik_int and compact:
        url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{compact}/{primary}".rstrip("/")
    name = payload.get("name") or "The company"
    description = _at(recent, "primaryDocDescription", index) or form
    summary = f"{name} filed {form} on {filed or 'an unknown date'}. {description}."
    return {
        "external_id": accession or None,
        "url": url,
        "title": f"{form} {description}".strip()[:500],
        "published_at": accepted,
        "author": name,
        "summary": summary[:500],
        "content": summary[:2000],
        "form": form,
    }


def _at(recent: dict, key: str, index: int):
    values = recent.get(key) or []
    if index >= len(values):
        return None
    value = values[index]
    if value is None or value == "":
        return None
    return str(value)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
