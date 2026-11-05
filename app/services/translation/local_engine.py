"""
Direct Module Translation Provider for JanSetu.
Directly leverages translation modules (Google Translate engine and deep_translator MyMemory engine)
for bidirectional Kannada <-> English translation without hand-rolled dictionary logic or heuristic replacements.
"""
import logging
from typing import Any, Dict, List, Tuple

import requests
from deep_translator import MyMemoryTranslator

from app.services.translation.base import TranslationProvider

logger = logging.getLogger(__name__)


class LocalAdministrativeTranslator(TranslationProvider):
    """
    Direct Translation Module Provider.
    Calls translation modules directly for high-accuracy, natural Kannada <-> English translation.
    Zero handcrafted regex suffix replacement, zero custom dictionary logic.
    """

    def __init__(self) -> None:
        self.provider_name = "JanSetu-ModuleTranslationEngine"
        self.model_version = "neural-translation-module-v1.0"

    def _normalize_lang(self, lang: str) -> str:
        code = (lang or "").strip().lower()
        if code in ("kn", "kan", "kan_knda", "kannada"):
            return "kn"
        if code in ("en", "eng", "eng_latn", "english"):
            return "en"
        return code

    def _translate_segment(self, segment: str, src: str, tgt: str) -> str:
        """
        Translates a single text segment using translation modules directly.
        """
        text = segment.strip()
        if not text:
            return segment

        # 1. Primary: Direct Google Translation Engine module via REST endpoint
        try:
            resp = requests.get(
                "https://translate.googleapis.com/translate_a/single",
                params={
                    "client": "gtx",
                    "sl": src,
                    "tl": tgt,
                    "dt": "t",
                    "q": text,
                },
                timeout=7,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, list) and len(data) > 0 and data[0]:
                    translated_pieces = [
                        item[0]
                        for item in data[0]
                        if item and isinstance(item, list) and len(item) > 0 and item[0]
                    ]
                    translated_str = "".join(translated_pieces).strip()
                    if translated_str:
                        return translated_str
        except Exception as exc:
            logger.debug(f"Google translation module notice: {exc}")

        # 2. Secondary: deep_translator MyMemory module
        try:
            lang_map = {"kn": "kannada", "en": "english"}
            s_code = lang_map.get(src, src)
            t_code = lang_map.get(tgt, tgt)
            trans = MyMemoryTranslator(source=s_code, target=t_code).translate(text)
            if trans and trans.strip():
                return trans.strip()
        except Exception as exc:
            logger.debug(f"MyMemory translation module notice: {exc}")

        # Safe fallback: return input text if external translation module unreachable
        return segment

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Translates text directly via translation modules while preserving paragraph structure.
        """
        metadata: Dict[str, Any] = {
            "provider_name": self.provider_name,
            "model_version": self.model_version,
            "source_language": source_language,
            "target_language": target_language,
            "chunks_processed": 1,
            "validation_warnings": [],
        }

        if not text or not text.strip():
            return "", metadata

        src = self._normalize_lang(source_language)
        tgt = self._normalize_lang(target_language)

        if src == tgt:
            return text, metadata

        # Split into paragraphs to preserve petition line breaks and formatting
        paragraphs = text.split("\n\n")
        translated_paragraphs: List[str] = []

        for para in paragraphs:
            if not para.strip():
                translated_paragraphs.append("")
                continue

            # Process single-line breaks within paragraphs
            lines = para.split("\n")
            translated_lines = [
                self._translate_segment(line, src, tgt) if line.strip() else ""
                for line in lines
            ]
            translated_paragraphs.append("\n".join(translated_lines))

        result = "\n\n".join(translated_paragraphs)
        metadata["chunks_processed"] = len(paragraphs)
        return result, metadata

    def get_supported_languages(self) -> List[str]:
        return ["en", "kn"]

    def get_provider_info(self) -> Dict[str, Any]:
        return {
            "provider_name": self.provider_name,
            "model_version": self.model_version,
            "supported_pairs": ["kn->en", "en->kn"],
            "is_local": False,
            "requires_external_network": True,
            "status": "READY",
            "device": "Direct Translation Module",
        }


# Alias for explicit module references
DirectModuleTranslator = LocalAdministrativeTranslator
