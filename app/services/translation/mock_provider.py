"""
Deterministic Mock Translation Provider for automated unit and integration tests.
Guarantees zero network calls and sub-millisecond execution.
"""
from typing import Any, Dict, List, Optional, Tuple

from app.services.translation.base import TranslationProvider


class MockTranslationProvider(TranslationProvider):
    """Predictable mock provider for test suites."""

    PROVIDER_NAME = "MockTranslationProvider"
    MODEL_VERSION = "mock-v1.0"
    SUPPORTED_LANGUAGES = ["en", "kn"]

    def __init__(self, fail_next: bool = False, custom_output: Optional[str] = None):
        self.fail_next = fail_next
        self.custom_output = custom_output
        self.call_count = 0
        self.last_source_lang: Optional[str] = None
        self.last_target_lang: Optional[str] = None

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> Tuple[str, Dict[str, Any]]:
        self.call_count += 1
        self.last_source_lang = source_language
        self.last_target_lang = target_language

        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("Simulated translation provider outage / failure.")

        src = source_language.strip().lower()
        tgt = target_language.strip().lower()

        if src not in self.SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported source language: '{source_language}'")
        if tgt not in self.SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported target language: '{target_language}'")
        if src == tgt:
            raise ValueError(f"Source and target languages cannot be identical ('{src}')")

        if self.custom_output is not None:
            return self.custom_output, {
                "provider_name": self.PROVIDER_NAME,
                "model_version": self.MODEL_VERSION,
                "chunks_processed": 1,
            }

        # Deterministic transformation preserving paragraph order, numbers, and identifiers
        paragraphs = text.split("\n")
        translated_paras: List[str] = []

        for p in paragraphs:
            if not p.strip():
                translated_paras.append("")
                continue

            if src == "kn" and tgt == "en":
                # Simulated translation: replace Kannada markers with English counterparts
                t = p
                t = t.replace("ದೂರು", "Complaint")
                t = t.replace("ಜಿಲ್ಲಾಧಿಕಾರಿಗಳು", "Deputy Commissioner")
                t = t.replace("ಪೊಲೀಸ್ ಠಾಣೆ", "Police Station")
                t = t.replace("ಒತ್ತುವರಿ", "Encroachment")
                t = t.replace("ಅರ್ಜಿದಾರರು", "Petitioner")
                translated_paras.append(f"[EN Translation]: {t}")
            elif src == "en" and tgt == "kn":
                t = p
                t = t.replace("Complaint", "ದೂರು")
                t = t.replace("Deputy Commissioner", "ಜಿಲ್ಲಾಧಿಕಾರಿಗಳು")
                t = t.replace("Police Station", "ಪೊಲೀಸ್ ಠಾಣೆ")
                t = t.replace("Encroachment", "ಒತ್ತುವರಿ")
                t = t.replace("Petitioner", "ಅರ್ಜಿದಾರರು")
                translated_paras.append(f"[KN ಭಾಷಾಂತರ]: {t}")
            else:
                translated_paras.append(f"[{tgt.upper()}]: {p}")

        return "\n".join(translated_paras), {
            "provider_name": self.PROVIDER_NAME,
            "model_version": self.MODEL_VERSION,
            "chunks_processed": len(paragraphs),
        }

    def get_supported_languages(self) -> List[str]:
        return self.SUPPORTED_LANGUAGES

    def get_provider_info(self) -> Dict[str, Any]:
        return {
            "provider_name": self.PROVIDER_NAME,
            "model_version": self.MODEL_VERSION,
            "supported_pairs": ["kn->en", "en->kn"],
            "is_local": True,
            "requires_external_network": False,
            "status": "READY",
            "device": "Test In-Memory Runner",
        }

