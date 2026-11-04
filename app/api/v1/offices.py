import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_permission
from app.core.database import get_db
from app.core.roles import Permission
from app.models.user import User
from app.schemas.office import (
    OfficeCreate,
    OfficeResponse,
    OfficeStatusUpdate,
    OfficeUpdate,
)
from app.services import office_service

offices_router = APIRouter(prefix="/offices", tags=["Offices"])


@offices_router.post(
    "",
    response_model=OfficeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new office/station",
)
def create_office(
    payload: OfficeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_DEPARTMENTS)),
) -> OfficeResponse:
    return office_service.create_office(db, payload, current_user)


@offices_router.get(
    "",
    response_model=List[OfficeResponse],
    summary="List offices (department-scoped)",
)
def list_offices(
    department_id: Optional[uuid.UUID] = Query(None, description="Filter by department (SUPER_ADMIN only)"),
    district: Optional[str] = Query(None, description="Filter by district"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[OfficeResponse]:
    return office_service.list_offices(
        db=db,
        current_user=current_user,
        department_id=department_id,
        district=district,
        is_active=is_active,
        skip=skip,
        limit=limit,
    )


@offices_router.get(
    "/{office_id}",
    response_model=OfficeResponse,
    summary="Get office details by ID",
)
def get_office(
    office_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OfficeResponse:
    return office_service.get_office_by_id(db, office_id, current_user)


@offices_router.patch(
    "/{office_id}",
    response_model=OfficeResponse,
    summary="Update office details",
)
def update_office(
    office_id: uuid.UUID,
    payload: OfficeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_DEPARTMENTS)),
) -> OfficeResponse:
    return office_service.update_office(db, office_id, payload, current_user)


@offices_router.patch(
    "/{office_id}/status",
    response_model=OfficeResponse,
    summary="Activate or deactivate an office",
)
def update_office_status(
    office_id: uuid.UUID,
    payload: OfficeStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_DEPARTMENTS)),
) -> OfficeResponse:
    return office_service.update_office_status(
        db, office_id, payload.is_active, current_user
    )

