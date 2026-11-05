"""
Local Neural Translation Provider for JanSetu.
100% offline, fully local neural machine translation engine based on AI4Bharat IndicTrans2.
Zero external network calls, zero third-party data transmission, full citizen data privacy.
"""
from typing import Any, Dict, List, Optional, Tuple

from app.services.translation.base import TranslationProvider
from app.services.translation.indictrans2 import IndicTrans2Provider


class LocalAdministrativeTranslator(TranslationProvider):
    """
    Local Neural Translation Engine for Karnataka Grievances.
    Direct drop-in provider running completely offline in-process.
    """

    def __init__(self, model_path: Optional[str] = None) -> None:
        self._neural_provider = IndicTrans2Provider(model_path=model_path)

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> Tuple[str, Dict[str, Any]]:
        return self._neural_provider.translate(text, source_language, target_language)

    def get_supported_languages(self) -> List[str]:
        return self._neural_provider.get_supported_languages()

    def get_provider_info(self) -> Dict[str, Any]:
        return self._neural_provider.get_provider_info()


# Aliases for compatibility
DirectModuleTranslator = LocalAdministrativeTranslator
LocalNeuralTranslator = LocalAdministrativeTranslator
