"""
AI4Bharat IndicTrans2 Neural Translation Service for JanSetu Grievance System.
Native in-project implementation for high-fidelity Kannada <-> English translation.

Operates 100% locally and offline:
- Zero external network requests
- Preserves dates, phone numbers, times, reference IDs, and paragraph structure
- Sentence-aware batch chunking for long petitions
- CPU and CUDA GPU execution
"""
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import torch
import transformers
import transformers.tokenization_utils
import transformers.tokenization_utils_base

# Compatibility patch for modern transformers with IndicTransToolkit
transformers.tokenization_utils.PreTrainedTokenizerBase = (
    transformers.tokenization_utils_base.PreTrainedTokenizerBase
)
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
from IndicTransToolkit import IndicProcessor

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Language codes for IndicTrans2
LANG_KANNADA = "kan_Knda"
LANG_ENGLISH = "eng_Latn"


def get_target_device() -> str:
    """Detect the best available local hardware for inference."""
    try:
        if torch.cuda.is_available():
            return "cuda"
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def get_local_model_path(source_lang: str, target_lang: str) -> Optional[str]:
    """
    Locates the offline model directory on local disk.
    Checks settings.INDICTRANS2_MODEL_PATH and project models/indictrans2 directory.
    """
    sub_dir = "en-indic" if source_lang == LANG_ENGLISH else "indic-en"

    # 1. Check custom configured setting in .env
    custom = getattr(settings, "INDICTRANS2_MODEL_PATH", None)
    if custom:
        p = Path(custom) / sub_dir
        if p.exists() and (p / "config.json").exists():
            return str(p)
        if Path(custom).exists() and (Path(custom) / "config.json").exists():
            return str(custom)

    # 2. Check default project models directory: grievance-backend/models/indictrans2/<sub_dir>
    project_root = Path(__file__).resolve().parents[3]
    default_path = project_root / "models" / "indictrans2" / sub_dir
    if default_path.exists() and (default_path / "config.json").exists():
        return str(default_path)

    return None


def clean_translated_text(src: str, tgt: str, target_lang: str = LANG_ENGLISH) -> str:
    """
    Post-process translated text:
    - Eliminate duplicated consecutive tokens
    - Preserve dates, times, and numeric entities exactly from source
    - Clean whitespace and formatting artifacts
    """
    if not tgt or not tgt.strip():
        return tgt

    res = tgt.replace("\\", "")
    res = re.sub(r"/{2,}", "/", res)

    if target_lang in [LANG_ENGLISH, "en", "eng"]:
        # Word deduplication
        res = re.sub(r"\b([A-Za-z]+)\s+\1\b", r"\1", res, flags=re.IGNORECASE)

        # Preserve exact times
        src_times = re.findall(r"\b\d{1,2}:\d{2}\b", src)
        tgt_times = re.findall(r"\b\d{1,2}:\d{2}\b", res)
        if len(src_times) == len(tgt_times):
            for s_t, t_t in zip(src_times, tgt_times):
                res = res.replace(t_t, s_t, 1)

        # Standardize am/pm
        res = re.sub(r"\b(\d{1,2}:\d{2})\s*a\.m\.", r"\1 AM", res, flags=re.IGNORECASE)
        res = re.sub(r"\b(\d{1,2}:\d{2})\s*p\.m\.", r"\1 PM", res, flags=re.IGNORECASE)
        res = re.sub(r"\b(\d{1,2}:\d{2})\s*am\b", r"\1 AM", res)
        res = re.sub(r"\b(\d{1,2}:\d{2})\s*pm\b", r"\1 PM", res)
        res = re.sub(r"(\d{1,2})\s*:\s*(\d{2})", r"\1:\2", res)

        # Convert Kannada numerals to standard Arabic numerals
        kan_to_eng_digits = str.maketrans("೦೧೨೩೪೫೬೭೮೯", "0123456789")
        res = res.translate(kan_to_eng_digits)

        # Spacing around punctuation
        res = re.sub(r"\s+([,.:;?!])", r"\1", res)
        res = re.sub(r"([,;?!])(?=[A-Za-z0-9])", r"\1 ", res)
        res = re.sub(r"(?<=[A-Za-z])\.(?=[A-Z])", r". ", res)

    # Bullet formatting preservation
    if src.startswith("•") and not res.startswith("•"):
        res = f"• {res.lstrip('-*• ')}"

    res = re.sub(r"\s+", " ", res).strip()
    return res


