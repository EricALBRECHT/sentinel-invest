"""HTTP client for the SEC EDGAR companyfacts API.

SEC asks callers to send a descriptive User-Agent with a contact email and
to stay under 10 requests per second. This client sends one request at a
time, waits between calls, and retries only transient failures.
"""

import asyncio
import json
import logging
import time
from decimal import Decimal

import httpx

from app.services.sec.cik import cik_for_companyfacts
from app.services.sec.errors import SecClientError, SecCompanyFactsNotFound

logger = logging.getLogger("sentinel.sec")

COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class SecClient:
    def __init__(
        self,
        user_agent: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        min_interval_seconds: float = 0.2,
        backoff_seconds: float = 0.5,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not user_agent or not user_agent.strip():
            raise ValueError("SEC_USER_AGENT is required")
        self._user_agent = user_agent.strip()
        self._timeout = timeout
        self._max_retries = max_retries
        self._min_interval_seconds = min_interval_seconds
        self._backoff_seconds = backoff_seconds
        self._http_client = http_client
        self._owns_client = http_client is None
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def aclose(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def get_company_facts(self, cik: str) -> dict:
        url = COMPANY_FACTS_URL.format(cik=cik_for_companyfacts(cik))
        response = await self._get(url)
        try:
            payload = json.loads(response.text, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise SecClientError("SEC companyfacts response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise SecClientError("SEC companyfacts response had an unexpected shape")
        return payload

    async def _get(self, url: str) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await self._send(url)
            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning("sec_request_timeout url=%s attempt=%s", url, attempt)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("sec_request_error url=%s attempt=%s error=%s", url, attempt, exc)
            else:
                if response.status_code == 404:
                    raise SecCompanyFactsNotFound(
                        "No SEC company facts for this CIK",
                        status_code=404,
                    )
                if response.status_code in RETRY_STATUS_CODES and attempt < self._max_retries:
                    logger.warning(
                        "sec_request_retry url=%s status=%s attempt=%s",
                        url,
                        response.status_code,
                        attempt,
                    )
                    await asyncio.sleep(self._backoff_seconds * (2 ** (attempt - 1)))
                    continue
                if response.status_code >= 400:
                    raise SecClientError(
                        f"SEC EDGAR request failed with status {response.status_code}",
                        status_code=response.status_code,
                    )
                return response
            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff_seconds * (2 ** (attempt - 1)))
        raise SecClientError("SEC EDGAR request failed") from last_error

    async def _send(self, url: str) -> httpx.Response:
        async with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            wait = self._min_interval_seconds - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()
            client = self._client()
            return await client.get(url, headers=self._headers())

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
        return self._http_client

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }
