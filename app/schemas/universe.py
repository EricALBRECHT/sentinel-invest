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
