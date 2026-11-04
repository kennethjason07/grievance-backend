"""
API router for Follow-up Policy Management (Stage 10A).
"""
import logging
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_permission
from app.core.database import get_db
from app.core.roles import Permission
from app.models.user import User
from app.schemas.policy import (
    FollowUpPolicyCreate,
    FollowUpPolicyResponse,
    FollowUpPolicyStatusUpdate,
    FollowUpPolicyUpdate,
    PaginatedPolicyResponse,
)
from app.services import policy_service

logger = logging.getLogger(__name__)

policies_router = APIRouter(tags=["Follow-up Policies"])


@policies_router.post(
    "/policies",
    response_model=FollowUpPolicyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Follow-up Policy",
    description="Configures department-specific reminder intervals, max reminders, and escalation thresholds.",
)
def create_policy(
    payload: FollowUpPolicyCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_POLICIES)),
) -> FollowUpPolicyResponse:
    ip = request.client.host if request.client else None
    policy = policy_service.create_policy(
        db=db,
        policy_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return FollowUpPolicyResponse.model_validate(policy)


@policies_router.get(
    "/policies",
    response_model=PaginatedPolicyResponse,
    status_code=status.HTTP_200_OK,
    summary="List Follow-up Policies",
    description="Returns paginated policies scoped to the caller's authorized department.",
)
def list_policies(
    department_id: Optional[uuid.UUID] = Query(None, description="Filter by department ID"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PaginatedPolicyResponse:
    items, total, total_pages = policy_service.list_policies(
        db=db,
        current_user=current_user,
        department_id=department_id,
        is_active=is_active,
        page=page,
        page_size=page_size,
    )
    return PaginatedPolicyResponse(
        items=[FollowUpPolicyResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@policies_router.get(
    "/policies/{policy_id}",
    response_model=FollowUpPolicyResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Policy by ID",
    description="Retrieves follow-up policy details, subject to department isolation.",
)
def get_policy(
    policy_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FollowUpPolicyResponse:
    policy = policy_service.get_policy_by_id(
        db=db,
        policy_id=policy_id,
        current_user=current_user,
    )
    return FollowUpPolicyResponse.model_validate(policy)


@policies_router.get(
    "/departments/{department_id}/policies/active",
    response_model=FollowUpPolicyResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Department Active Policy",
    description="Retrieves the currently active policy designated for a department.",
)
def get_active_policy(
    department_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FollowUpPolicyResponse:
    policy = policy_service.get_active_department_policy(
        db=db,
        department_id=department_id,
        current_user=current_user,
    )
    return FollowUpPolicyResponse.model_validate(policy)


@policies_router.patch(
    "/policies/{policy_id}",
    response_model=FollowUpPolicyResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Follow-up Policy",
    description="Modifies policy thresholds without silently mutating existing task schedules.",
)
def update_policy(
    policy_id: uuid.UUID,
    payload: FollowUpPolicyUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_POLICIES)),
) -> FollowUpPolicyResponse:
    ip = request.client.host if request.client else None
    policy = policy_service.update_policy(
        db=db,
        policy_id=policy_id,
        policy_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return FollowUpPolicyResponse.model_validate(policy)


@policies_router.patch(
    "/policies/{policy_id}/status",
    response_model=FollowUpPolicyResponse,
    status_code=status.HTTP_200_OK,
    summary="Activate or Deactivate Policy",
    description="Toggles the active state of a follow-up policy.",
)
def set_policy_status(
    policy_id: uuid.UUID,
    payload: FollowUpPolicyStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_POLICIES)),
) -> FollowUpPolicyResponse:
    ip = request.client.host if request.client else None
    policy = policy_service.set_policy_status(
        db=db,
        policy_id=policy_id,
        status_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return FollowUpPolicyResponse.model_validate(policy)

