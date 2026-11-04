import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_permission
from app.core.database import get_db
from app.core.roles import Permission
from app.models.user import User
from app.schemas.user import (
    UserCreate,
    UserResponse,
    UserRoleUpdate,
    UserStatusUpdate,
    UserUpdate,
)
from app.services import user_service

users_router = APIRouter(prefix="/users", tags=["Users"])


@users_router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a new authorized user",
)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_USERS)),
) -> UserResponse:
    return user_service.create_user(db, payload, current_user)


@users_router.get(
    "",
    response_model=List[UserResponse],
    summary="List users (department-scoped)",
)
def list_users(
    department_id: Optional[uuid.UUID] = Query(None, description="Filter by department (SUPER_ADMIN only)"),
    office_id: Optional[uuid.UUID] = Query(None, description="Filter by office"),
    role_id: Optional[uuid.UUID] = Query(None, description="Filter by role"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_USERS)),
) -> List[UserResponse]:
    return user_service.list_users(
        db=db,
        current_user=current_user,
        department_id=department_id,
        office_id=office_id,
        role_id=role_id,
        is_active=is_active,
        skip=skip,
        limit=limit,
    )


@users_router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get user details by ID",
)
def get_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_USERS)),
) -> UserResponse:
    return user_service.get_user_by_id(db, user_id, current_user)


@users_router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Update user profile",
)
def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_USERS)),
) -> UserResponse:
    return user_service.update_user(db, user_id, payload, current_user)


@users_router.patch(
    "/{user_id}/status",
    response_model=UserResponse,
    summary="Activate or deactivate a user account",
)
def update_user_status(
    user_id: uuid.UUID,
    payload: UserStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_USERS)),
) -> UserResponse:
    return user_service.update_user_status(
        db, user_id, payload.is_active, current_user
    )


@users_router.patch(
    "/{user_id}/role",
    response_model=UserResponse,
    summary="Update user role",
)
def update_user_role(
    user_id: uuid.UUID,
    payload: UserRoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.MANAGE_USERS)),
) -> UserResponse:
    return user_service.update_user_role(
        db, user_id, payload.role_id, current_user
    )

