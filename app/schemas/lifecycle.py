"""
Pydantic schemas for Grievance Lifecycle Transitions, Resolution, Closure, Reopen, and Timeline.
"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class InformationRequestCreate(BaseModel):
    information_required: str = Field(
        ...,
        min_length=5,
        max_length=2000,
        description="Detailed description of additional information requested from citizen or external agency",
    )
    next_follow_up_at: Optional[datetime] = Field(
        default=None,
        description="Optional scheduled follow-up date for awaiting information",
    )


class ResumeInvestigationCreate(BaseModel):
    information_received: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Summary of information received from citizen or agency",
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Operational notes or reason for resuming investigation",
    )


class ReopenCreate(BaseModel):
    reopen_reason: str = Field(
        ...,
        min_length=5,
        max_length=1000,
        description="Mandatory administrative justification for reopening a resolved grievance",
    )

class ResolutionCreate(BaseModel):
    resolution_summary: str = Field(
        ...,
        min_length=5,
        max_length=255,
        description="High-level synopsis of the grievance resolution",
    )
    resolution_details: str = Field(
        ...,
        min_length=10,
        description="Comprehensive technical and departmental findings and resolution steps taken",
    )
    supporting_notes: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Optional supporting notes or citizen communication details",
    )
    resolution_date: Optional[datetime] = Field(
        default=None,
        description="Official resolution date (defaults to current UTC time if omitted)",
    )


class ClosureCreate(BaseModel):
    closure_reason: str = Field(
        ...,
        min_length=3,
        max_length=255,
        description="Administrative justification for closing the grievance",
    )
    closure_notes: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Closing notes or citizen acknowledgment remarks",
    )


class TimelineEvent(BaseModel):
    id: uuid.UUID
    action: str
    created_at: datetime
    performing_user_id: Optional[uuid.UUID] = None
    performing_user_name: Optional[str] = None
    description: str
    old_status: Optional[str] = None
    new_status: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)


class TimelineResponse(BaseModel):
    grievance_id: uuid.UUID
    grievance_number: str
    current_status: str
    total_events: int
    timeline: List[TimelineEvent]

