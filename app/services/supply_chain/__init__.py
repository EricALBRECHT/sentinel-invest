"""Supply-chain discovery. Relationships are directed and rule-backed. Scores stay untouched."""

from app.services.supply_chain.discovery import discover_candidates
from app.services.supply_chain.extraction import extract_relationships, process_company_documents
from app.services.supply_chain.graph import get_company_graph
from app.services.supply_chain.relationships import add_evidence, upsert_relationship

__all__ = [
    "add_evidence",
    "discover_candidates",
    "extract_relationships",
    "get_company_graph",
    "process_company_documents",
    "upsert_relationship",
]
