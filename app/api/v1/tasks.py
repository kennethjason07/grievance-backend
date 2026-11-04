"""
Follow-up Task REST API endpoints (Stage 10B).

Endpoints:
  POST   /grievances/{grievance_id}/schedule-follow-up  → Manually schedule a follow-up task
  GET    /follow-up-tasks                               → List tasks (with filters)
  GET    /follow-up-tasks/{task_id}                     → Retrieve single task
  PATCH  /follow-up-tasks/{task_id}/cancel              → Cancel a SCHEDULED task
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_permission
from app.core.database import get_db
from app.core.roles import Permission
from app.models.user import User
from app.schemas.task import FollowUpTaskResponse, ManualScheduleFollowUpTaskCreate, PaginatedTaskResponse
from app.services import task_service

tasks_router = APIRouter(tags=["Follow-up Tasks"])


@tasks_router.post(
    "/grievances/{grievance_id}/schedule-follow-up",
    response_model=FollowUpTaskResponse,
    status_code=201,
    summary="Manually schedule a follow-up task on a grievance",
)
def schedule_follow_up_task(
    grievance_id: uuid.UUID,
    task_in: ManualScheduleFollowUpTaskCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_FOLLOW_UPS)),
) -> FollowUpTaskResponse:
    """
    Manually create a follow-up task for the specified grievance.
    - If a SCHEDULED task already exists, it is cancelled and replaced.
    - due_at must be a future datetime.
    - Requires MANAGE_FOLLOW_UPS permission.
    """
    task = task_service.schedule_follow_up_task(
        db=db,
        grievance_id=grievance_id,
        task_in=task_in,
        current_user=current_user,
        ip_address=request.client.host if request.client else None,
    )
    return FollowUpTaskResponse.model_validate(task)


@tasks_router.get(
    "/follow-up-tasks",
    response_model=PaginatedTaskResponse,
    summary="List follow-up tasks with optional filters",
)
def list_follow_up_tasks(
    grievance_id: Optional[uuid.UUID] = Query(None, description="Filter by grievance ID"),
    status_filter: Optional[str] = Query(None, alias="status", description="SCHEDULED | COMPLETED | CANCELLED"),
    overdue_only: bool = Query(False, description="Return only overdue SCHEDULED tasks"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_FOLLOW_UPS)),
) -> PaginatedTaskResponse:
    """
    List follow-up tasks filtered by grievance, status, or overdue flag.
    Department isolation applied for non-SUPER_ADMIN users.
    """
    items, total, total_pages = task_service.list_tasks(
        db=db,
        current_user=current_user,
        grievance_id=grievance_id,
        status_filter=status_filter,
        overdue_only=overdue_only,
        page=page,
        page_size=page_size,
    )
    return PaginatedTaskResponse(
        items=[FollowUpTaskResponse.model_validate(t) for t in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@tasks_router.get(
    "/follow-up-tasks/{task_id}",
    response_model=FollowUpTaskResponse,
    summary="Retrieve a single follow-up task by ID",
)
def get_follow_up_task(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_FOLLOW_UPS)),
) -> FollowUpTaskResponse:
    """
    Return detailed information about a specific follow-up task.
    Department isolation enforced.
    """
    task = task_service.get_task_by_id(db=db, task_id=task_id, current_user=current_user)
    return FollowUpTaskResponse.model_validate(task)


@tasks_router.patch(
    "/follow-up-tasks/{task_id}/cancel",
    response_model=FollowUpTaskResponse,
    summary="Cancel a SCHEDULED follow-up task",
)
def cancel_follow_up_task(
    task_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_FOLLOW_UPS)),
) -> FollowUpTaskResponse:
    """
    Cancel a SCHEDULED follow-up task.
    Only SCHEDULED tasks can be cancelled.
    Requires MANAGE_FOLLOW_UPS permission.
    """
    task = task_service.cancel_task(
        db=db,
        task_id=task_id,
        current_user=current_user,
        ip_address=request.client.host if request.client else None,
    )
    return FollowUpTaskResponse.model_validate(task)

