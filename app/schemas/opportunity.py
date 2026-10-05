from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.company import CompanyRead
from app.services.analysis.opportunity_thresholds import MEGATRENDS, STRATEGIC_ROLES

SourceName = Literal[
    "AUTO_FINANCIAL",
    "DOCUMENT_EXTRACTED",
    "MANUAL_STRUCTURED",
    "ESTIMATED",
    "UNKNOWN",
]


def _score():
    return Field(default=None, ge=0, le=100)


class MegatrendWrite(BaseModel):
    trend: str
    exposure_score: Decimal = Field(ge=0, le=100)
    confidence: int = Field(ge=0, le=100)
    evidence: str | None = Field(default=None, max_length=2000)
    source: SourceName = "MANUAL_STRUCTURED"

    @field_validator("trend")
    @classmethod
    def known_trend(cls, value: str) -> str:
        if value not in MEGATRENDS:
            raise ValueError(f"Unknown megatrend '{value}'")
        return value


class CustomerWrite(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    revenue_share: Decimal | None = Field(default=None, ge=0, le=100)
    major: bool = False


class SupplierWrite(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    role: str | None = Field(default=None, max_length=255)


class EvidenceWrite(BaseModel):
    component: str | None = Field(default=None, max_length=64)
    text: str = Field(min_length=1, max_length=2000)
    source: SourceName = "MANUAL_STRUCTURED"


class OpportunityProfileWrite(BaseModel):
    market_growth_score: Decimal | None = _score()
    market_size_score: Decimal | None = _score()
    market_penetration_score: Decimal | None = _score()
    strategic_position_score: Decimal | None = _score()
    bottleneck_score: Decimal | None = _score()
    supplier_leverage_score: Decimal | None = _score()
    customer_diversification_score: Decimal | None = _score()
    innovation_score: Decimal | None = _score()
    rd_intensity_score: Decimal | None = _score()
    capacity_expansion_score: Decimal | None = _score()
    geographic_expansion_score: Decimal | None = _score()
    competitive_moat_score: Decimal | None = _score()
    competition_risk_score: Decimal | None = _score()
    megatrends_json: list[MegatrendWrite] | None = None
    strategic_roles_json: list[str] | None = None
    customers_json: list[CustomerWrite] | None = None
    suppliers_json: list[SupplierWrite] | None = None
    evidence_json: list[EvidenceWrite] | None = None
    data_confidence: int | None = Field(default=None, ge=0, le=100)
    analysis_date: date | None = None

    @field_validator("strategic_roles_json")
    @classmethod
    def known_roles(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = [role for role in value if role not in STRATEGIC_ROLES]
        if unknown:
            raise ValueError(f"Unknown strategic role '{unknown[0]}'")
        if len(value) != len(set(value)):
            raise ValueError("Each strategic role can be recorded once")
        return value

    @model_validator(mode="after")
    def unique_megatrends(self) -> "OpportunityProfileWrite":
        if self.megatrends_json:
            names = [item.trend for item in self.megatrends_json]
            if len(names) != len(set(names)):
                raise ValueError("Each megatrend can be recorded once")
        return self


class OpportunityProfileRead(BaseModel):
    id: int
    company_id: int
    ticker: str
    megatrend_score: Decimal | None
    market_growth_score: Decimal | None
    market_size_score: Decimal | None
    market_penetration_score: Decimal | None
    strategic_position_score: Decimal | None
    bottleneck_score: Decimal | None
    supplier_leverage_score: Decimal | None
    customer_diversification_score: Decimal | None
    innovation_score: Decimal | None
    rd_intensity_score: Decimal | None
    capacity_expansion_score: Decimal | None
    geographic_expansion_score: Decimal | None
    size_runway_score: Decimal | None
    competitive_moat_score: Decimal | None
    competition_risk_score: Decimal | None
    megatrends_json: list | None
    strategic_roles_json: list | None
    customers_json: list | None
    suppliers_json: list | None
    evidence_json: list | None
    data_confidence: int | None
    analysis_date: date | None
    method_version: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OpportunityComponentRead(BaseModel):
    score: Decimal | None
    weight: Decimal
    points: Decimal | None
    confidence: int | None
    source: str | None
    evidence: str | None
    included: bool


class OpportunityScoreRead(BaseModel):
    id: int
    company_id: int
    ticker: str
    score_date: date
    method_version: str
    opportunity_score: Decimal | None
    opportunity_confidence_score: int
    coverage_score: int
    coverage_status: str
    ranking_eligible: bool
    components: dict[str, OpportunityComponentRead]
    available_weight: Decimal
    excluded: list[str]
    revenue_growth_acceleration: Decimal | None
    notes: list[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScoreSummary(BaseModel):
    score: Decimal | None
    confidence: int | None
    method_version: str | None


class OpportunitySummary(BaseModel):
    score: Decimal | None
    confidence: int | None
    coverage: int | None
    status: str | None
    ranking_eligible: bool | None
    method_version: str | None


class AnalysisSummaryRead(BaseModel):
    company: CompanyRead
    quality: ScoreSummary
    opportunity: OpportunitySummary
