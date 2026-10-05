"""Registered external content providers."""

from app.services.intelligence.providers.base import ExternalContentProvider
from app.services.intelligence.providers.rss import RssAtomProvider
from app.services.intelligence.providers.sec_submissions import SecSubmissionsProvider

PROVIDERS: dict[str, ExternalContentProvider] = {
    RssAtomProvider.provider_name: RssAtomProvider(),
    SecSubmissionsProvider.provider_name: SecSubmissionsProvider(),
}


def provider_for(name: str) -> ExternalContentProvider:
    try:
        return PROVIDERS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown external provider: {name}") from exc
