"""
API router for Grievance Assignment and Officer Workflows (Stage 9D).
"""
import logging
import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_permission
from app.core.database import get_db
from app.core.roles import Permission
from app.models.user import User
from app.schemas.assignment import (
    AssignmentCreate,
    AssignmentHistoryResponse,
    AssignmentReassign,
    AssignmentResponse,
    OfficerWorkloadResponse,
)
from app.schemas.grievance import GrievanceListResponse, PaginatedGrievanceResponse
from app.services import assignment_service

logger = logging.getLogger(__name__)

assignments_router = APIRouter(tags=["Grievance Assignments & Officer Workflow"])


@assignments_router.post(
    "/grievances/{grievance_id}/assign",
    response_model=AssignmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign Grievance to Officer",
    description="Assigns a confirmed grievance to an eligible department officer and transitions status to ASSIGNED.",
)
def assign_grievance(
    grievance_id: uuid.UUID,
    payload: AssignmentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.ASSIGN_GRIEVANCE)),
) -> AssignmentResponse:
    ip = request.client.host if request.client else None
    assignment = assignment_service.assign_grievance(
        db=db,
        grievance_id=grievance_id,
        assignment_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return AssignmentResponse.model_validate(assignment)


@assignments_router.post(
    "/grievances/{grievance_id}/reassign",
    response_model=AssignmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Reassign Grievance to Another Officer",
    description="Reassigns an active grievance to a new eligible officer, deactivating the prior assignment and preserving history.",
)
def reassign_grievance(
    grievance_id: uuid.UUID,
    payload: AssignmentReassign,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.ASSIGN_GRIEVANCE)),
) -> AssignmentResponse:
    ip = request.client.host if request.client else None
    assignment = assignment_service.reassign_grievance(
        db=db,
        grievance_id=grievance_id,
        reassign_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return AssignmentResponse.model_validate(assignment)


@assignments_router.get(
    "/grievances/{grievance_id}/assignments",
    response_model=AssignmentHistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Grievance Assignment History",
    description="Returns full chronological assignment audit history for a given grievance.",
)
def get_assignment_history(
    grievance_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_GRIEVANCE)),
) -> AssignmentHistoryResponse:
    return assignment_service.get_assignment_history(
        db=db,
        grievance_id=grievance_id,
        current_user=current_user,
    )


@assignments_router.get(
    "/officers/me/assignments",
    response_model=PaginatedGrievanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Current Officer Assigned Grievances",
    description="Returns paginated grievances actively assigned to the authenticated officer.",
)
def get_my_assignments(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by grievance status"),
    priority: Optional[str] = Query(None, description="Filter by grievance priority"),
    date_from: Optional[datetime] = Query(None, description="Filter by date received on/after"),
    date_to: Optional[datetime] = Query(None, description="Filter by date received on/before"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PaginatedGrievanceResponse:
    items, total, total_pages = assignment_service.get_officer_assignments(
        db=db,
        officer_id=current_user.id,
        current_user=current_user,
        page=page,
        page_size=page_size,
        status_filter=status,
        priority_filter=priority,
        date_from=date_from,
        date_to=date_to,
    )
    response_items = [GrievanceListResponse.model_validate(item) for item in items]
    return PaginatedGrievanceResponse(
        items=response_items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@assignments_router.get(
    "/officers/me/workload",
    response_model=OfficerWorkloadResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Current Officer Workload",
    description="Calculates current active and resolved grievance counts for the authenticated officer.",
)
def get_my_workload(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OfficerWorkloadResponse:
    return assignment_service.get_officer_workload(
        db=db,
        officer_id=current_user.id,
        current_user=current_user,
    )


@assignments_router.get(
    "/officers/{officer_id}/workload",
    response_model=OfficerWorkloadResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Specific Officer Workload",
    description="Calculates officer workload subject to department-level permissions and supervisory access.",
)
def get_officer_workload(
    officer_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OfficerWorkloadResponse:
    return assignment_service.get_officer_workload(
        db=db,
        officer_id=officer_id,
        current_user=current_user,
    )

