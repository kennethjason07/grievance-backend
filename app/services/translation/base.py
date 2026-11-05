"""
Base abstraction for JanSetu Translation Providers.
Enables pluggable translation backends (AI4Bharat IndicTrans2, Local Offline Engine, Test Mock)
while strictly safeguarding citizen data privacy and confidentiality.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple


class TranslationProvider(ABC):
    """Abstract interface for bidirectional translation providers."""

    @abstractmethod
    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Execute translation from source_language to target_language.

        Returns:
            Tuple of (translated_text, metadata_dict) where metadata includes:
            - provider_name: str
            - model_version: str
            - chunks_processed: int
        """
        pass

    @abstractmethod
    def get_supported_languages(self) -> List[str]:
        """Return list of supported ISO language codes (e.g. ['en', 'kn'])."""
        pass

    @abstractmethod
    def get_provider_info(self) -> Dict[str, Any]:
        """Return provider architecture, model metadata, and operational status."""
        pass

