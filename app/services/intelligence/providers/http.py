"""Polite HTTP for public feeds. One request at a time, with a named User-Agent."""

from dataclasses import dataclass
from urllib.parse import urlsplit
import asyncio
import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger("sentinel.intelligence")

MAX_BODY_CHARS = 5_000_000
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class HttpText:
    status_code: int
    text: str
    url: str
    error: str | None = None


class PoliteClient:
    """Shared rate limit. Tests can pass an httpx client and skip the network."""

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        min_interval_seconds: float | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.user_agent = (user_agent or settings.intelligence_user_agent).strip()
        self.timeout = settings.intelligence_timeout_seconds if timeout is None else timeout
        self.max_retries = settings.intelligence_max_retries if max_retries is None else max_retries
        self.min_interval_seconds = (
            settings.intelligence_min_interval_seconds if min_interval_seconds is None else min_interval_seconds
        )
        self._http = http_client
        self._owns = http_client is None
        self._lock = asyncio.Lock()
        self._last_at = 0.0
        self._crawl_delay: dict[str, float] = {}

    async def aclose(self) -> None:
        if self._owns and self._http is not None:
            await self._http.aclose()
            self._http = None

    def set_crawl_delay(self, url: str, seconds: float | None) -> None:
        if seconds is None:
            return
        host = urlsplit(url).netloc.lower()
        self._crawl_delay[host] = max(self._crawl_delay.get(host, 0.0), seconds)

    async def get_text(self, url: str, *, user_agent: str | None = None) -> HttpText:
        agent = (user_agent or self.user_agent).strip()
        if not agent:
            return HttpText(0, "", url, "User-Agent is required")
        last_error = "request failed"
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self._send(url, agent)
            except httpx.TimeoutException:
                last_error = "timeout"
                logger.warning("intelligence_timeout host=%s attempt=%s", urlsplit(url).netloc, attempt)
            except httpx.HTTPError as exc:
                last_error = type(exc).__name__
                logger.warning(
                    "intelligence_http_error host=%s attempt=%s error_type=%s",
                    urlsplit(url).netloc,
                    attempt,
                    type(exc).__name__,
                )
            else:
                if response.status_code in RETRY_STATUS and attempt < self.max_retries:
                    await asyncio.sleep(min(8.0, 0.5 * attempt))
                    continue
                text = response.text if len(response.text) <= MAX_BODY_CHARS else response.text[:MAX_BODY_CHARS]
                error = None if response.status_code < 400 else f"HTTP {response.status_code}"
                return HttpText(response.status_code, text, str(response.url), error)
        return HttpText(0, "", url, last_error)

    async def _send(self, url: str, user_agent: str) -> httpx.Response:
        async with self._lock:
            host = urlsplit(url).netloc.lower()
            wait = max(self.min_interval_seconds, self._crawl_delay.get(host, 0.0))
            elapsed = time.monotonic() - self._last_at
            if self._last_at and elapsed < wait:
                await asyncio.sleep(wait - elapsed)
            if self._http is None:
                self._http = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
            response = await self._http.get(url, headers={"User-Agent": user_agent, "Accept": "application/xml, application/json, text/plain"})
            self._last_at = time.monotonic()
            return response
