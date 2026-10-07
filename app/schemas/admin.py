from datetime import date, datetime

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
    failed_total: int | None = None


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


class TechnicalCounts(BaseModel):
    companies_scored: int | None
    companies_without_score: int | None
    last_calculation: datetime | None
    technical_snapshots_count: int | None
    oldest_technical_date: date | None
    latest_technical_date: date | None


class InvestmentViewCounts(BaseModel):
    companies_with_view: int | None
    last_calculation: datetime | None


class IntelligenceCounts(BaseModel):
    sources_active: int | None
    documents_total: int | None
    documents_last_24h: int | None
    events_total: int | None
    events_last_24h: int | None
    last_success: datetime | None
    failed_sources: int | None


class SupplyChainCounts(BaseModel):
    relationships_total: int | None
    confirmed_relationships: int | None
    candidate_relationships: int | None
    discovered_companies: int | None
    last_processing: datetime | None


class DiscoveryVerificationCounts(BaseModel):
    unverified: int | None
    partial: int | None
    verified: int | None
    promoted: int | None
    rejected: int | None
    last_verification: datetime | None


class DiscoveryExpansionCounts(BaseModel):
    ready: int | None
    collecting: int | None
    analyzed: int | None
    blocked: int | None
    max_depth: int | None
    deepest_company: str | None
    last_expansion: datetime | None


class AdminStatusRead(BaseModel):
    system: SystemStatus
    data: DataCounts
    analysis: AnalysisCounts
    sec: SecCounts
    jobs: JobQueueStatus
    sync: SyncSupervision
    universe: UniverseCounts
    market: MarketCounts
    technical: TechnicalCounts
    investment_view: InvestmentViewCounts
    intelligence: IntelligenceCounts
    supply_chain: SupplyChainCounts
    discovery_verification: DiscoveryVerificationCounts
    discovery_expansion: DiscoveryExpansionCounts
    server: ServerStatus
