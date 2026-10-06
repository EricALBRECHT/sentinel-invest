"""Polite HTTP helpers for public index membership sources."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings

logger = logging.getLogger("sentinel.universe")
_RETRYABLE = frozenset({429, 500, 502, 503, 504})
_WIKI_API = "https://en.wikipedia.org/w/api.php"


class PublicHttpClient:
    """Shared client: User-Agent, timeout, retries, and a minimum request interval."""

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        min_interval_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._max_retries = max(1, settings.universe_max_retries if max_retries is None else max_retries)
        self._min_interval = max(
            0.0,
            settings.universe_min_interval_seconds if min_interval_seconds is None else min_interval_seconds,
        )
        self._next_request = 0.0
        self._pace = asyncio.Lock()
        self._client = httpx.AsyncClient(
            timeout=settings.universe_timeout_seconds if timeout is None else timeout,
            headers={
                "User-Agent": user_agent or settings.universe_user_agent,
                "Accept": "*/*",
            },
            follow_redirects=True,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_text(self, url: str, *, headers: dict[str, str] | None = None) -> str:
        response = await self._request("GET", url, headers=headers)
        return response.text

    async def get_json(self, url: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        response = await self._request("GET", url, params=params, headers={"Accept": "application/json"})
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Expected JSON object from {url}")
        return payload

    async def get_csv_rows(self, url: str) -> list[dict[str, str]]:
        text = await self.get_text(url, headers={"Accept": "text/csv,text/plain,*/*"})
        reader = csv.DictReader(io.StringIO(text))
        rows: list[dict[str, str]] = []
        for raw in reader:
            item = {str(key).strip(): (value or "").strip() for key, value in raw.items() if key}
            if any(item.values()):
                rows.append(item)
        return rows

    async def parse_wikipedia_html(self, page: str) -> str:
        """MediaWiki action=parse helper kept for optional Wikipedia-backed sources."""
        payload = await self.get_json(
            _WIKI_API,
            params={
                "action": "parse",
                "page": page,
                "prop": "text",
                "format": "json",
                "redirects": "1",
            },
        )
        parse = payload.get("parse") if isinstance(payload, dict) else None
        text = parse.get("text") if isinstance(parse, dict) else None
        html = text.get("*") if isinstance(text, dict) else None
        if not isinstance(html, str) or not html.strip():
            raise RuntimeError(f"Wikipedia page {page} returned no HTML")
        return html

    # Alias used by older tests / Wikipedia-oriented call sites.
    async def parse_html(self, page: str) -> str:
        return await self.parse_wikipedia_html(page)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            await self._wait()
            try:
                response = await self._client.request(method, url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
                await asyncio.sleep(min(8.0, 0.5 * attempt))
                continue
            if response.status_code in _RETRYABLE:
                last_error = RuntimeError(f"HTTP {response.status_code} for {url}")
                retry_after = response.headers.get("Retry-After")
                delay = min(30.0, float(retry_after)) if retry_after and retry_after.isdigit() else min(8.0, 0.5 * attempt)
                await asyncio.sleep(delay)
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code} for {url}")
            return response
        assert last_error is not None
        raise last_error

    async def _wait(self) -> None:
        async with self._pace:
            delay = self._next_request - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_request = time.monotonic() + self._min_interval


# Backward-compatible alias used by existing offline tests.
WikipediaClient = PublicHttpClient


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            self._skip_depth += 1
            return
        if tag in {"script", "style", "sup"}:
            self._skip_depth = 1
            return
        if tag == "table":
            if self._table is not None:
                self.tables.append(self._table)
            self._table = []
            return
        if self._table is None:
            return
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")
        elif tag == "a" and self._cell is not None:
            title = dict(attrs).get("title")
            if title and not self._cell:
                self._cell.append(title)

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(cell.strip() for cell in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._cell is None:
            return
        self._cell.append(data)


def html_tables(html: str) -> list[list[list[str]]]:
    parser = _TableParser()
    parser.feed(html)
    parser.close()
    if parser._table is not None:
        parser.tables.append(parser._table)
    return parser.tables


def table_dicts(table: list[list[str]]) -> list[dict[str, str]]:
    if not table:
        return []
    headers = [_header(cell) for cell in table[0]]
    rows: list[dict[str, str]] = []
    for raw in table[1:]:
        item: dict[str, str] = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            item[header] = raw[index].strip() if index < len(raw) else ""
        if any(item.values()):
            rows.append(item)
    return rows


def find_table(tables: list[list[list[str]]], required: set[str]) -> list[dict[str, str]]:
    for table in tables:
        rows = table_dicts(table)
        if not rows:
            continue
        keys = set(rows[0])
        if required.issubset(keys):
            return rows
    raise RuntimeError(f"HTML table missing columns {sorted(required)}")


def _header(value: str) -> str:
    text = " ".join(value.lower().replace("\xa0", " ").split())
    aliases = {
        "symbol": "ticker",
        "ticker symbol": "ticker",
        "ticker": "ticker",
        "security": "name",
        "company": "name",
        "company name": "name",
        "gics sector": "sector",
        "sector": "sector",
        "gics sub-industry": "industry",
        "gics sub industry": "industry",
        "sub-industry": "industry",
        "industry": "industry",
        "cik": "cik",
        "headquarters location": "headquarters",
        "date added": "date_added",
        "founded": "founded",
        "no.": "rank",
        "#": "rank",
        "weight": "weight",
    }
    return aliases.get(text, text)


def wikipedia_page_url(page: str) -> str:
    return f"https://en.wikipedia.org/wiki/{quote(page.replace(' ', '_'))}"
