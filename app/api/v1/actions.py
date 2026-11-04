"""
Follow-up Action REST API Endpoints (Stage 10C).

Endpoints:
  POST /follow-up-tasks/{task_id}/actions
  GET  /follow-up-tasks/{task_id}/actions
  GET  /grievances/{grievance_id}/follow-up-actions
  GET  /follow-up-actions/{action_id}
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_permission
from app.core.database import get_db
from app.core.roles import Permission
from app.models.user import User
from app.schemas.action import (
    FollowUpActionCreate,
    FollowUpActionResponse,
    PaginatedActionResponse,
)
from app.services import action_service

actions_router = APIRouter(tags=["Follow-up Actions"])


@actions_router.post(
    "/follow-up-tasks/{task_id}/actions",
    response_model=FollowUpActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record action against a follow-up task",
    description=(
        "Records an executed follow-up action (phone call, field visit, citizen response, etc.), "
        "marks the scheduled task as COMPLETED, and optionally schedules a subsequent task. "
        "Completing the task does NOT resolve or close the grievance."
    ),
)
def record_follow_up_action(
    task_id: uuid.UUID,
    action_in: FollowUpActionCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.LOG_FOLLOW_UP_ACTION)),
) -> FollowUpActionResponse:
    ip = request.client.host if request.client else None
    action, next_task_id = action_service.record_follow_up_action(
        db=db,
        task_id=task_id,
        action_in=action_in,
        current_user=current_user,
        ip_address=ip,
    )
    resp = FollowUpActionResponse.model_validate(action)
    resp.next_scheduled_task_id = next_task_id
    return resp


@actions_router.get(
    "/follow-up-tasks/{task_id}/actions",
    response_model=PaginatedActionResponse,
    summary="List actions recorded for a follow-up task",
)
def list_task_actions(
    task_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_FOLLOW_UPS)),
) -> PaginatedActionResponse:
    items, total, total_pages = action_service.list_actions_for_task(
        db=db,
        task_id=task_id,
        current_user=current_user,
        page=page,
        page_size=page_size,
    )
    return PaginatedActionResponse(
        items=[FollowUpActionResponse.model_validate(a) for a in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@actions_router.get(
    "/grievances/{grievance_id}/follow-up-actions",
    response_model=PaginatedActionResponse,
    summary="List all follow-up actions recorded for a grievance",
)
def list_grievance_actions(
    grievance_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_FOLLOW_UPS)),
) -> PaginatedActionResponse:
    items, total, total_pages = action_service.list_actions_for_grievance(
        db=db,
        grievance_id=grievance_id,
        current_user=current_user,
        page=page,
        page_size=page_size,
    )
    return PaginatedActionResponse(
        items=[FollowUpActionResponse.model_validate(a) for a in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@actions_router.get(
    "/follow-up-actions/{action_id}",
    response_model=FollowUpActionResponse,
    summary="Retrieve a specific follow-up action by ID",
)
def get_follow_up_action(
    action_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_FOLLOW_UPS)),
) -> FollowUpActionResponse:
    action = action_service.get_action_by_id(
        db=db,
        action_id=action_id,
        current_user=current_user,
    )
    return FollowUpActionResponse.model_validate(action)

