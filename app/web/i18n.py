"""French labels for the HTML pages.

Stored enums and API payloads stay in their original tokens.
Unknown values are shown unchanged.
"""

from datetime import date
from decimal import Decimal

UNIVERSE_STATUS_LABELS = {
    "DISCOVERED": "Découverte",
    "WATCHED": "Surveillée",
    "SCREENED": "Filtrée",
    "DEEP_ANALYSIS": "Analyse approfondie",
    "PORTFOLIO": "Portefeuille",
    "ARCHIVED": "Archivée",
}

DISCOVERY_SOURCE_LABELS = {
    "MANUAL": "Manuel",
    "SP500": "S&P 500",
    "NASDAQ100": "Nasdaq 100",
    "PEA": "PEA",
    "EUROPE": "Europe",
    "SUPPLY_CHAIN": "Chaîne d’approvisionnement",
    "BOTTLENECK": "Goulot d’étranglement",
    "INSTITUTIONAL": "Institutionnel",
    "OTHER": "Autre",
}

UNIVERSE_NAME_LABELS = {
    **DISCOVERY_SOURCE_LABELS,
    "EURO_STOXX": "Euro Stoxx",
}

COVERAGE_LABELS = {
    "INCOMPLETE": "Incomplète",
    "PARTIAL": "Partielle",
    "USABLE": "Exploitable",
    "COMPLETE": "Complète",
}

CONVICTION_LABELS = {
    "LOW": "Faible",
    "MODERATE": "Modérée",
    "GOOD": "Bonne",
    "HIGH": "Élevée",
    "VERY_HIGH": "Très élevée",
}

ENTRY_LABELS = {
    "POOR": "Défavorable",
    "NEUTRAL": "Neutre",
    "FAVORABLE": "Favorable",
    "STRONG": "Forte",
    "EXTENDED_OR_EXCEPTIONAL": "Très forte / étendue",
}

TREND_LABELS = {
    "STRONG_UP": "Forte hausse",
    "UP": "Hausse",
    "NEUTRAL": "Neutre",
    "DOWN": "Baisse",
    "STRONG_DOWN": "Forte baisse",
}

VERIFICATION_STATUS_LABELS = {
    "UNVERIFIED": "Non vérifiée",
    "PARTIAL": "Partielle",
    "VERIFIED": "Vérifiée",
    "REJECTED": "Rejetée",
}

PIPELINE_STATUS_LABELS = {
    "NEW": "Nouveau",
    "READY": "Prêt",
    "COLLECTING": "Collecte en cours",
    "ANALYZED": "Analysé",
    "BLOCKED": "Bloqué",
}

RELATIONSHIP_STATUS_LABELS = {
    "DISCOVERED": "Découverte",
    "CONFIRMED": "Confirmée",
    "REJECTED": "Rejetée",
    "STALE": "Obsolète",
}

RELATIONSHIP_TYPE_LABELS = {
    "CUSTOMER": "Client",
    "SUPPLIER": "Fournisseur",
    "PARTNER": "Partenaire",
    "COMPETITOR": "Concurrent",
    "INVESTOR": "Investisseur",
    "SUBCONTRACTOR": "Sous-traitant",
    "EQUIPMENT_PROVIDER": "Fournisseur d’équipements",
    "MATERIAL_PROVIDER": "Fournisseur de matériaux",
    "INFRASTRUCTURE_PROVIDER": "Fournisseur d’infrastructure",
    "OTHER": "Autre",
}

EVENT_TYPE_LABELS = {
    "CONTRACT": "Contrat",
    "PARTNERSHIP": "Partenariat",
    "ACQUISITION": "Acquisition",
    "INVESTMENT": "Investissement",
    "NEW_FACTORY": "Nouvelle usine",
    "CAPACITY_EXPANSION": "Extension de capacité",
    "PRODUCT_LAUNCH": "Lancement produit",
    "CUSTOMER_WIN": "Nouveau client",
    "SUPPLIER_CHANGE": "Changement fournisseur",
    "REGULATORY": "Réglementaire",
    "MANAGEMENT": "Direction",
    "FINANCING": "Financement",
    "LAYOFF": "Réduction d’effectifs",
    "CYBERSECURITY": "Cybersécurité",
    "LEGAL": "Juridique",
    "OTHER": "Autre",
}

IMPORTANCE_LABELS = {
    "LOW": "Faible",
    "MEDIUM": "Moyenne",
    "HIGH": "Élevée",
    "CRITICAL": "Critique",
}

CANDIDATE_STATUS_LABELS = {
    "CANDIDATE": "Candidate",
    "VERIFIED": "Vérifiée",
    "IMPORTED": "Importée",
    "REJECTED": "Rejetée",
}

DOCUMENT_TYPE_LABELS = {
    "NEWS_ARTICLE": "Article",
    "PRESS_RELEASE": "Communiqué",
    "SEC_FILING": "Dépôt SEC",
    "BLOG_POST": "Billet",
    "GOVERNMENT_RELEASE": "Publication officielle",
    "INDUSTRY_ARTICLE": "Article sectoriel",
    "OTHER": "Autre",
}

