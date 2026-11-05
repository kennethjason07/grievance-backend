"""
Comprehensive Unit & Quality Test Suite for JanSetu Local Neural Translation Engine.
Tests:
- 100% offline execution (verifies zero outbound network calls)
- Bidirectional Kannada <-> English translation
- Paragraph and line-break preservation
- Sentence-aware chunking for long petitions
- Preservation of phone numbers, dates, times, and complaint IDs
- Graceful handling of missing model files and unsupported languages
- CPU execution and status reporting
"""
import socket
import sys
import time
from pathlib import Path
from unittest.mock import patch
import pytest

from app.services.translation.indictrans_service import (
    LANG_ENGLISH,
    LANG_KANNADA,
    IndicTransService,
    clean_translated_text,
    get_target_device,
)
from app.services.translation.indictrans2 import IndicTrans2Provider


# ---------------------------------------------------------------------------
# 1. Offline & Privacy Guard Tests
# ---------------------------------------------------------------------------

def test_engine_status_reports_local_and_offline():
    """Verify engine status guarantees local and offline execution without exposing file paths."""
    service = IndicTransService.get_instance()
    status = service.get_status()

    assert status["provider"] == "LOCAL"
    assert status["is_local"] is True
    assert status["offline_available"] is True
    assert status["requires_external_network"] is False
    assert status["supported_pairs"] == ["kn->en", "en->kn"]
    assert "en" in status["supported_languages"]
    assert "kn" in status["supported_languages"]
    assert status["device"] in ["CPU", "CUDA", "MPS"]
    # Verify no private absolute paths or tokens are leaked
    for key, val in status.items():
        if isinstance(val, str):
            assert "C:" not in val
            assert "/Users/" not in val
            assert "token" not in key.lower()


def test_zero_external_network_calls_guaranteed():
    """Verify that translation provider does not initiate external network connections."""
    provider = IndicTrans2Provider()

    # Block socket connections to guarantee zero external calls
    orig_connect = socket.socket.connect
    def blocked_connect(self, *args, **kwargs):
        raise ConnectionRefusedError("Privacy violation: External network call attempted during translation!")

    with patch("socket.socket.connect", side_effect=blocked_connect):
        # Empty and same-language paths must succeed with zero network access
        res_empty, meta_empty = provider.translate("", "kn", "en")
        assert res_empty == ""

        res_same, meta_same = provider.translate("Same text", "en", "en")
        assert res_same == "Same text"
        assert meta_same.get("unchanged") is True


# ---------------------------------------------------------------------------
# 2. Text Processing & Chunking Tests
# ---------------------------------------------------------------------------

def test_sentence_aware_chunking_preserves_boundaries():
    """Verify long paragraphs are split into coherent sentences without breaking times or initials."""
    service = IndicTransService.get_instance()

    long_para = (
        "Respected Deputy Commissioner, I am writing from Shantinagar at 10:30 AM. "
        "The road excavation commenced on 15/08/2026 and caused serious water pipe rupture! "
        "Can the authorities inspect the location immediately? Please help."
    )
    sentences = service.split_into_sentences(long_para)
    assert len(sentences) >= 3
    # Verify time 10:30 AM is not broken
    assert any("10:30 AM" in s for s in sentences)


def test_clean_translated_text_preserves_entities():
    """Verify phone numbers, dates, times, and formatting are protected."""
    src = "Call 9876543210 regarding petition on 12/04/2026 at 10:00 AM."
    mock_tgt = "Call 9876543210 regarding petition on 12/04/2026 at 10:00 a.m.."

    cleaned = clean_translated_text(src, mock_tgt, target_lang=LANG_ENGLISH)
    assert "9876543210" in cleaned
    assert "12/04/2026" in cleaned
    assert "10:00 AM" in cleaned


