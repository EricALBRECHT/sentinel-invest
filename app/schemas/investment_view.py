from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class InvestmentViewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company_id: int
    as_of_date: date
    method: str
    quality_score: Decimal | None
    quality_confidence: int | None
    opportunity_score: Decimal | None
    opportunity_confidence: int | None
    opportunity_coverage: int | None
    opportunity_coverage_status: str | None
    opportunity_ranking_eligible: bool | None
    technical_score: Decimal | None
    technical_confidence: int | None
    long_term_conviction_score: Decimal | None
    entry_attractiveness_score: Decimal | None
    analysis_readiness_score: Decimal
    long_term_conviction_label: str | None
    entry_attractiveness_label: str | None
    analysis_readiness_label: str
    components_json: dict
    created_at: datetime
    updated_at: datetime
