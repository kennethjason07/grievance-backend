"""
Pydantic schemas for Follow-up Action recording (Stage 10C).
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ActionTypeEnum(str, Enum):
    CALL_ATTEMPTED = "CALL_ATTEMPTED"
    CITIZEN_CONTACTED = "CITIZEN_CONTACTED"
    NO_RESPONSE = "NO_RESPONSE"
    DOCUMENTS_REQUESTED = "DOCUMENTS_REQUESTED"
    DOCUMENT_REQUESTED = "DOCUMENT_REQUESTED"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    FIELD_VISIT = "FIELD_VISIT"
    DEPARTMENT_RESPONSE = "DEPARTMENT_RESPONSE"
    OFFICER_CONTACTED = "OFFICER_CONTACTED"
    EMAIL_SENT = "EMAIL_SENT"
    LETTER_SENT = "LETTER_SENT"
    STATUS_UPDATED = "STATUS_UPDATED"
    OTHER = "OTHER"


class FollowUpActionCreate(BaseModel):
    """
    Payload for recording a follow-up action against a follow-up task.
    """
    model_config = ConfigDict(extra="forbid")

    action_type: str = Field(
        ...,
        description="Type of follow-up action performed.",
        examples=["CALL_ATTEMPTED", "CITIZEN_CONTACTED", "NO_RESPONSE", "RESPONSE_RECEIVED"],
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Detailed description of the follow-up attempt or activity.",
    )
    outcome: Optional[str] = Field(
        None,
        max_length=5000,
        description="Result or outcome of the action.",
    )
    notes: Optional[str] = Field(
        None,
        max_length=5000,
        description="Optional additional notes (persisted in outcome).",
    )
    response_received: Optional[bool] = Field(
        None,
        description="Whether a substantive citizen/department response was received. Defaults to True if action_type is RESPONSE_RECEIVED.",
    )
    action_taken_at: Optional[datetime] = Field(
        None,
        description="Timestamp when the action occurred. Defaults to current UTC time.",
    )
    next_follow_up_at: Optional[datetime] = Field(
        None,
        description="Optional future datetime to automatically schedule the next follow-up task.",
    )

    @field_validator("action_type")
    @classmethod
    def validate_action_type(cls, v: str) -> str:
        cleaned = v.strip().upper()
        valid_values = {e.value for e in ActionTypeEnum}
        if cleaned not in valid_values:
            raise ValueError(
                f"Invalid action_type '{v}'. Allowed types: {', '.join(sorted(valid_values))}"
            )
        return cleaned


class FollowUpActionResponse(BaseModel):
    """
    Serialised response representation for a recorded FollowUpAction.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    grievance_id: uuid.UUID
    follow_up_task_id: Optional[uuid.UUID] = None
    performed_by_user_id: uuid.UUID
    action_type: str
    description: str
    outcome: Optional[str] = None
    response_received: bool
    action_taken_at: datetime
    next_follow_up_at: Optional[datetime] = None
    created_at: datetime
    next_scheduled_task_id: Optional[uuid.UUID] = None


class PaginatedActionResponse(BaseModel):
    """Paginated list of follow-up actions."""
    model_config = ConfigDict(from_attributes=True)

    items: List[FollowUpActionResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

