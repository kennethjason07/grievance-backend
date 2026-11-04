"""
Pydantic schemas for Notification management and reminder processing (Stage 10D).
"""
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class NotificationResponse(BaseModel):
    """Serialised representation of a Notification record."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    grievance_id: uuid.UUID
    follow_up_task_id: Optional[uuid.UUID] = None
    recipient_user_id: Optional[uuid.UUID] = None
    recipient_name: str
    recipient_contact: str
    notification_type: str
    channel: str
    subject: Optional[str] = None
    message: str
    status: str
    scheduled_at: Optional[datetime] = None
    generated_at: datetime
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    response_received_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    created_at: datetime


class PaginatedNotificationResponse(BaseModel):
    """Paginated list of notifications."""
    model_config = ConfigDict(from_attributes=True)

    items: List[NotificationResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class ProcessRemindersRequest(BaseModel):
    """Optional payload to trigger reminder processing with custom reference time."""
    reference_time: Optional[datetime] = Field(
        None,
        description="Optional UTC datetime for deterministic reminder evaluation.",
    )


class ProcessRemindersResponse(BaseModel):
    """Summary of executed reminder generation process."""
    processed_tasks: int
    generated_reminders: int
    skipped_or_conflicts: int
    failures: int
    evaluated_at: datetime

