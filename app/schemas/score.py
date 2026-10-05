from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class QualityScoreRead(BaseModel):
    id: int
    company_id: int
    ticker: str
    score_date: date
    method_version: str
    quality_score: Decimal | None
    overall_confidence_score: int
    metrics: dict
    components: dict[str, Decimal | None]
    confidence: dict
    anomalies: list[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
