"""
API router for Grievance Lifecycle Transitions and Timeline.
"""
import logging
import uuid
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_permission
from app.core.database import get_db
from app.core.roles import Permission
from app.models.user import User
from app.schemas.grievance import GrievanceDetailResponse
from app.schemas.lifecycle import (
    ClosureCreate,
    InformationRequestCreate,
    ReopenCreate,
    ResolutionCreate,
    ResumeInvestigationCreate,
    TimelineResponse,
)
from app.services import lifecycle_service

logger = logging.getLogger(__name__)

lifecycle_router = APIRouter(prefix="/grievances", tags=["Grievance Lifecycle & Resolution"])


@lifecycle_router.post(
    "/{grievance_id}/start",
    response_model=GrievanceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Start Grievance Investigation",
    description="Transitions an assigned grievance from ASSIGNED to IN_PROGRESS. Only callable by the assigned officer or supervisory roles.",
)
def start_investigation(
    grievance_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GrievanceDetailResponse:
    ip = request.client.host if request.client else None
    grievance = lifecycle_service.start_investigation(
        db=db,
        grievance_id=grievance_id,
        current_user=current_user,
        ip_address=ip,
    )
    return GrievanceDetailResponse.model_validate(grievance)


@lifecycle_router.post(
    "/{grievance_id}/request-information",
    response_model=GrievanceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Request Additional Information",
    description="Transitions grievance from IN_PROGRESS to AWAITING_INFORMATION and records follow-up requirements.",
)
def request_information(
    grievance_id: uuid.UUID,
    payload: InformationRequestCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GrievanceDetailResponse:
    ip = request.client.host if request.client else None
    grievance = lifecycle_service.request_information(
        db=db,
        grievance_id=grievance_id,
        info_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return GrievanceDetailResponse.model_validate(grievance)


@lifecycle_router.post(
    "/{grievance_id}/resume",
    response_model=GrievanceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Resume Investigation",
    description="Transitions grievance from AWAITING_INFORMATION back to IN_PROGRESS.",
)
def resume_investigation(
    grievance_id: uuid.UUID,
    payload: ResumeInvestigationCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GrievanceDetailResponse:
    ip = request.client.host if request.client else None
    grievance = lifecycle_service.resume_investigation(
        db=db,
        grievance_id=grievance_id,
        resume_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return GrievanceDetailResponse.model_validate(grievance)


@lifecycle_router.post(
    "/{grievance_id}/resolve",
    response_model=GrievanceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Resolve Grievance",
    description="Transitions grievance from IN_PROGRESS to RESOLVED with findings and timestamp.",
)
def resolve_grievance(
    grievance_id: uuid.UUID,
    payload: ResolutionCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GrievanceDetailResponse:
    ip = request.client.host if request.client else None
    grievance = lifecycle_service.resolve_grievance(
        db=db,
        grievance_id=grievance_id,
        resolution_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return GrievanceDetailResponse.model_validate(grievance)


@lifecycle_router.post(
    "/{grievance_id}/close",
    response_model=GrievanceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Close Resolved Grievance",
    description="Transitions grievance from RESOLVED to CLOSED (terminal state). Requires administrative authorization.",
)
def close_grievance(
    grievance_id: uuid.UUID,
    payload: ClosureCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.CLOSE_GRIEVANCE)),
) -> GrievanceDetailResponse:
    ip = request.client.host if request.client else None
    grievance = lifecycle_service.close_grievance(
        db=db,
        grievance_id=grievance_id,
        closure_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return GrievanceDetailResponse.model_validate(grievance)


@lifecycle_router.get(
    "/{grievance_id}/timeline",
    response_model=TimelineResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Grievance Lifecycle Timeline",
    description="Constructs a unified, chronological history of all lifecycle transitions and administrative actions.",
)
def get_timeline(
    grievance_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_GRIEVANCE)),
) -> TimelineResponse:
    return lifecycle_service.get_grievance_timeline(
        db=db,
        grievance_id=grievance_id,
        current_user=current_user,
    )


@lifecycle_router.post(
    "/{grievance_id}/reopen",
    response_model=GrievanceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Reopen Resolved Grievance",
    description=(
        "Reopens a RESOLVED grievance back to IN_PROGRESS. "
        "Requires REOPEN_GRIEVANCE permission (SUPER_ADMIN or DEPARTMENT_ADMIN). "
        "A mandatory reopen_reason must be provided and is recorded in the activity log. "
        "CLOSED grievances are terminal and cannot be reopened."
    ),
)
def reopen_grievance(
    grievance_id: uuid.UUID,
    payload: ReopenCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.REOPEN_GRIEVANCE)),
) -> GrievanceDetailResponse:
    ip = request.client.host if request.client else None
    grievance = lifecycle_service.reopen_grievance(
        db=db,
        grievance_id=grievance_id,
        reopen_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return GrievanceDetailResponse.model_validate(grievance)

