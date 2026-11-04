"""
Notification and Reminder REST API Endpoints (Stage 10D).

Endpoints:
  GET   /notifications
  GET   /notifications/{notification_id}
  PATCH /notifications/{notification_id}/acknowledge
  POST  /notifications/process-reminders
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_permission
from app.core.database import get_db
from app.core.roles import Permission
from app.models.user import User
from app.schemas.notification import (
    NotificationResponse,
    PaginatedNotificationResponse,
    ProcessRemindersRequest,
    ProcessRemindersResponse,
)
from app.services import notification_service

notifications_router = APIRouter(tags=["Notifications & Reminders"])


@notifications_router.get(
    "/notifications",
    response_model=PaginatedNotificationResponse,
    summary="List notifications with filters and scoping",
    description="Lists notifications. Department and recipient scoping are automatically applied.",
)
def list_notifications(
    grievance_id: Optional[uuid.UUID] = Query(None, description="Filter by grievance ID"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status (e.g. GENERATED, ACKNOWLEDGED)"),
    channel_filter: Optional[str] = Query(None, alias="channel", description="Filter by channel (e.g. IN_APP, EMAIL)"),
    recipient_user_id: Optional[uuid.UUID] = Query(None, description="Filter by recipient user ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_NOTIFICATIONS)),
) -> PaginatedNotificationResponse:
    items, total, total_pages = notification_service.list_notifications(
        db=db,
        current_user=current_user,
        grievance_id=grievance_id,
        status_filter=status_filter,
        channel_filter=channel_filter,
        recipient_user_id=recipient_user_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedNotificationResponse(
        items=[NotificationResponse.model_validate(n) for n in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@notifications_router.get(
    "/notifications/{notification_id}",
    response_model=NotificationResponse,
    summary="Retrieve single notification by ID",
)
def get_notification(
    notification_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_NOTIFICATIONS)),
) -> NotificationResponse:
    notification = notification_service.get_notification_by_id(
        db=db,
        notification_id=notification_id,
        current_user=current_user,
    )
    return NotificationResponse.model_validate(notification)


@notifications_router.patch(
    "/notifications/{notification_id}/acknowledge",
    response_model=NotificationResponse,
    summary="Acknowledge an in-app notification",
    description="Acknowledge a notification. Acknowledging a notification does NOT resolve the grievance.",
)
def acknowledge_notification(
    notification_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationResponse:
    ip = request.client.host if request.client else None
    notification = notification_service.acknowledge_notification(
        db=db,
        notification_id=notification_id,
        current_user=current_user,
        ip_address=ip,
    )
    return NotificationResponse.model_validate(notification)


@notifications_router.post(
    "/notifications/process-reminders",
    response_model=ProcessRemindersResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger reminder generation evaluation",
    description="Synchronous evaluation of due reminders across scheduled follow-up tasks.",
)
def trigger_process_reminders(
    payload: Optional[ProcessRemindersRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_NOTIFICATIONS)),
) -> ProcessRemindersResponse:
    ref_time = payload.reference_time if payload else None
    result = notification_service.process_due_reminders(db=db, now=ref_time)
    return ProcessRemindersResponse(
        processed_tasks=result["processed_tasks"],
        generated_reminders=result["generated_reminders"],
        skipped_or_conflicts=result["skipped_or_conflicts"],
        failures=result["failures"],
        evaluated_at=result["evaluated_at"],
    )

