"""Keyword events. Each rule names the words that fired it. There is no model."""

from dataclasses import dataclass
import re

_IMPORTANCE_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
_BILLION = r"(?:\$\d+(?:\.\d+)?\s*[Bb]\b|\$?\d+(?:\.\d+)?\s*(?:billion|bn)\b)"


@dataclass(frozen=True)
class EventRule:
    event_type: str
    importance: str
    confidence: int
    pattern: str
    reason: str


RULES: tuple[EventRule, ...] = (
    EventRule("LEGAL", "CRITICAL", 90, r"\b(?:bankruptcy|chapter\s+11|insolvency)\b", "bankruptcy or insolvency"),
    EventRule(
        "CONTRACT",
        "HIGH",
        80,
        r"\b(?:cancel(?:s|led|ling)?|terminat(?:e|es|ed))\b(?:\W+\w+){0,6}\W+\bcontracts?\b|\bcontracts?\b(?:\W+\w+){0,6}\W+\b(?:cancel(?:s|led|ling)?|terminat(?:e|es|ed))\b",
        "a contract is cancelled",
    ),
    EventRule("CYBERSECURITY", "HIGH", 80, r"\b(?:data breach|ransomware|cyber\s?attack|cybersecurity incident)\b", "a security incident"),
    EventRule(
        "NEW_FACTORY",
        "HIGH",
        85,
        rf"(?:\bfab\b|\bfactory\b|\bplant\b).{{0,40}}{_BILLION}|{_BILLION}.{{0,40}}(?:\bfab\b|\bfactory\b|\bplant\b)",
        "a large factory or fab investment",
    ),
    EventRule(
        "INVESTMENT",
        "HIGH",
        80,
        rf"{_BILLION}.{{0,40}}\binvest(?:ment|ing|s|ed)?\b|\binvest(?:ment|ing|s|ed)?\b.{{0,40}}{_BILLION}",
        "an investment measured in billions",
    ),
    EventRule("ACQUISITION", "HIGH", 75, r"\b(?:acquir(?:e|es|ed|ing)|acquisition|merger)\b", "an acquisition or merger"),
    EventRule("LAYOFF", "HIGH", 70, r"\b(?:layoffs?|job cuts|workforce reduction)\b", "a workforce reduction"),
    EventRule("CAPACITY_EXPANSION", "MEDIUM", 70, r"\b(?:capacity expansion|expand(?:s|ed|ing)? capacity)\b", "a capacity expansion"),
    EventRule("PARTNERSHIP", "MEDIUM", 65, r"\b(?:partnership|partners with|strategic alliance|joint venture)\b", "a partnership"),
    EventRule("CUSTOMER_WIN", "MEDIUM", 65, r"\b(?:design win|customer win|wins? (?:a )?customer)\b", "a customer win"),
    EventRule("SUPPLIER_CHANGE", "MEDIUM", 65, r"\b(?:switches suppliers?|supplier change|new supplier)\b", "a supplier change"),
    EventRule("REGULATORY", "HIGH", 70, r"\b(?:antitrust|regulator(?:y)? investigation|sec investigation)\b", "a regulatory action"),
    EventRule("MANAGEMENT", "MEDIUM", 60, r"\b(?:chief executive|chief financial|\bceo\b|\bcfo\b|resigns|appointed ceo)\b", "a management change"),
    EventRule("FINANCING", "MEDIUM", 60, r"\b(?:public offering|debt offering|raises \$|bond offering)\b", "a financing"),
    EventRule("CONTRACT", "MEDIUM", 60, r"\b(?:awarded a contract|wins? (?:a )?contract|supply agreement)\b", "a contract award"),
    EventRule("PRODUCT_LAUNCH", "MEDIUM", 55, r"\b(?:product launch|launches (?:a |the )?(?:new )?product|unveils)\b", "a product launch"),
    EventRule("PRODUCT_LAUNCH", "LOW", 50, r"\bnew product announced\b|\bannounc(?:e|es|ed) (?:a |the )?new product\b", "a new product announcement"),
)


@dataclass(frozen=True)
class ExtractedEvent:
    event_type: str
    importance: str
    confidence: int
    title: str
    description: str


def extract_events(text: str | None) -> list[ExtractedEvent]:
    if not text or not text.strip():
        return []
    chosen: dict[str, tuple[EventRule, re.Match]] = {}
    for rule in RULES:
        match = re.search(rule.pattern, text, re.IGNORECASE)
        if match is None:
            continue
        current = chosen.get(rule.event_type)
        if current is None or _IMPORTANCE_RANK[rule.importance] > _IMPORTANCE_RANK[current[0].importance]:
            chosen[rule.event_type] = (rule, match)
    events = []
    for rule, match in chosen.values():
        excerpt = " ".join(text[max(0, match.start() - 40): match.end() + 40].split())
        events.append(
            ExtractedEvent(
                event_type=rule.event_type,
                importance=rule.importance,
                confidence=rule.confidence,
                title=rule.reason[:500],
                description=f"Matched because of {rule.reason}. Excerpt: {excerpt}"[:2000],
            )
        )
    return events
