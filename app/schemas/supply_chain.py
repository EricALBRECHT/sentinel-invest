from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RelationshipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_company_id: int
    target_company_id: int
    relationship_type: str
    direction: str
    confidence: int
    importance: str
    first_seen_at: datetime
    last_seen_at: datetime
    evidence_count: int
    status: str
    discovery_method: str


class DiscoveredCompanyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    canonical_name: str | None = None
    ticker: str | None = None
    isin: str | None = None
    country: str | None = None
    exchange: str | None = None
    website: str | None = None
    sec_cik: str | None = None
    entity_type: str = "UNKNOWN"
    verification_status: str = "UNVERIFIED"
    verification_confidence: int = 0
    verified_at: datetime | None = None
    verification_evidence_json: dict = Field(default_factory=dict)
    promoted_company_id: int | None = None
    discovered_from_company_id: int
    discovery_reason: str
    evidence_count: int
    confidence: int
    status: str


class PromotionRead(BaseModel):
    promoted: bool
    company_id: int | None
    relationships: int
    detail: str


class GraphNodeRead(BaseModel):
    company_id: int
    name: str
    ticker: str


class SupplyChainGraphRead(BaseModel):
    company_id: int
    depth: int
    nodes: list[GraphNodeRead]
    edges: list[RelationshipRead]
    candidates: list[DiscoveredCompanyRead]
