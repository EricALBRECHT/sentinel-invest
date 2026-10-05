"""Candidate verification and promotion. A partial identity is never promoted by itself."""

from app.services.discovery_verification.matching import find_existing_company
from app.services.discovery_verification.promotion import promote_candidate, reject_candidate
from app.services.discovery_verification.verifier import select_due_candidate_ids, verify_candidate

__all__ = [
    "find_existing_company",
    "promote_candidate",
    "reject_candidate",
    "select_due_candidate_ids",
    "verify_candidate",
]
