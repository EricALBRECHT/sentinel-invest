"""Pydantic schemas for AI document analysis."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CompanyRole = Literal["SUBJECT", "CUSTOMER", "SUPPLIER", "PARTNER", "COMPETITOR", "INVESTOR", "OTHER"]
EventType = Literal[
    "CONTRACT",
    "PARTNERSHIP",
    "ACQUISITION",
    "INVESTMENT",
    "NEW_FACTORY",
    "CAPACITY_EXPANSION",
    "PRODUCT_LAUNCH",
    "CUSTOMER_WIN",
    "SUPPLIER_CHANGE",
    "REGULATORY",
    "MANAGEMENT",
    "FINANCING",
    "LAYOFF",
    "CYBERSECURITY",
    "LEGAL",
    "OTHER",
]
RelationshipType = Literal[
    "CUSTOMER",
    "SUPPLIER",
    "PARTNER",
    "COMPETITOR",
    "INVESTOR",
    "SUBCONTRACTOR",
    "EQUIPMENT_PROVIDER",
    "MATERIAL_PROVIDER",
    "INFRASTRUCTURE_PROVIDER",
    "OTHER",
]
Importance = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
SignalImportance = Literal["LOW", "MEDIUM", "HIGH"]
AnalysisStatus = Literal["PENDING", "RUNNING", "SUCCESS", "INVALID_OUTPUT", "FAILED"]


class CompanyContextItem(BaseModel):
    company_id: int
    name: str
    ticker: str | None = None
    relation_type: str | None = None


class AiDocumentInput(BaseModel):
    document_id: int
    title: str | None = None
    published_at: str | None = None
    source_name: str
    source_type: str
    trust_level: str
    company_context: list[CompanyContextItem] = Field(default_factory=list)
    content_text: str
    content_truncated: bool = False
    original_char_count: int = 0
    analyzed_char_count: int = 0


class AiCompanyMention(BaseModel):
    company_id: int | None = None
    name: str
    ticker: str | None = None
    role: CompanyRole
    confidence: int = Field(ge=0, le=100)
    evidence: str = Field(min_length=1)


class AiEvent(BaseModel):
    type: EventType
    importance: Importance
    confidence: int = Field(ge=0, le=100)
    description: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class AiRelationship(BaseModel):
    source_company: str = Field(min_length=1)
    target_company: str = Field(min_length=1)
    type: RelationshipType
    confidence: int = Field(ge=0, le=100)
    evidence: str = Field(min_length=1)


class AiStrategicSignal(BaseModel):
    signal: str = Field(min_length=1)
    importance: SignalImportance
    confidence: int = Field(ge=0, le=100)
    evidence: str = Field(min_length=1)


class AiRisk(BaseModel):
    risk: str = Field(min_length=1)
    importance: Importance
    confidence: int = Field(ge=0, le=100)
    evidence: str = Field(min_length=1)


class AiDocumentResult(BaseModel):
    summary: str = Field(min_length=1)
    companies: list[AiCompanyMention] = Field(default_factory=list)
    events: list[AiEvent] = Field(default_factory=list)
    relationships: list[AiRelationship] = Field(default_factory=list)
    strategic_signals: list[AiStrategicSignal] = Field(default_factory=list)
    risks: list[AiRisk] = Field(default_factory=list)
    analysis_confidence: int = Field(ge=0, le=100)

    @field_validator("summary")
    @classmethod
    def summary_not_advice(cls, value: str) -> str:
        lowered = value.lower()
        for token in ("buy ", "sell ", "hold ", "price target"):
            if token in lowered:
                raise ValueError("Summary must not contain investment advice")
        return value


class AiDocumentAnalysisRead(BaseModel):
    id: int
    document_id: int
    model_name: str
    model_version: str | None
    prompt_version: str
    status: AnalysisStatus
    summary: str | None
    result: AiDocumentResult | None
    analysis_confidence: int | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    worker_name: str | None
    error_message: str | None
    runtime: dict | None = None
    created_at: datetime
    updated_at: datetime


class AiAdminStatusRead(BaseModel):
    provider: str
    model_name: str
    model_version: str | None
    prompt_version: str
    gpu_workers_online: int
    model_loaded_hint: str | None
    success_count: int
    failed_count: int
    invalid_output_count: int
    pending_or_running: int
    last_completed_at: datetime | None
    average_duration_ms: float | None
