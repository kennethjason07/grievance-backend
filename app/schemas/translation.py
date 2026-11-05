import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_TRANSLATION_STATUSES = {
    "PENDING",
    "PROCESSING",
    "COMPLETED",
    "FAILED",
    "UNDER_REVIEW",
    "APPROVED",
}

ALLOWED_LANGUAGES = {"en", "kn"}
ALLOWED_TEXT_SOURCES = {"RAW_OCR", "REVIEWED_OCR"}


class TranslationTriggerRequest(BaseModel):
    source_language: str = Field("kn", description="Source language code: 'kn' or 'en'")
    target_language: str = Field("en", description="Target language code: 'en' or 'kn'")
    text_source: str = Field("REVIEWED_OCR", description="'REVIEWED_OCR' (prefer verified text) or 'RAW_OCR'")

    @field_validator("source_language", "target_language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        lang = v.strip().lower()
        if lang not in ALLOWED_LANGUAGES:
            raise ValueError(f"Language '{v}' is not supported. Supported codes: {sorted(ALLOWED_LANGUAGES)}")
        return lang

    @field_validator("text_source")
    @classmethod
    def validate_text_source(cls, v: str) -> str:
        src = v.strip().upper()
        if src not in ALLOWED_TEXT_SOURCES:
            raise ValueError(f"text_source must be one of {sorted(ALLOWED_TEXT_SOURCES)}")
        return src


class TranslationReviewRequest(BaseModel):
    corrected_text: str = Field(..., min_length=1, description="Operator reviewed and corrected translation text")


class TranslationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    source_language: str
    target_language: str
    text_source: str
    source_text: str
    translated_text: Optional[str] = None
    corrected_text: Optional[str] = None
    provider_name: str
    model_version: Optional[str] = None
    status: str
    created_by: Optional[uuid.UUID] = None
    created_at: datetime
    reviewed_by: Optional[uuid.UUID] = None
    reviewed_at: Optional[datetime] = None
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    error_message: Optional[str] = None


class TranslationEngineInfo(BaseModel):
    provider_name: str
    model_version: str
    supported_pairs: List[str]
    is_local: bool
    requires_external_network: bool
    device: str
    status: str

