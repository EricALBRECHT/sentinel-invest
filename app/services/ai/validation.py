"""Validate model output before persistence."""

import json
import re

from pydantic import ValidationError

from app.services.ai.schemas import AiDocumentResult

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


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


def validate_ai_result(raw: str) -> tuple[AiDocumentResult, dict]:
    payload = extract_json_object(raw)
    try:
        parsed = AiDocumentResult.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    return parsed, payload
