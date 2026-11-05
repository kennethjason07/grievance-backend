"""
Translation Provider Factory.
Instantiates the configured translation engine according to system settings.
"""
from typing import Optional

from app.core.config import get_settings
from app.services.translation.base import TranslationProvider
from app.services.translation.indictrans2 import IndicTrans2Provider
from app.services.translation.local_engine import LocalAdministrativeTranslator
from app.services.translation.mock_provider import MockTranslationProvider

settings = get_settings()

_override_provider: Optional[TranslationProvider] = None


def set_translation_provider_override(provider: Optional[TranslationProvider]) -> None:
    """Used in test fixtures to inject custom or mocked translation providers."""
    global _override_provider
    _override_provider = provider


def get_translation_provider() -> TranslationProvider:
    """Return the active translation provider."""
    global _override_provider
    if _override_provider is not None:
        return _override_provider

    provider_name = (settings.TRANSLATION_PROVIDER or "mock").strip().lower()

    if provider_name == "indictrans2":
        return IndicTrans2Provider(model_path=settings.INDICTRANS2_MODEL_PATH)
    elif provider_name in ("local", "admin_lexicon"):
        return LocalAdministrativeTranslator()
    else:
        # Default fallback for testing and development
        return MockTranslationProvider()

