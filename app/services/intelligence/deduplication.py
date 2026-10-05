"""One article is stored once.

Identity is checked in this order:
1. source_id + external_id, when the provider gave an id
2. canonical URL, after tracking parameters are removed
3. SHA-256 of the normalized title and text
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.intelligence import ExternalDocument
from app.services.intelligence.providers.text import canonical_url, content_hash


async def find_duplicate(
    session: AsyncSession,
    *,
    source_id: int,
    external_id: str | None,
    url: str | None,
    title: str | None,
    content_text: str | None,
) -> ExternalDocument | None:
    identity = document_identity(external_id, url, title, content_text)
    if identity["external_id"]:
        row = await session.scalar(
            select(ExternalDocument).where(
                ExternalDocument.source_id == source_id,
                ExternalDocument.external_id == identity["external_id"],
            )
        )
        if row is not None:
            return row
    if identity["canonical_url"]:
        row = await session.scalar(
            select(ExternalDocument).where(ExternalDocument.canonical_url == identity["canonical_url"])
        )
        if row is not None:
            return row
    row = await session.scalar(
        select(ExternalDocument).where(ExternalDocument.content_hash == identity["content_hash"])
    )
    return row


def document_identity(external_id: str | None, url: str | None, title: str | None, content_text: str | None) -> dict:
    return {
        "external_id": external_id.strip() if external_id and external_id.strip() else None,
        "canonical_url": canonical_url(url),
        "content_hash": content_hash(title, content_text),
    }
