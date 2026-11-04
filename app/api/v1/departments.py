import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_permission
from app.core.database import get_db
from app.core.roles import Permission
from app.models.user import User
from app.schemas.department import (
    DepartmentCreate,
    DepartmentResponse,
    DepartmentStatusUpdate,
    DepartmentUpdate,
)
from app.services import department_service

departments_router = APIRouter(prefix="/departments", tags=["Departments"])


@departments_router.post(
    "",
    response_model=DepartmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new government department",
)
def create_department(
    payload: DepartmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_DEPARTMENTS)),
) -> DepartmentResponse:
    return department_service.create_department(db, payload, current_user)


@departments_router.get(
    "",
    response_model=List[DepartmentResponse],
    summary="List government departments (department-scoped)",
)
def list_departments(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[DepartmentResponse]:
    return department_service.list_departments(
        db=db,
        current_user=current_user,
        is_active=is_active,
        skip=skip,
        limit=limit,
    )


@departments_router.get(
    "/{department_id}",
    response_model=DepartmentResponse,
    summary="Get department details by ID",
)
def get_department(
    department_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DepartmentResponse:
    return department_service.get_department_by_id(db, department_id, current_user)


@departments_router.patch(
    "/{department_id}",
    response_model=DepartmentResponse,
    summary="Update department metadata",
)
def update_department(
    department_id: uuid.UUID,
    payload: DepartmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_DEPARTMENTS)),
) -> DepartmentResponse:
    return department_service.update_department(db, department_id, payload, current_user)


@departments_router.patch(
    "/{department_id}/status",
    response_model=DepartmentResponse,
    summary="Activate or deactivate a department",
)
def update_department_status(
    department_id: uuid.UUID,
    payload: DepartmentStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_DEPARTMENTS)),
) -> DepartmentResponse:
    return department_service.update_department_status(
        db, department_id, payload.is_active, current_user
    )

