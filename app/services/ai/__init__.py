"""AI document analysis services."""

from app.services.ai.prompts import PROMPT_VERSION
from app.services.ai.schemas import AiDocumentAnalysisRead, AiDocumentResult
from app.services.ai.service import (
    admin_ai_status,
    build_document_payload,
    effective_model_name,
    ensure_analysis_row,
    finalize_from_gpu_result,
    get_latest_analysis,
    mark_running,
    run_inference,
    serialize_analysis,
)

__all__ = [
    "PROMPT_VERSION",
    "AiDocumentAnalysisRead",
    "AiDocumentResult",
    "admin_ai_status",
    "build_document_payload",
    "effective_model_name",
    "ensure_analysis_row",
    "finalize_from_gpu_result",
    "get_latest_analysis",
    "mark_running",
    "run_inference",
    "serialize_analysis",
]
