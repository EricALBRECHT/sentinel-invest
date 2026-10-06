"""Company JSON must match the document-v1 schema without inventing fields."""

import ast
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _namespace(name: str, path: Path) -> None:
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    module.__package__ = name
    sys.modules[name] = module


# Load the validator without app.services.ai.__init__, which pulls the database.
_namespace("app", ROOT / "app")
_namespace("app.services", ROOT / "app" / "services")
_namespace("app.services.ai", ROOT / "app" / "services" / "ai")

from app.services.ai.validation import validate_ai_result  # noqa: E402

_VALID = {
    "summary": "The document says NVIDIA announced a partnership.",
    "companies": [
        {
            "company_id": 2,
            "name": "NVIDIA Corporation",
            "ticker": "NVDA",
            "role": "SUBJECT",
            "confidence": 90,
            "evidence": "NVIDIA announced a partnership",
        }
    ],
    "events": [],
    "relationships": [],
    "strategic_signals": [],
    "risks": [],
    "analysis_confidence": 80,
}


def test_relation_type_is_normalized_to_role(caplog):
    payload = json.loads(json.dumps(_VALID))
    payload["companies"][0].pop("role")
    payload["companies"][0]["relation_type"] = "SUBJECT"
    with caplog.at_level("INFO", logger="sentinel.ai.validation"):
        parsed, stored = validate_ai_result(json.dumps(payload))
    assert parsed.companies[0].role == "SUBJECT"
    assert stored["companies"][0]["role"] == "SUBJECT"
    assert "relation_type" not in stored["companies"][0]
    assert "normalized_fields=companies[0].relation_type" in caplog.text
    assert "validation_success" in caplog.text


def test_existing_role_is_not_overwritten():
    payload = json.loads(json.dumps(_VALID))
    payload["companies"][0]["relation_type"] = "PARTNER"
    parsed, stored = validate_ai_result(json.dumps(payload))
    assert parsed.companies[0].role == "SUBJECT"
    assert stored["companies"][0]["role"] == "SUBJECT"
    assert "relation_type" not in stored["companies"][0]


def test_missing_confidence_is_invalid_output(caplog):
    payload = json.loads(json.dumps(_VALID))
    payload["companies"][0].pop("confidence")
    with caplog.at_level("WARNING", logger="sentinel.ai.validation"):
        with pytest.raises(ValueError):
            validate_ai_result(json.dumps(payload))
    assert "validation_error" in caplog.text


def test_missing_evidence_is_invalid_output():
    payload = json.loads(json.dumps(_VALID))
    payload["companies"][0].pop("evidence")
    with pytest.raises(ValueError):
        validate_ai_result(json.dumps(payload))


def test_conforming_output_validates():
    parsed, stored = validate_ai_result(json.dumps(_VALID))
    assert parsed.analysis_confidence == 80
    company = stored["companies"][0]
    assert set(company) == {"company_id", "name", "ticker", "role", "confidence", "evidence"}
    assert company["confidence"] == 90
    assert company["evidence"] == "NVIDIA announced a partnership"


def test_analysis_confidence_scale_is_not_rescaled():
    payload = json.loads(json.dumps(_VALID))
    payload["analysis_confidence"] = 1
    parsed, _stored = validate_ai_result(json.dumps(payload))
    assert parsed.analysis_confidence == 1


def test_company_normalization_does_not_touch_financial_scores():
    forbidden = (
        "quality_score",
        "opportunity_score",
        "company_score",
        "financial_metric",
        "app.services.analysis",
        "app.api.routes.scores",
        "app.api.routes.financials",
    )
    for relative in (
        "app/services/ai/validation.py",
        "app/services/ai/prompts.py",
        "app/services/ai/schemas.py",
        "app/jobs/gpu/prompts.py",
    ):
        source = Path(relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        for module in modules:
            assert not any(token in module for token in forbidden)
        for token in forbidden:
            assert token not in source
