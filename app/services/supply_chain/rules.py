"""Explainable relationship patterns.

Each rule names the source, the target, and why the confidence is what it is.
A co-mention such as "NVIDIA and CoreWeave" is not a rule. A single vague
mention is not stored.
"""

from dataclasses import dataclass
import re

from app.services.intelligence.company_matching import AMBIGUOUS_TOKENS


@dataclass(frozen=True)
class CatalogEntity:
    names: tuple[str, ...]
    ticker: str | None
    label: str


# Distinctive names a document may introduce before they exist as companies.
# This is a configured list, not a general named-entity scan.
CATALOG: tuple[CatalogEntity, ...] = (
    CatalogEntity(("CoreWeave",), None, "CoreWeave"),
    CatalogEntity(("OpenAI",), None, "OpenAI"),
    CatalogEntity(("Acer",), None, "Acer"),
    CatalogEntity(("TSMC", "Taiwan Semiconductor Manufacturing Company"), "TSM", "TSMC"),
    CatalogEntity(("ASML",), "ASML", "ASML"),
)


@dataclass(frozen=True)
class Entity:
    label: str
    names: tuple[str, ...]
    company_id: int | None
    ticker: str | None


@dataclass(frozen=True)
class RelationRule:
    name: str
    relationship_type: str
    template: str
    source_slot: str
    target_slot: str
    confidence: int
    importance: str


@dataclass(frozen=True)
class RelationHit:
    source: Entity
    target: Entity
    relationship_type: str
    confidence: int
    importance: str
    rule_name: str
    excerpt: str


# {left} is the first slot and {right} the second. Source and target say which
# slot plays the relationship. More specific rules come first.
RULES: tuple[RelationRule, ...] = (
    RelationRule(
        name="partners_with",
        relationship_type="PARTNER",
        template=r"{left} partners with {right}",
        source_slot="left",
        target_slot="right",
        confidence=90,
        importance="HIGH",
    ),
    RelationRule(
        name="co_engineering",
        relationship_type="PARTNER",
        template=r"co-engineering[\s\S]{{0,120}}{left}[\s\S]{{0,80}}{right}",
        source_slot="left",
        target_slot="right",
        confidence=90,
        importance="HIGH",
    ),
    RelationRule(
        name="manufacturer_partners",
        relationship_type="PARTNER",
        template=r"{left}[\s\S]{{0,220}}manufacturer partners?\s*[—–\-]\s*{right}",
        source_slot="left",
        target_slot="right",
        confidence=90,
        importance="HIGH",
    ),
    RelationRule(
        name="manufactures_for",
        relationship_type="SUPPLIER",
        template=r"{left} manufactures(?:\s+\w+){{0,4}}\s+for {right}",
        source_slot="left",
        target_slot="right",
        confidence=100,
        importance="HIGH",
    ),
    RelationRule(
        name="supplies_lithography",
        relationship_type="EQUIPMENT_PROVIDER",
        template=r"{left} supplies lithography(?:\s+\w+){{0,4}}\s+to {right}",
        source_slot="left",
        target_slot="right",
        confidence=100,
        importance="HIGH",
    ),
    RelationRule(
        name="supplies_to",
        relationship_type="SUPPLIER",
        template=r"{left} supplies (?!lithography)(?:\w+\s+){{0,6}}to {right}",
        source_slot="left",
        target_slot="right",
        confidence=90,
        importance="HIGH",
    ),
    RelationRule(
        name="uses_supplier",
        relationship_type="SUPPLIER",
        template=r"{right} uses {left}",
        source_slot="left",
        target_slot="right",
        confidence=90,
        importance="HIGH",
    ),
    RelationRule(
        name="selected_platform",
        relationship_type="CUSTOMER",
        template=r"{left} selected {right}(?:'s|’s)? platform",
        source_slot="left",
        target_slot="right",
        confidence=90,
        importance="MEDIUM",
    ),
    RelationRule(
        name="running_on",
        relationship_type="CUSTOMER",
        template=r"running on {right}[\s\S]{{0,180}}{left}",
        source_slot="left",
        target_slot="right",
        confidence=75,
        importance="MEDIUM",
    ),
    RelationRule(
        name="built_compute",
        relationship_type="INFRASTRUCTURE_PROVIDER",
        template=r"{right} has built {left} compute",
        source_slot="left",
        target_slot="right",
        confidence=75,
        importance="MEDIUM",
    ),
)