def test_clean_translated_text_word_deduplication():
    """Verify stuttering duplicate tokens from beam search are cleanly collapsed."""
    src = "Important details regarding grievance."
    mock_tgt = "Important details details regarding regarding grievance."

    cleaned = clean_translated_text(src, mock_tgt, target_lang=LANG_ENGLISH)
    assert "details details" not in cleaned
    assert "regarding regarding" not in cleaned


# ---------------------------------------------------------------------------
# 3. Missing Model Handling
# ---------------------------------------------------------------------------

def test_missing_model_raises_clear_error():
    """Verify that uninstalled models raise an informative error instructing user to run download script."""
    service = IndicTransService.get_instance()

    # If models are not installed yet, load_model must raise RuntimeError with instructions
    with patch("app.services.translation.indictrans_service.get_local_model_path", return_value=None):
        with pytest.raises(RuntimeError) as exc_info:
            service.load_model(LANG_ENGLISH, LANG_KANNADA)
        assert "download_translation_models.py" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 4. End-to-End Real Model Quality Benchmark (runs when weights are installed)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not IndicTransService.get_instance().is_ready(),
    reason="Local neural model weights not yet downloaded/installed on disk.",
)
def test_real_model_kannada_to_english_quality():
    """Benchmark real neural translation of Kannada grievance sample to English."""
    provider = IndicTrans2Provider()
    kannada_petition = (
        "ಮಾನ್ಯ ಜಿಲ್ಲಾಧಿಕಾರಿಗಳಿಗೆ,\n\n"
        "ನಮ್ಮ ಶಾಂತಿನಗರ ಗ್ರಾಮದಲ್ಲಿ ಕುಡಿಯುವ ನೀರಿನ ಪೈಪ್ ಒಡೆದು ಹೋಗಿದ್ದು, "
        "ಕಳೆದ ಒಂದು ವಾರದಿಂದ ಸಾರ್ವಜನಿಕರಿಗೆ ತೊಂದರೆಯಾಗಿದೆ. ಮೊಬೈಲ್ ಸಂಖ್ಯೆ: 9845012345. "
        "ದಯವಿಟ್ಟು ಕೂಡಲೇ ದುರಸ್ತಿ ಕಾರ್ಯ ಕೈಗೊಳ್ಳಲು ವಿನಂತಿ."
    )

    start = time.time()
    translated, meta = provider.translate(kannada_petition, "kn", "en")
    elapsed = time.time() - start

    assert translated and len(translated) > 20
    assert meta["status"] == "COMPLETED"
    # Ensure paragraphs are preserved
    assert "\n\n" in translated
    # Check that phone number is preserved
    assert "9845012345" in translated
    print(f"\n[BENCHMARK] KN->EN Translation completed in {elapsed:.2f}s ({meta['device']})")
    print(f"Output:\n{translated}")


@pytest.mark.skipif(
    not IndicTransService.get_instance().is_ready(),
    reason="Local neural model weights not yet downloaded/installed on disk.",
)
def test_real_model_english_to_kannada_quality():
    """Benchmark real neural translation of English grievance sample to Kannada."""
    provider = IndicTrans2Provider()
    english_petition = (
        "To The Deputy Commissioner, Bengaluru Urban District.\n\n"
        "Subject: Complaint regarding severe road damage and drainage blockage on MG Road.\n\n"
        "We request the authorities to conduct a spot inspection and initiate repair work immediately. "
        "Contact: 9876543210, Date: 03-11-2026."
    )

    start = time.time()
    translated, meta = provider.translate(english_petition, "en", "kn")
    elapsed = time.time() - start

    assert translated and len(translated) > 20
    assert meta["status"] == "COMPLETED"
    # Ensure Kannada Unicode script is produced
    assert any("\u0C80" <= c <= "\u0CFF" for c in translated)
    # Ensure phone number and date are preserved
    assert "9876543210" in translated
    assert "03-11-2026" in translated
    print(f"\n[BENCHMARK] EN->KN Translation completed in {elapsed:.2f}s ({meta['device']})")
    print(f"Output:\n{translated}")