DIRECTION_LABELS = {
    "SOURCE_TO_TARGET": "Source vers cible",
}

SYSTEM_STATUS_LABELS = {
    "ok": "disponible",
    "error": "erreur",
}

AI_ANALYSIS_STATUS_LABELS = {
    "PENDING": "En attente",
    "RUNNING": "En cours",
    "SUCCESS": "Réussie",
    "INVALID_OUTPUT": "Sortie invalide",
    "FAILED": "Échec",
}

WARNING_LABELS = {
    "Opportunity coverage is below ranking threshold.": (
        "La couverture de l’analyse d’opportunité est sous le seuil requis pour le classement."
    ),
    "Market capitalization missing.": "La capitalisation boursière est absente.",
    "Quality score is missing.": "Le score de qualité est absent.",
    "Opportunity score is missing.": "Le score d’opportunité est absent.",
    "Technical timing is extended above long-term average.": (
        "Le cours est fortement étendu au-dessus de sa moyenne long terme."
    ),
    "Technical score is missing.": "Le score technique est absent.",
    "RSI is very overbought.": "Le RSI est en fort surachat.",
    "Technical confidence is below 70.": "La confiance technique est inférieure à 70.",
}

LOGIN_ERROR = "Identifiants incorrects."
SESSION_EXPIRED = "Session expirée."

_CATALOGS = {
    "universe_status": UNIVERSE_STATUS_LABELS,
    "discovery_source": DISCOVERY_SOURCE_LABELS,
    "universe_name": UNIVERSE_NAME_LABELS,
    "coverage": COVERAGE_LABELS,
    "readiness": COVERAGE_LABELS,
    "conviction": CONVICTION_LABELS,
    "entry": ENTRY_LABELS,
    "trend": TREND_LABELS,
    "verification_status": VERIFICATION_STATUS_LABELS,
    "pipeline_status": PIPELINE_STATUS_LABELS,
    "relationship_status": RELATIONSHIP_STATUS_LABELS,
    "relationship_type": RELATIONSHIP_TYPE_LABELS,
    "event_type": EVENT_TYPE_LABELS,
    "importance": IMPORTANCE_LABELS,
    "candidate_status": CANDIDATE_STATUS_LABELS,
    "document_type": DOCUMENT_TYPE_LABELS,
    "direction": DIRECTION_LABELS,
    "system_status": SYSTEM_STATUS_LABELS,
    "ai_analysis_status": AI_ANALYSIS_STATUS_LABELS,
}


def translate_label(category: str, value: object) -> str:
    if value is None or value == "" or value == "—":
        return "—"
    text = str(value).strip()
    return _CATALOGS.get(category, {}).get(text, text)


def translate_warning(value: object) -> str:
    if value is None or value == "":
        return "—"
    text = str(value)
    return WARNING_LABELS.get(text, text)


def label_universe_status(value: object) -> str:
    return translate_label("universe_status", value)


def label_discovery_source(value: object) -> str:
    return translate_label("discovery_source", value)


def label_universe_name(value: object) -> str:
    return translate_label("universe_name", value)


def label_coverage(value: object) -> str:
    return translate_label("coverage", value)


def label_readiness(value: object) -> str:
    return translate_label("readiness", value)


def label_conviction(value: object) -> str:
    return translate_label("conviction", value)


def label_entry(value: object) -> str:
    return translate_label("entry", value)


def label_trend(value: object) -> str:
    return translate_label("trend", value)


def label_verification(value: object) -> str:
    return translate_label("verification_status", value)


def label_pipeline(value: object) -> str:
    return translate_label("pipeline_status", value)


def label_relationship_status(value: object) -> str:
    return translate_label("relationship_status", value)


def label_relationship_type(value: object) -> str:
    return translate_label("relationship_type", value)


def label_event(value: object) -> str:
    return translate_label("event_type", value)


def label_importance(value: object) -> str:
    return translate_label("importance", value)


def label_candidate_status(value: object) -> str:
    return translate_label("candidate_status", value)


def label_document_type(value: object) -> str:
    return translate_label("document_type", value)


def label_direction(value: object) -> str:
    return translate_label("direction", value)


def label_system_status(value: object) -> str:
    return translate_label("system_status", value)


def label_ai_analysis_status(value: object) -> str:
    return translate_label("ai_analysis_status", value)


def format_number(value: object, digits: int = 2) -> str:
    number = Decimal(str(value))
    places = max(0, digits)
    quantum = Decimal("1") if places == 0 else Decimal("1").scaleb(-places)
    number = number.quantize(quantum)
    sign = "-" if number < 0 else ""
    text = f"{abs(number):.{places}f}"
    whole, _, fraction = text.partition(".")
    groups: list[str] = []
    while whole:
        groups.append(whole[-3:])
        whole = whole[:-3]
    grouped = " ".join(reversed(groups))
    if places == 0:
        return sign + grouped
    return f"{sign}{grouped},{fraction}"


def format_date(value: date) -> str:
    return f"{value.day:02d}/{value.month:02d}/{value.year}"
