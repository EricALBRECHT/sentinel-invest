from datetime import datetime

from pydantic import BaseModel


class DiscoveryStatusRead(BaseModel):
    company_id: int
    universe_status: str
    discovery_pipeline_status: str
    discovery_depth: int
    discovered_parent_company_id: int | None
    last_discovery_collection_at: datetime | None
    last_relationship_processing_at: datetime | None
    market_symbol: str | None
    sec_sync_eligible: bool
    sources: int
    max_depth: int


class DiscoveryGraphSummaryRead(BaseModel):
    companies_by_depth: dict[str, int]
    open_candidates: int
    relationships: int
