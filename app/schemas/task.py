"""
Pydantic schemas for Follow-up Task management (Stage 10B).
"""
from datetime import datetime
from typing import List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class FollowUpTaskResponse(BaseModel):
    """Serialised representation of a FollowUpTask record."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    grievance_id: uuid.UUID
    assigned_to_user_id: Optional[uuid.UUID] = None
    policy_id: Optional[uuid.UUID] = None
    due_at: datetime
    status: str  # SCHEDULED | COMPLETED | CANCELLED
    completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ManualScheduleFollowUpTaskCreate(BaseModel):
    """
    Payload for manually scheduling a follow-up task on a specific grievance.
    due_at must be provided explicitly for manual scheduling.
    """
    due_at: datetime = Field(
        ...,
        description="UTC datetime when the follow-up task is due.",
    )
    assigned_to_user_id: Optional[uuid.UUID] = Field(
        None,
        description="Optional officer to assign this task to. Defaults to current grievance officer.",
    )
    policy_id: Optional[uuid.UUID] = Field(
        None,
        description="Follow-up policy driving this task (optional for manual tasks).",
    )


class PaginatedTaskResponse(BaseModel):
    """Paginated wrapper for follow-up task lists."""
    model_config = ConfigDict(from_attributes=True)

    items: List[FollowUpTaskResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

