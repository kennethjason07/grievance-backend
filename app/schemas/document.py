"""
Pydantic schemas for Document management, OCR extraction, and review.
Internal file paths are strictly omitted from client responses.
"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

ALLOWED_DOCUMENT_TYPES = {
    "PETITION",
    "IDENTITY_PROOF",
    "EVIDENCE",
    "REPORT",
    "RESPONSE",
    "TRANSLATION",
    "OTHER",
}

ALLOWED_OCR_STATUSES = {
    "NOT_STARTED",
    "PENDING",
    "PROCESSING",
    "COMPLETED",
    "FAILED",
    "NEEDS_REVIEW",
}


class DocumentResponse(BaseModel):
    id: uuid.UUID
    grievance_id: uuid.UUID
    document_name: str
    original_filename: str
    mime_type: str
    file_size: int
    document_type: str
    language: str
    parent_document_id: Optional[uuid.UUID] = None
    uploaded_by: Optional[uuid.UUID] = None
    created_at: datetime
    download_url: Optional[str] = None

    # OCR extraction summary
    ocr_status: str = "NOT_STARTED"
    ocr_language: Optional[str] = None
    ocr_processed_at: Optional[datetime] = None
    ocr_error: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class DocumentListResponse(BaseModel):
    items: List[DocumentResponse]
    total: int


class DocumentOcrTriggerRequest(BaseModel):
    languages: Optional[str] = Field(
        default=None,
        description="Optional Tesseract language code (e.g. 'eng', 'kan', 'eng+kan'). Defaults to system configured languages.",
    )


class DocumentOcrReviewRequest(BaseModel):
    reviewed_text: str = Field(..., min_length=1, description="Corrected or reviewed text content")
    mark_status: Optional[str] = Field(
        default="COMPLETED",
        description="Target status after review: COMPLETED or NEEDS_REVIEW",
    )


class PageOcrData(BaseModel):
    page_number: int
    extracted_text: str
    method: str  # 'embedded' | 'ocr' | 'docx' | 'txt'
    confidence: Optional[float] = None
    char_count: int


class DocumentOcrResponse(BaseModel):
    id: uuid.UUID
    grievance_id: uuid.UUID
    ocr_status: str
    extracted_text: Optional[str] = None
    reviewed_text: Optional[str] = None
    ocr_language: Optional[str] = None
    ocr_pages_data: Optional[List[Dict[str, Any]]] = None
    ocr_processed_at: Optional[datetime] = None
    ocr_error: Optional[str] = None
    ocr_reviewed_at: Optional[datetime] = None
    ocr_reviewed_by: Optional[uuid.UUID] = None

    model_config = ConfigDict(from_attributes=True)


class DocumentEntityExtractionRequest(BaseModel):
    text: str = Field(..., description="Petition text from which to extract form fields")


class ExtractedFormFields(BaseModel):
    citizen_name: str = ""
    citizen_name_kn: Optional[str] = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    address_kn: Optional[str] = ""
    district: str = "Bengaluru Urban"
    district_kn: Optional[str] = "ಬೆಂಗಳೂರು ನಗರ"
    taluk: str = "Bengaluru South"
    taluk_kn: Optional[str] = "ಬೆಂಗಳೂರು ದಕ್ಷಿಣ"
    village_locality: str = ""
    village_locality_kn: Optional[str] = ""
    title: str = ""
    title_kn: Optional[str] = ""
    category: str = "General Grievance"
    category_kn: Optional[str] = "ಸಾಮಾನ್ಯ ನಾಗರಿಕ ಅಹವಾಲು"
    incident_date: str = ""
    incident_location: str = ""
    incident_location_kn: Optional[str] = ""
    description: str = ""
    description_kn: Optional[str] = ""
    requested_action: str = ""
    requested_action_kn: Optional[str] = ""
    assignment_instructions_en: Optional[str] = ""
    assignment_instructions_kn: Optional[str] = ""
    language: str = "english"
    confidence_scores: Dict[str, str] = Field(default_factory=dict)