_IMPORTANCE_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
_EXCLUSIVE = re.compile(r"\b(exclusive|sole supplier|only supplier|depends on|dependency)\b", re.IGNORECASE)
_AMOUNT = re.compile(r"\$\s?\d|\bbillion\b", re.IGNORECASE)


def contains_phrase(text: str, phrase: str) -> bool:
    if not phrase or phrase.casefold() in AMBIGUOUS_TOKENS:
        return False
    if len(phrase) <= 3 and phrase.isalpha():
        return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, flags=re.IGNORECASE) is not None


def find_relationships(text: str, entities: list[Entity]) -> list[RelationHit]:
    if not text or not text.strip():
        return []
    present = [entity for entity in entities if any(contains_phrase(text, name) for name in entity.names)]
    found: dict[tuple, RelationHit] = {}
    for left in present:
        for right in present:
            if left.label.casefold() == right.label.casefold():
                continue
            if left.company_id is not None and left.company_id == right.company_id:
                continue
            for rule in RULES:
                hit = _apply(text, rule, left, right)
                if hit is None:
                    continue
                key = (
                    hit.source.company_id or hit.source.label.casefold(),
                    hit.target.company_id or hit.target.label.casefold(),
                    hit.relationship_type,
                )
                current = found.get(key)
                if current is None or hit.confidence > current.confidence:
                    found[key] = hit
    return list(found.values())


def importance_for(base: str, evidence_text: str, evidence_count: int, trust_level: str) -> str:
    rank = _IMPORTANCE_RANK.get(base, 0)
    if _EXCLUSIVE.search(evidence_text or ""):
        return "CRITICAL"
    if trust_level == "HIGH" and rank < _IMPORTANCE_RANK["HIGH"]:
        rank += 1
    if evidence_count >= 3 and rank < _IMPORTANCE_RANK["HIGH"]:
        rank += 1
    if _AMOUNT.search(evidence_text or "") and rank < _IMPORTANCE_RANK["HIGH"]:
        rank += 1
    for label, value in _IMPORTANCE_RANK.items():
        if value == rank:
            return label
    return "LOW"


def automatic_status(confidence: int, evidence_count: int) -> str:
    """A confidence below 60 never confirms. One indirect mention stays discovered."""
    if confidence >= 90:
        return "CONFIRMED"
    if confidence >= 60 and evidence_count >= 2:
        return "CONFIRMED"
    return "DISCOVERED"


def _apply(text: str, rule: RelationRule, left: Entity, right: Entity) -> RelationHit | None:
    for left_name in left.names:
        if not contains_phrase(text, left_name):
            continue
        for right_name in right.names:
            if not contains_phrase(text, right_name):
                continue
            pattern = rule.template.format(left=re.escape(left_name), right=re.escape(right_name))
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match is None:
                continue
            source = left if rule.source_slot == "left" else right
            target = right if rule.target_slot == "right" else left
            if source.label.casefold() == target.label.casefold():
                return None
            return RelationHit(
                source=source,
                target=target,
                relationship_type=rule.relationship_type,
                confidence=rule.confidence,
                importance=rule.importance,
                rule_name=rule.name,
                excerpt=_excerpt(text, match.start(), match.end()),
            )
    return None


def _excerpt(text: str, start: int, end: int) -> str:
    window_start = max(0, start - 60)
    window_end = min(len(text), end + 60)
    snippet = " ".join(text[window_start:window_end].split())
    return snippet[:500]
