"""
AI4Bharat IndicTrans2 Provider for JanSetu Grievance System.
Native in-project neural translation provider implementing the TranslationProvider interface.

Operates 100% locally and offline:
- Zero external HTTP calls or third-party data transmission
- Native in-process PyTorch Seq2Seq execution
- Sentence-aware batch chunking and entity preservation
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import get_settings
from app.services.translation.base import TranslationProvider
from app.services.translation.indictrans_service import (
    LANG_ENGLISH,
    LANG_KANNADA,
    IndicTransService,
)
from app.services.translation.validator import validate_document_translation

logger = logging.getLogger(__name__)
settings = get_settings()


class IndicTrans2Provider(TranslationProvider):
    """
    Local AI4Bharat IndicTrans2 Neural Provider.
    Translates full sentences between Kannada and English with 100% complete coverage.
    Executes entirely in-process using CPU or CUDA GPU.
    """

    PROVIDER_NAME = "AI4Bharat-IndicTrans2"
    MODEL_VERSION = "indictrans2-dist-200M (Neural Engine)"
    SUPPORTED_LANGUAGES = ["en", "kn"]

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path or getattr(settings, "INDICTRANS2_MODEL_PATH", None)

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> Tuple[str, Dict[str, Any]]:
        metadata: Dict[str, Any] = {
            "provider": "LOCAL",
            "provider_name": self.PROVIDER_NAME,
            "model_version": self.MODEL_VERSION,
            "source_language": source_language,
            "target_language": target_language,
            "validation_warnings": [],
            "chunks_processed": 0,
            "offline": True,
        }

        if not text or not text.strip():
            return "", metadata

        # Normalize language codes to IndicTrans2 format ('kan_Knda', 'eng_Latn')
        src_code = (
            LANG_KANNADA
            if source_language in ["kn", "kan", LANG_KANNADA, "kannada"]
            else LANG_ENGLISH
        )
        tgt_code = (
            LANG_KANNADA
            if target_language in ["kn", "kan", LANG_KANNADA, "kannada"]
            else LANG_ENGLISH
        )

        if src_code == tgt_code:
            metadata["unchanged"] = True
            return text, metadata

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

        metadata["chunks_processed"] = len(paragraphs)

        # Execute In-Process Local Neural Inference
        service = IndicTransService.get_instance()
        translated_paras = service.translate_paragraphs(paragraphs, src_code, tgt_code)
        full_translated = "\n\n".join(translated_paras)

        validation = validate_document_translation(text, full_translated)
        metadata["validation_warnings"] = validation.get("warnings", [])
        metadata["device"] = service.device.upper()
        metadata["status"] = "COMPLETED"

        return full_translated, metadata

    def get_supported_languages(self) -> List[str]:
        return self.SUPPORTED_LANGUAGES

    def get_provider_info(self) -> Dict[str, Any]:
        service = IndicTransService.get_instance()
        return service.get_status()
