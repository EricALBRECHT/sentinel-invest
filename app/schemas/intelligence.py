from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class NewsItemRead(BaseModel):
    document_id: int
    source_id: int
    source_name: str
    title: str | None
    url: str | None
    canonical_url: str | None
    published_at: datetime | None
    document_type: str
    summary: str | None
    content_hash: str | None
    relation_type: str
    match_method: str
    confidence: int


class CompanyEventRead(BaseModel):
    id: int
    event_type: str
    event_date: date | None
    title: str
    description: str | None
    importance: str
    confidence: int
    source_document_id: int | None
    role: str
    role_confidence: int


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    external_id: str | None
    url: str | None
    canonical_url: str | None
    title: str | None
    published_at: datetime | None
    fetched_at: datetime
    language: str | None
    author: str | None
    summary: str | None
    content_text: str | None
    content_hash: str | None
    document_type: str
    metadata_json: dict


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    event_date: date | None
    title: str
    description: str | None
    importance: str
    confidence: int
    source_document_id: int | None
    created_at: datetime
    updated_at: datetime
