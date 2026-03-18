from __future__ import annotations

import logging
import os
from typing import Dict, Optional

from app.providers.base import ScraperProvider
from app.providers.allegro_scraper import AllegroScraperProvider

logger = logging.getLogger(__name__)

_providers: Dict[str, ScraperProvider] = {}


def _init_defaults() -> None:
    if not _providers:
        register(AllegroScraperProvider())


def register(provider: ScraperProvider) -> None:
    _providers[provider.name] = provider
    logger.info("PROVIDER registered: %s", provider.name)


def get(name: Optional[str] = None) -> ScraperProvider:
    """Get a provider by name. Defaults to PROVIDER_MODE env or 'allegro_scraper'."""
    _init_defaults()
    if name is None:
        name = os.getenv("PROVIDER_MODE", "allegro_scraper")
    provider = _providers.get(name)
    if not provider:
        raise ValueError(f"Unknown provider: {name}. Available: {list(_providers.keys())}")
    return provider


def list_providers() -> Dict[str, str]:
    """Return dict of name -> class name for all registered providers."""
    _init_defaults()
    return {name: type(p).__name__ for name, p in _providers.items()}


def health_all() -> Dict[str, dict]:
    """Run health check on all registered providers."""
    _init_defaults()
    results = {}
    for name, provider in _providers.items():
        try:
            results[name] = provider.health()
        except Exception as exc:
            results[name] = {"status": "error", "error": repr(exc)}
    return results
