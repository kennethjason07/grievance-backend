import logging
import uuid
from typing import List, Optional
from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.roles import RoleName
from app.core.security import hash_password
from app.models.department import Department
from app.models.office import Office
from app.models.role import Role
from app.models.user import User
from app.schemas.user import (
    UserCreate,
    UserResponse,
    UserUpdate,
)
from app.services.audit import log_activity

logger = logging.getLogger(__name__)


def _to_user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        department_id=user.department_id,
        office_id=user.office_id,
        role_id=user.role_id,
        role_name=user.role.name if user.role else "",
        is_active=user.is_active,
        is_verified=user.is_verified,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def create_user(
    db: Session,
    payload: UserCreate,
    current_user: User,
) -> UserResponse:
    """
    Provision a new authorized user.
    Enforces role hierarchy, department scoping, office consistency, and unique email.
    """
    # 1. Target role verification
    role = db.query(Role).filter(Role.id == payload.role_id).first()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Specified role not found.",
        )

    # Prevent privilege escalation: DEPARTMENT_ADMIN cannot create SUPER_ADMIN
    if role.name == RoleName.SUPER_ADMIN and current_user.role.name != RoleName.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a SUPER_ADMIN can assign the SUPER_ADMIN role.",
        )

    # 2. Department boundary enforcement
    if current_user.role.name != RoleName.SUPER_ADMIN:
        # Department admins must provision users strictly inside their own department
        target_dept_id = current_user.department_id
        if payload.department_id and payload.department_id != current_user.department_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot create a user outside your authorized department.",
            )
    else:
        # SUPER_ADMIN: if role is SUPER_ADMIN, department can be None
        if role.name == RoleName.SUPER_ADMIN:
            target_dept_id = payload.department_id
        else:
            if not payload.department_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="department_id is required for departmental roles.",
                )
            target_dept_id = payload.department_id

    # Verify department if provided
    if target_dept_id:
        dept = db.query(Department).filter(Department.id == target_dept_id).first()
        if not dept or not dept.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Specified department does not exist or is inactive.",
            )

    # 3. Office validation (must belong to target department)
    if payload.office_id:
        office = db.query(Office).filter(Office.id == payload.office_id).first()
        if not office or not office.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Specified office does not exist or is inactive.",
            )
        if target_dept_id and office.department_id != target_dept_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Selected office does not belong to the user's department.",
            )

    # 4. Email uniqueness check (case-insensitive)
    clean_email = payload.email.lower().strip()
    existing_user = db.query(User).filter(func.lower(User.email) == clean_email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email '{clean_email}' is already in use.",
        )

    # 5. Create user with hashed password
    hashed = hash_password(payload.password)
    user = User(
        full_name=payload.full_name.strip(),
        email=clean_email,
        password_hash=hashed,
        phone=payload.phone.strip() if payload.phone else None,
        department_id=target_dept_id,
        office_id=payload.office_id,
        role_id=role.id,
        is_active=True,
        is_verified=payload.is_verified,
    )
    db.add(user)
    db.flush()

    # 6. Audit logging
    log_activity(
        db=db,
        action="USER_CREATED",
        entity_type="User",
        entity_id=user.id,
        user_id=current_user.id,
        new_values={
            "email": user.email,
            "role": role.name,
            "department_id": str(user.department_id) if user.department_id else None,
            "office_id": str(user.office_id) if user.office_id else None,
        },
        description=f"User '{user.email}' provisioned with role '{role.name}'.",
    )
    db.commit()
    db.refresh(user)
    return _to_user_response(user)


