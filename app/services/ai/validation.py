"""Validate model output before persistence."""

import json
import logging
import re

from pydantic import ValidationError

from app.services.ai.schemas import AiDocumentResult

logger = logging.getLogger("sentinel.ai.validation")

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_COMPANY_ALIASES = ("relation_type",)
# Bare "partner(s)" is too weak ("manufacturer partners" ≠ company role PARTNER).
_PARTNER_EVIDENCE = (
    "partnership",
    "partnered",
    "strategic partner",
    "alliance",
    "co-engineer",
    "coengineering",
    "collaborate",
    "collaboration",
    "joint venture",
)


def extract_json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Empty model output")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(text)
        if match is None:
            raise ValueError("Model output is not valid JSON") from None
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("Model output must be a JSON object")
    return payload


def normalize_company_roles(payload: dict) -> list[str]:
    """Map relation_type to role only when role is missing. Do not invent other fields."""
    companies = payload.get("companies")
    if not isinstance(companies, list):
        return []
    normalized: list[str] = []
    for index, company in enumerate(companies):
        if not isinstance(company, dict):
            continue
        role = company.get("role")
        role_missing = role is None or (isinstance(role, str) and not role.strip())
        relation = company.get("relation_type")
        if role_missing and isinstance(relation, str) and relation.strip():
            company["role"] = relation.strip()
            normalized.append(f"companies[{index}].relation_type")
        for alias in _COMPANY_ALIASES:
            company.pop(alias, None)
    return normalized


def enforce_partner_evidence(payload: dict) -> None:
    """PARTNER is allowed only when evidence explicitly supports a partnership."""
    companies = payload.get("companies")
    if not isinstance(companies, list):
        return
    for index, company in enumerate(companies):
        if not isinstance(company, dict):
            continue
        role = str(company.get("role") or "").strip().upper()
        if role != "PARTNER":
            continue
        evidence = str(company.get("evidence") or "").casefold()
        if any(hint in evidence for hint in _PARTNER_EVIDENCE):
            continue
        raise ValueError(
            f"companies[{index}].role=PARTNER requires explicit partnership evidence in evidence"
        )


def validate_ai_result(raw: str) -> tuple[AiDocumentResult, dict]:
    payload = extract_json_object(raw)
    normalized_fields = normalize_company_roles(payload)
    logger.info("normalized_fields=%s", ",".join(normalized_fields))
    enforce_partner_evidence(payload)
    try:
        parsed = AiDocumentResult.model_validate(payload)
    except ValidationError as exc:
        logger.warning("validation_error error=%s", exc)
        raise ValueError(str(exc)) from exc
    logger.info("validation_success")
    return parsed, parsed.model_dump()
