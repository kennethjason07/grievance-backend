"""
API router for Grievance Registration and Retrieval (Stage 9C).
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
from app.schemas.grievance import (
    GrievanceConfirmDepartment,
    GrievanceCreate,
    GrievanceDetailResponse,
    GrievanceListResponse,
    GrievanceUpdate,
    PaginatedGrievanceResponse,
)
from app.services import grievance_service

logger = logging.getLogger(__name__)

grievances_router = APIRouter(prefix="/grievances", tags=["Grievances"])


@grievances_router.post(
    "",
    response_model=GrievanceDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register Citizen Grievance",
    description="Registers a new grievance with a sequential unique grievance number and initial NEW status.",
)
def create_grievance(
    payload: GrievanceCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.CREATE_GRIEVANCE)),
) -> GrievanceDetailResponse:
    ip = request.client.host if request.client else None
    grievance = grievance_service.create_grievance(
        db=db,
        grievance_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return GrievanceDetailResponse.model_validate(grievance)


@grievances_router.post(
    "/{grievance_id}/confirm-department",
    response_model=GrievanceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirm Department for Grievance",
    description="Assigns a confirmed department to the grievance and transitions status from NEW to UNDER_REVIEW.",
)
def confirm_department(
    grievance_id: uuid.UUID,
    payload: GrievanceConfirmDepartment,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GrievanceDetailResponse:
    ip = request.client.host if request.client else None
    grievance = grievance_service.confirm_grievance_department(
        db=db,
        grievance_id=grievance_id,
        confirm_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return GrievanceDetailResponse.model_validate(grievance)


@grievances_router.get(
    "",
    response_model=PaginatedGrievanceResponse,
    status_code=status.HTTP_200_OK,
    summary="List Grievances",
    description="Returns a paginated list of department-scoped grievances with filtering and search support.",
)
def list_grievances(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by grievance status"),
    priority: Optional[str] = Query(None, description="Filter by grievance priority"),
    department_id: Optional[uuid.UUID] = Query(None, description="Filter by department ID"),
    assigned_to_user_id: Optional[uuid.UUID] = Query(None, description="Filter by assigned officer"),
    office_id: Optional[uuid.UUID] = Query(None, description="Filter by office ID"),
    date_from: Optional[datetime] = Query(None, description="Filter by date received on/after"),
    date_to: Optional[datetime] = Query(None, description="Filter by date received on/before"),
    search: Optional[str] = Query(None, description="Search by grievance number or title"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_GRIEVANCE)),
) -> PaginatedGrievanceResponse:
    items, total, total_pages = grievance_service.list_grievances(
        db=db,
        current_user=current_user,
        page=page,
        page_size=page_size,
        status_filter=status,
        priority_filter=priority,
        department_id=department_id,
        assigned_to_user_id=assigned_to_user_id,
        office_id=office_id,
        date_from=date_from,
        date_to=date_to,
        search=search,
    )
    response_items = [GrievanceListResponse.model_validate(item) for item in items]
    return PaginatedGrievanceResponse(
        items=response_items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@grievances_router.get(
    "/{grievance_id}",
    response_model=GrievanceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Grievance by ID",
    description="Returns detailed grievance information, subject to department isolation checks.",
)
def get_grievance(
    grievance_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_GRIEVANCE)),
) -> GrievanceDetailResponse:
    grievance = grievance_service.get_grievance_by_id(
        db=db,
        grievance_id=grievance_id,
        current_user=current_user,
    )
    return GrievanceDetailResponse.model_validate(grievance)


@grievances_router.patch(
    "/{grievance_id}",
    response_model=GrievanceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Grievance Details",
    description="Controlled updates for grievance content, priority, and metadata with audit logging.",
)
def update_grievance(
    grievance_id: uuid.UUID,
    payload: GrievanceUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.EDIT_GRIEVANCE)),
) -> GrievanceDetailResponse:
    ip = request.client.host if request.client else None
    grievance = grievance_service.update_grievance(
        db=db,
        grievance_id=grievance_id,
        grievance_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return GrievanceDetailResponse.model_validate(grievance)

