"""
Pydantic schemas for Grievance management:
- GrievanceCreate: Registration payload
- GrievanceUpdate: Controlled update payload
- GrievanceConfirmDepartment: Department confirmation payload
- GrievanceListResponse: Summarized response for paginated listings
- GrievanceDetailResponse: Detailed response with complete metadata
- PaginatedGrievanceResponse: Pagination container
"""
import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Allowed constants
SUPPORTED_LANGUAGES = {"en", "kn"}
ALLOWED_SOURCES = {
    "ONLINE_PORTAL",
    "IN_PERSON",
    "WRITTEN_LETTER",
    "HELPLINE",
    "SOCIAL_MEDIA",
}
ALLOWED_PRIORITIES = {"LOW", "MEDIUM", "HIGH", "URGENT"}
ALLOWED_STATUSES = {
    "NEW",
    "UNDER_REVIEW",
    "ASSIGNED",
    "AWAITING_INFORMATION",
    "IN_PROGRESS",
    "RESOLVED",
    "CLOSED",
}


class GrievanceCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=255, description="Brief summary of grievance")
    description: str = Field(..., min_length=5, description="Full description of the issue")
    source: str = Field(
        default="ONLINE_PORTAL",
        description="Source channel (ONLINE_PORTAL, IN_PERSON, WRITTEN_LETTER, HELPLINE, SOCIAL_MEDIA)",
    )
    original_language: str = Field(default="en", description="Language code: en or kn")
    original_text: Optional[str] = Field(default=None, description="Original text if received in native language")
    translated_language: Optional[str] = Field(default=None, description="Translated language code")
    translated_text: Optional[str] = Field(default=None, description="Translated text into English or Kannada")
    neutral_summary: Optional[str] = Field(default=None, description="Objective, non-emotional synopsis")
    suggested_department_id: Optional[uuid.UUID] = Field(default=None, description="Auto-suggested or operator-suggested department")
    category_id: Optional[uuid.UUID] = Field(default=None, description="Category under the suggested/confirmed department")
    office_id: Optional[uuid.UUID] = Field(default=None, description="Specific jurisdictional office if applicable")
    priority: str = Field(default="MEDIUM", description="Grievance priority (LOW, MEDIUM, HIGH, URGENT)")
    estimated_resolution_date: Optional[datetime] = Field(default=None, description="Estimated target resolution date")

    @field_validator("original_language")
    @classmethod
    def validate_original_language(cls, v: str) -> str:
        clean = v.strip().lower()
        if clean not in SUPPORTED_LANGUAGES:
            raise ValueError(f"original_language must be one of {sorted(SUPPORTED_LANGUAGES)}")
        return clean

    @field_validator("translated_language")
    @classmethod
    def validate_translated_language(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        clean = v.strip().lower()
        if clean not in SUPPORTED_LANGUAGES:
            raise ValueError(f"translated_language must be one of {sorted(SUPPORTED_LANGUAGES)}")
        return clean

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        clean = v.strip().upper()
        if clean not in ALLOWED_SOURCES:
            raise ValueError(f"source must be one of {sorted(ALLOWED_SOURCES)}")
        return clean

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        clean = v.strip().upper()
        if clean not in ALLOWED_PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(ALLOWED_PRIORITIES)}")
        return clean


class GrievanceConfirmDepartment(BaseModel):
    department_id: uuid.UUID = Field(..., description="Confirmed government department ID")
    confirmation_notes: Optional[str] = Field(default=None, max_length=1000, description="Notes on why this department was confirmed")


class GrievanceUpdate(BaseModel):
    """
    Permitted grievance modification fields for the general PATCH endpoint.
    Protected fields (status, grievance_number, confirmed_department_id,
    assigned_to_user_id, created_by) must NOT appear in the request body.
    Submitting any protected field returns HTTP 422 Unprocessable Entity.
    """
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, min_length=5)
    neutral_summary: Optional[str] = Field(default=None)
    priority: Optional[str] = Field(default=None)
    category_id: Optional[uuid.UUID] = Field(default=None)
    translated_text: Optional[str] = Field(default=None)
    estimated_resolution_date: Optional[datetime] = Field(default=None)

    model_config = ConfigDict(extra="forbid")  # HTTP 422 for any unexpected / protected field

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        clean = v.strip().upper()
        if clean not in ALLOWED_PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(ALLOWED_PRIORITIES)}")
        return clean


class GrievanceListResponse(BaseModel):
    id: uuid.UUID
    grievance_number: str
    title: str
    neutral_summary: Optional[str] = None
    source: str
    status: str
    priority: str
    date_received: datetime
    suggested_department_id: Optional[uuid.UUID] = None
    confirmed_department_id: Optional[uuid.UUID] = None
    assigned_to_user_id: Optional[uuid.UUID] = None
    next_follow_up_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GrievanceDetailResponse(BaseModel):
    id: uuid.UUID
    grievance_number: str
    title: str
    description: str
    neutral_summary: Optional[str] = None
    source: str
    original_language: str
    translated_language: Optional[str] = None
    original_text: Optional[str] = None
    translated_text: Optional[str] = None

    suggested_department_id: Optional[uuid.UUID] = None
    confirmed_department_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    office_id: Optional[uuid.UUID] = None
    assigned_to_user_id: Optional[uuid.UUID] = None

    status: str
    priority: str
    date_received: datetime
    estimated_resolution_date: Optional[datetime] = None
    last_action_at: Optional[datetime] = None
    next_follow_up_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    created_by: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaginatedGrievanceResponse(BaseModel):
    items: List[GrievanceListResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

