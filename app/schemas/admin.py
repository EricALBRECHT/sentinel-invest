from datetime import datetime

from pydantic import BaseModel


class SystemStatus(BaseModel):
    api: str
    postgres: str
    redis: str


class DataCounts(BaseModel):
    companies: int | None
    financial_metrics: int | None
    quality_scores: int | None
    opportunity_scores: int | None
    users: int | None


class AnalysisCounts(BaseModel):
    opportunity_incomplete: int | None
    opportunity_partial: int | None
    opportunity_usable: int | None
    opportunity_complete: int | None
    ranking_eligible: int | None


class SecCounts(BaseModel):
    companies_with_cik: int | None
    companies_without_cik: int | None


class ServerStatus(BaseModel):
    started_at: datetime
    uptime_seconds: int


class JobQueueStatus(BaseModel):
    queued: int | None
    started: int | None
    finished_recent: int | None
    failed: int | None


class SyncSupervision(BaseModel):
    sec_due: int | None
    sec_last_success: datetime | None
    sec_failures: int | None


class UniverseCounts(BaseModel):
    active_companies: int | None
    discovered: int | None
    watched: int | None
    screened: int | None
    deep_analysis: int | None
    portfolio: int | None
    archived: int | None
    universes: dict[str, int] | None


class MarketCounts(BaseModel):
    companies_with_market_data: int | None
    companies_without_market_data: int | None
    last_market_sync: datetime | None
    market_jobs_due: int | None


class AdminStatusRead(BaseModel):
    system: SystemStatus
    data: DataCounts
    analysis: AnalysisCounts
    sec: SecCounts
    jobs: JobQueueStatus
    sync: SyncSupervision
    universe: UniverseCounts
    market: MarketCounts
    server: ServerStatus