def list_users(
    db: Session,
    current_user: User,
    department_id: Optional[uuid.UUID] = None,
    office_id: Optional[uuid.UUID] = None,
    role_id: Optional[uuid.UUID] = None,
    is_active: Optional[bool] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[UserResponse]:
    """
    List users with department-level isolation.
    """
    query = db.query(User)

    if current_user.role.name != RoleName.SUPER_ADMIN:
        if not current_user.department_id:
            return []
        query = query.filter(User.department_id == current_user.department_id)
    else:
        if department_id is not None:
            query = query.filter(User.department_id == department_id)

    if office_id is not None:
        query = query.filter(User.office_id == office_id)

    if role_id is not None:
        query = query.filter(User.role_id == role_id)

    if is_active is not None:
        query = query.filter(User.is_active == is_active)

    users = query.order_by(User.full_name.asc()).offset(skip).limit(limit).all()
    return [_to_user_response(u) for u in users]


def get_user_by_id(
    db: Session,
    user_id: uuid.UUID,
    current_user: User,
) -> UserResponse:
    """
    Get user details with object-level department access enforcement.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # Department scoping check
    if current_user.role.name != RoleName.SUPER_ADMIN:
        if user.department_id != current_user.department_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Cannot view users outside your authorized department.",
            )

    return _to_user_response(user)


def update_user(
    db: Session,
    user_id: uuid.UUID,
    payload: UserUpdate,
    current_user: User,
) -> UserResponse:
    """
    Update user profile details (full_name, phone, office_id).
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # Department scoping check
    if current_user.role.name != RoleName.SUPER_ADMIN:
        if user.department_id != current_user.department_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Cannot modify users outside your authorized department.",
            )

    old_values = {
        "full_name": user.full_name,
        "phone": user.phone,
        "office_id": str(user.office_id) if user.office_id else None,
    }
    new_values = {}

    if payload.full_name is not None:
        user.full_name = payload.full_name.strip()
        new_values["full_name"] = user.full_name

    if payload.phone is not None:
        user.phone = payload.phone.strip() if payload.phone else None
        new_values["phone"] = user.phone

    if payload.office_id is not None:
        office = db.query(Office).filter(Office.id == payload.office_id).first()
        if not office or not office.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Specified office does not exist or is inactive.",
            )
        if user.department_id and office.department_id != user.department_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Selected office does not belong to the user's department.",
            )
        user.office_id = payload.office_id
        new_values["office_id"] = str(user.office_id)

    if new_values:
        log_activity(
            db=db,
            action="USER_UPDATED",
            entity_type="User",
            entity_id=user.id,
            user_id=current_user.id,
            old_values=old_values,
            new_values=new_values,
            description=f"User profile '{user.email}' updated.",
        )
        db.commit()
        db.refresh(user)

    return _to_user_response(user)


def update_user_status(
    db: Session,
    user_id: uuid.UUID,
    is_active: bool,
    current_user: User,
) -> UserResponse:
    """
    Activate or deactivate a user account.
    Self-deactivation is strictly prohibited.
    """
    # Self-deactivation prevention
    if user_id == current_user.id and not is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrators cannot deactivate their own account.",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # Department scoping check
    if current_user.role.name != RoleName.SUPER_ADMIN:
        if user.department_id != current_user.department_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Cannot modify users outside your authorized department.",
            )

    if user.is_active == is_active:
        return _to_user_response(user)

    old_status = user.is_active
    user.is_active = is_active

    log_activity(
        db=db,
        action="USER_STATUS_CHANGED",
        entity_type="User",
        entity_id=user.id,
        user_id=current_user.id,
        old_values={"is_active": old_status},
        new_values={"is_active": is_active},
        description=f"User '{user.email}' status changed to {'active' if is_active else 'inactive'}.",
    )
    db.commit()
    db.refresh(user)
    return _to_user_response(user)


def update_user_role(
    db: Session,
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    current_user: User,
) -> UserResponse:
    """
    Modify a user's role.
    Self-role modification is strictly prohibited.
    DEPARTMENT_ADMIN cannot assign the SUPER_ADMIN role.
    """
    # Self-role change prevention
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrators cannot modify their own role.",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # Department scoping check
    if current_user.role.name != RoleName.SUPER_ADMIN:
        if user.department_id != current_user.department_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Cannot modify users outside your authorized department.",
            )

    # Target role check
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Specified role not found.",
        )

    # Privilege escalation prevention
    if role.name == RoleName.SUPER_ADMIN and current_user.role.name != RoleName.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a SUPER_ADMIN can assign the SUPER_ADMIN role.",
        )

    old_role_name = user.role.name if user.role else ""
    user.role_id = role.id

    log_activity(
        db=db,
        action="USER_ROLE_CHANGED",
        entity_type="User",
        entity_id=user.id,
        user_id=current_user.id,
        old_values={"role": old_role_name},
        new_values={"role": role.name},
        description=f"User '{user.email}' role changed from '{old_role_name}' to '{role.name}'.",
    )
    db.commit()
    db.refresh(user)
    return _to_user_response(user)

