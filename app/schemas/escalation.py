"""
Pydantic schemas for Escalation management and processing (Stage 10E).
"""
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class EscalationResponse(BaseModel):
    """Serialised representation of an Escalation record."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    grievance_id: uuid.UUID
    follow_up_task_id: Optional[uuid.UUID] = None
    escalated_from_user_id: Optional[uuid.UUID] = None
    escalated_to_user_id: uuid.UUID
    reason: str
    trigger_date: datetime
    escalated_at: datetime
    status: str  # PENDING, ACKNOWLEDGED, RESOLVED, DISMISSED
    resolution_notes: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime


class EscalationResolveRequest(BaseModel):
    """Payload to resolve an active escalation."""
    model_config = ConfigDict(extra="forbid")

    resolution_notes: Optional[str] = Field(
        None,
        max_length=5000,
        description="Optional notes documenting how the escalation was resolved.",
    )


class EscalationDismissRequest(BaseModel):
    """Payload to dismiss an active escalation."""
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        ...,
        min_length=5,
        max_length=5000,
        description="Mandatory reason explaining why the escalation is dismissed.",
    )


class PaginatedEscalationResponse(BaseModel):
    """Paginated list of escalations."""
    model_config = ConfigDict(from_attributes=True)

    items: List[EscalationResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class ProcessEscalationsRequest(BaseModel):
    """Optional payload to trigger escalation processing with custom reference time."""
    reference_time: Optional[datetime] = Field(
        None,
        description="Optional UTC datetime for deterministic escalation evaluation.",
    )


class ProcessEscalationsResponse(BaseModel):
    """Summary of executed escalation generation process."""
    evaluated_grievances: int
    escalated_count: int
    skipped_or_existing: int
    failures: int
    evaluated_at: datetime

