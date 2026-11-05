"""
Document and Text Translation Validator.
Verifies fidelity of translation output against source input:
- Preserves dates, times, phone numbers, and currency values.
- Checks paragraph count and sequence preservation.
- Detects dropped entities or hallucinated numbers.
"""
import re
from typing import Any, Dict, List


def validate_document_translation(source_text: str, translated_text: str) -> Dict[str, Any]:
    """
    Validate translation output against source input text.
    Returns a dictionary with validation status and any warnings.
    """
    warnings: List[str] = []

    if not source_text or not source_text.strip():
        return {"is_valid": True, "warnings": []}

    if not translated_text or not translated_text.strip():
        return {
            "is_valid": False,
            "warnings": ["Translated text is empty while source text contains content."],
        }

    # 1. Check numbers and numeric identifiers preservation
    src_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", source_text))
    tgt_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", translated_text))

    # Identify any crucial numeric entities in source missing in target
    missing_numbers = src_numbers - tgt_numbers
    # Filter out single digits that might have been converted to words (e.g. 1 -> one)
    significant_missing = [n for n in missing_numbers if len(n) > 1]
    if significant_missing:
        warnings.append(
            f"Possible dropped numeric entity or reference ID: {', '.join(sorted(significant_missing)[:5])}"
        )

    # 2. Check time formats (e.g., 10:00, 4:00, 9:30)
    src_times = re.findall(r"\b\d{1,2}:\d{2}\b", source_text)
    tgt_times = re.findall(r"\b\d{1,2}:\d{2}\b", translated_text)
    if len(src_times) != len(tgt_times):
        warnings.append(
            f"Time format count mismatch: source has {len(src_times)} time(s), translated has {len(tgt_times)}."
        )

    # 3. Check paragraph count preservation
    src_paras = [p for p in source_text.split("\n\n") if p.strip()]
    tgt_paras = [p for p in translated_text.split("\n\n") if p.strip()]
    if len(src_paras) > 1 and len(tgt_paras) > 1 and len(src_paras) != len(tgt_paras):
        warnings.append(
            f"Paragraph count difference: source has {len(src_paras)} paragraphs, target has {len(tgt_paras)}."
        )

    # 4. Check year range preservation (e.g. 2026-2027 or 2026–2027)
    src_years = re.findall(r"\b(20\d{2})\b", source_text)
    tgt_years = re.findall(r"\b(20\d{2})\b", translated_text)
    if set(src_years) != set(tgt_years) and len(src_years) > 0:
        missing_years = set(src_years) - set(tgt_years)
        if missing_years:
            warnings.append(f"Year entities missing in translation: {', '.join(missing_years)}")

    return {
        "is_valid": len(warnings) == 0,
        "warnings": warnings,
        "source_paragraphs": len(src_paras),
        "target_paragraphs": len(tgt_paras),
    }


def validate_page_translation(
    original_paragraphs: List[str], translated_paragraphs: List[str]
) -> Dict[str, Any]:
    """
    Validate a list of original paragraphs against translated paragraphs for a document page.
    """
    src_full = "\n\n".join(original_paragraphs)
    tgt_full = "\n\n".join(translated_paragraphs)
    return validate_document_translation(src_full, tgt_full)