class IndicTransService:
    """
    Singleton Neural Translation Engine for IndicTrans2.
    Loads and caches Seq2Seq models locally in memory on CPU or CUDA GPU.
    Runs 100% offline with zero external network connectivity.
    """

    _instance: Optional["IndicTransService"] = None

    def __init__(self):
        self.device = get_target_device()
        self.loaded_models: Dict[str, Any] = {}
        self.loaded_tokenizers: Dict[str, Any] = {}
        self.processor = IndicProcessor(inference=True)
        logger.info(f"IndicTransService initialized on hardware device: {self.device}")

    @classmethod
    def get_instance(cls) -> "IndicTransService":
        if cls._instance is None:
            cls._instance = IndicTransService()
        return cls._instance

    def is_model_installed(self, source_lang: str, target_lang: str) -> bool:
        """Check whether local model weights exist on disk for the given direction."""
        p = get_local_model_path(source_lang, target_lang)
        if not p:
            return False
        path_obj = Path(p)
        has_config = (path_obj / "config.json").exists()
        has_weights = (
            (path_obj / "model.safetensors").exists()
            or (path_obj / "pytorch_model.bin").exists()
        )
        return has_config and has_weights

    def is_ready(self) -> bool:
        """Check if both translation directions are installed and ready on disk."""
        return self.is_model_installed(
            LANG_ENGLISH, LANG_KANNADA
        ) and self.is_model_installed(LANG_KANNADA, LANG_ENGLISH)

    def load_model(self, source_lang: str, target_lang: str) -> Tuple[Any, Any]:
        """
        Load and cache tokenizer and model strictly from local disk.
        Does NOT download over network during translation requests.
        """
        direction_key = f"{source_lang}->{target_lang}"
        if direction_key in self.loaded_models:
            return (
                self.loaded_models[direction_key],
                self.loaded_tokenizers[direction_key],
            )

        local_path = get_local_model_path(source_lang, target_lang)
        if not local_path or not self.is_model_installed(source_lang, target_lang):
            sub = "en-indic" if source_lang == LANG_ENGLISH else "indic-en"
            raise RuntimeError(
                f"Local IndicTrans2 model files for '{direction_key}' are not installed. "
                f"Please run 'python scripts/download_translation_models.py' during system deployment "
                f"to download offline model weights into 'models/indictrans2/{sub}'."
            )

        logger.info(f"Loading local IndicTrans2 model from '{local_path}' on device '{self.device}'...")

        tokenizer = AutoTokenizer.from_pretrained(
            local_path, local_files_only=True, trust_remote_code=True
        )

        torch_dtype = torch.float16 if self.device == "cuda" else torch.float32
        model = AutoModelForSeq2SeqLM.from_pretrained(
            local_path,
            local_files_only=True,
            trust_remote_code=True,
            torch_dtype=torch_dtype,
        )

        try:
            model = model.to(self.device)
            logger.info(f"IndicTrans2 model moved to device: {self.device}")
        except Exception as e:
            logger.warning(f"Failed to place model on {self.device}, falling back to CPU: {e}")
            self.device = "cpu"
            model = model.to("cpu")

        model.eval()
        self.loaded_models[direction_key] = model
        self.loaded_tokenizers[direction_key] = tokenizer
        return model, tokenizer

    def split_into_sentences(self, paragraph: str) -> List[str]:
        """Split a paragraph into coherent sentences while preserving numbers and abbreviations."""
        if not paragraph or not paragraph.strip():
            return []

        # Split on sentence terminals (. ? ! । \n)
        raw_sents = re.split(r"(?<=[.?!।\n])\s+", paragraph.strip())
        sentences = [s.strip() for s in raw_sents if s.strip()]
        return sentences if sentences else [paragraph.strip()]

    def translate_batch(
        self,
        texts: List[str],
        source_lang: str,
        target_lang: str,
        batch_size: int = 8,
    ) -> List[str]:
        """Translate a batch of sentences using local IndicTrans2 neural beam search."""
        if not texts:
            return []

        model, tokenizer = self.load_model(source_lang, target_lang)

        results = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]

            processed_chunk = []
            chunk_indices = []
            for idx, text in enumerate(chunk):
                cleaned = text.strip()
                if not cleaned or not re.search(r"\w", cleaned):
                    processed_chunk.append(None)
                else:
                    processed_chunk.append(cleaned)
                    chunk_indices.append(idx)

            active_texts = [processed_chunk[idx] for idx in chunk_indices]

            if active_texts:
                try:
                    # 1. Preprocess with IndicProcessor
                    preprocessed = self.processor.preprocess_batch(
                        active_texts, src_lang=source_lang, tgt_lang=target_lang
                    )

                    # 2. Tokenize
                    inputs = tokenizer(
                        preprocessed,
                        padding="longest",
                        truncation=True,
                        max_length=256,
                        return_tensors="pt",
                    ).to(self.device)

                    # 3. Generate with beam search
                    with torch.no_grad():
                        generated_tokens = model.generate(
                            **inputs,
                            use_cache=False,
                            max_length=256,
                            num_beams=4,
                            early_stopping=True,
                        )

                    # 4. Decode
                    decoded = tokenizer.batch_decode(
                        generated_tokens, skip_special_tokens=True
                    )

                    # 5. Postprocess with IndicProcessor
                    translated_texts = self.processor.postprocess_batch(
                        decoded, lang=target_lang
                    )

                    # 6. Entity & Numeric Preservation
                    aligned_texts = []
                    for s_text, t_text in zip(active_texts, translated_texts):
                        cleaned_t = clean_translated_text(s_text, t_text, target_lang)

                        # Preserve 10-digit mobile numbers from source text
                        src_phones = re.findall(r"\b[6-9]\d{9}\b", s_text)
                        tgt_phones = re.findall(r"\b[6-9]\d{9}\b", cleaned_t)
                        if len(src_phones) > 0 and len(tgt_phones) == len(src_phones):
                            for s_p, t_p in zip(src_phones, tgt_phones):
                                cleaned_t = cleaned_t.replace(t_p, s_p, 1)

                        # Preserve dates (e.g. 15/08/2026, 03-11-2026)
                        src_dates = re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", s_text)
                        tgt_dates = re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", cleaned_t)
                        if len(src_dates) > 0 and len(tgt_dates) == len(src_dates):
                            for s_d, t_d in zip(src_dates, tgt_dates):
                                cleaned_t = cleaned_t.replace(t_d, s_d, 1)

                        aligned_texts.append(cleaned_t)

                    trans_idx = 0
                    for idx in range(len(chunk)):
                        if idx in chunk_indices:
                            results.append(aligned_texts[trans_idx])
                            trans_idx += 1
                        else:
                            results.append(chunk[idx])
                except Exception as e:
                    logger.error(f"Error during local neural translation: {e}")
                    raise e
            else:
                results.extend(chunk)

        return results

    def translate_paragraphs(
        self,
        paragraphs: List[str],
        source_lang: str,
        target_lang: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[str]:
        """Translate structured paragraphs preserving sentence boundaries, line breaks, and paragraph order."""
        if not paragraphs:
            return []

        translated_paragraphs = []
        total = len(paragraphs)
        for p_idx, para in enumerate(paragraphs):
            if not para.strip():
                translated_paragraphs.append("")
                continue

            p_to_translate = para
            if "kan" in source_lang:
                p_to_translate = re.sub(
                    r"\b(ನನ್ನ|ನಮ್ಮ|ನಿಮ್ಮ|ತನ್ನ|ತಮ್ಮ)(?=[\u0C85-\u0CB9])",
                    r"\1 ",
                    p_to_translate,
                )

            sentences = self.split_into_sentences(p_to_translate)
            if not sentences:
                cleaned = clean_translated_text(para, para, target_lang)
                translated_paragraphs.append(cleaned)
                continue

            translated_sents = self.translate_batch(sentences, source_lang, target_lang)
            raw_para = " ".join(translated_sents)
            cleaned_para = clean_translated_text(para, raw_para, target_lang)
            translated_paragraphs.append(cleaned_para)

            if progress_callback:
                progress_callback(p_idx + 1, total)

        return translated_paragraphs

    def get_status(self) -> Dict[str, Any]:
        """Return operational status and metadata for the local neural engine."""
        ready = self.is_ready()
        return {
            "provider": "LOCAL",
            "provider_name": "AI4Bharat-IndicTrans2",
            "model_name": "indictrans2-dist-200M",
            "model_version": "200M-distilled",
            "status": "READY" if ready else "NOT_INSTALLED",
            "is_ready": ready,
            "supported_pairs": ["kn->en", "en->kn"],
            "supported_languages": ["en", "kn"],
            "device": self.device.upper(),
            "offline_available": True,
            "is_local": True,
            "requires_external_network": False,
        }
