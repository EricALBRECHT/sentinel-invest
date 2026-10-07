from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UniverseMembershipRead(BaseModel):
    id: int
    company_id: int
    universe_name: str
    source: str
    added_at: datetime
    removed_at: datetime | None
    is_active: bool
    metadata_json: dict | None

    model_config = ConfigDict(from_attributes=True)


class UniverseAdd(BaseModel):
    universe_name: str = Field(min_length=1, max_length=32)
    source: str = Field(min_length=1, max_length=32)
    universe_status: str | None = Field(default=None, max_length=32)
    discovery_reason: str | None = Field(default=None, max_length=500)
    metadata_json: dict | None = None


class UniverseCompanyRead(BaseModel):
    id: int
    ticker: str
    name: str
    universe_status: str
    universe_priority: int
    discovery_source: str | None
    is_active: bool
    pea_eligible: bool
    memberships: list[UniverseMembershipRead]


class UniverseListRead(BaseModel):
    items: list[UniverseCompanyRead]
    limit: int
    offset: int
    total: int


class PriorityRead(BaseModel):
    company_id: int
    ticker: str
    universe_priority: int
    parts: dict[str, int]


class UniverseImportRead(BaseModel):
    rows: int
    created_companies: int
    existing_companies: int
    memberships_added: int
    memberships_existing: int


class UniverseIndexRefreshRead(BaseModel):
    universe: str
    provider: str
    source_documentation: str
    fetched: int
    created_companies: int
    updated_companies: int = 0
    existing_companies: int
    skipped_companies: int = 0
    conflict_companies: int = 0
    memberships_added: int
    memberships_existing: int
    memberships_removed: int
    multi_universe_companies: int
    multi_universe_delta: int
    last_refresh: str


class UniverseBootstrapRead(BaseModel):
    selected: int
    market_enqueued: int
    sec_enqueued: int
    already_active: int
    job_ids: list[str]
    limit: int
    last_bootstrap: str


class UniverseIndexStatusRead(BaseModel):
    members: int
    last_refresh: str | None


class UniverseBootstrapStatusRead(BaseModel):
    pending_market: int
    pending_sec: int
    ready: int
    last_bootstrap: str | None = None
    max_per_run: int | None = None


class UniverseMarketStatusRead(BaseModel):
    SP500: UniverseIndexStatusRead
    NASDAQ100: UniverseIndexStatusRead
    bootstrap: UniverseBootstrapStatusRead
