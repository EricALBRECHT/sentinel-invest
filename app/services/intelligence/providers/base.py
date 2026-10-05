"""Provider contract. Sentinel talks to this, not to one vendor."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class NormalizedDocument:
    external_id: str | None
    url: str | None
    title: str | None
    published_at: datetime | None
    language: str | None
    author: str | None
    summary: str | None
    content_text: str | None
    document_type: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FetchBatch:
    documents: list[NormalizedDocument]
    error: str | None = None


class ExternalContentProvider(Protocol):
    provider_name: str

    async def fetch_since(self, source, since: datetime) -> FetchBatch:
        """Return documents published on or after `since`."""

    def normalize_document(self, raw: dict) -> NormalizedDocument:
        """Map one provider payload into the common document shape."""

    async def health_check(self, source) -> bool:
        """Report whether the source endpoint answers."""
