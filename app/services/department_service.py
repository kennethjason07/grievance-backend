import logging
import uuid
from typing import List, Optional
from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.v1.dependencies import check_department_access
from app.core.roles import RoleName
from app.models.department import Department
from app.models.user import User
from app.schemas.department import DepartmentCreate, DepartmentUpdate
from app.services.audit import log_activity

logger = logging.getLogger(__name__)


def create_department(
    db: Session,
    payload: DepartmentCreate,
    current_user: User,
) -> Department:
    """
    Create a new government department. Restricted to SUPER_ADMIN.
    """
    if current_user.role.name != RoleName.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a SUPER_ADMIN can create new government departments.",
        )

    code_normalized = payload.code.strip().upper()
    name_normalized = payload.name.strip()

    # Check for duplicate code
    existing_code = db.query(Department).filter(
        func.upper(Department.code) == code_normalized
    ).first()
    if existing_code:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Department code '{code_normalized}' is already in use.",
        )

    # Check for duplicate name
    existing_name = db.query(Department).filter(
        func.lower(Department.name) == name_normalized.lower()
    ).first()
    if existing_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Department name '{name_normalized}' is already in use.",
        )

    dept = Department(
        name=name_normalized,
        code=code_normalized,
        description=payload.description,
        is_active=True,
    )
    db.add(dept)
    db.flush()

    log_activity(
        db=db,
        action="DEPARTMENT_CREATED",
        entity_type="Department",
        entity_id=dept.id,
        user_id=current_user.id,
        new_values={"name": dept.name, "code": dept.code, "is_active": dept.is_active},
        description=f"Department '{dept.name}' ({dept.code}) created.",
    )
    db.commit()
    db.refresh(dept)
    return dept


def list_departments(
    db: Session,
    current_user: User,
    is_active: Optional[bool] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[Department]:
    """
    List departments.
    SUPER_ADMIN can list all departments.
    Departmental staff only receive their assigned department.
    """
    query = db.query(Department)

    if current_user.role.name != RoleName.SUPER_ADMIN:
        if not current_user.department_id:
            return []
        query = query.filter(Department.id == current_user.department_id)

    if is_active is not None:
        query = query.filter(Department.is_active == is_active)

    return query.order_by(Department.name.asc()).offset(skip).limit(limit).all()


def get_department_by_id(
    db: Session,
    department_id: uuid.UUID,
    current_user: User,
) -> Department:
    """
    Retrieve department by ID with department-scoping check.
    """
    check_department_access(current_user, department_id)

    dept = db.query(Department).filter(Department.id == department_id).first()
    if not dept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found.",
        )
    return dept


def update_department(
    db: Session,
    department_id: uuid.UUID,
    payload: DepartmentUpdate,
    current_user: User,
) -> Department:
    """
    Update department metadata.
    SUPER_ADMIN can update any department; DEPARTMENT_ADMIN can update their own department.
    """
    dept = get_department_by_id(db, department_id, current_user)

    old_values = {"name": dept.name, "description": dept.description}
    new_values = {}

    if payload.name is not None:
        name_clean = payload.name.strip()
        if name_clean.lower() != dept.name.lower():
            conflict = db.query(Department).filter(
                func.lower(Department.name) == name_clean.lower(),
                Department.id != dept.id,
            ).first()
            if conflict:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Department name '{name_clean}' is already in use.",
                )
        dept.name = name_clean
        new_values["name"] = name_clean

    if payload.description is not None:
        dept.description = payload.description.strip() if payload.description else None
        new_values["description"] = dept.description

    if new_values:
        log_activity(
            db=db,
            action="DEPARTMENT_UPDATED",
            entity_type="Department",
            entity_id=dept.id,
            user_id=current_user.id,
            old_values=old_values,
            new_values=new_values,
            description=f"Department '{dept.code}' updated.",
        )
        db.commit()
        db.refresh(dept)

    return dept


def update_department_status(
    db: Session,
    department_id: uuid.UUID,
    is_active: bool,
    current_user: User,
) -> Department:
    """
    Activate or deactivate a department. Restricted to SUPER_ADMIN.
    """
    if current_user.role.name != RoleName.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a SUPER_ADMIN can activate or deactivate departments.",
        )

    dept = db.query(Department).filter(Department.id == department_id).first()
    if not dept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found.",
        )

    if dept.is_active == is_active:
        return dept

    old_status = dept.is_active
    dept.is_active = is_active

    log_activity(
        db=db,
        action="DEPARTMENT_STATUS_CHANGED",
        entity_type="Department",
        entity_id=dept.id,
        user_id=current_user.id,
        old_values={"is_active": old_status},
        new_values={"is_active": is_active},
        description=f"Department '{dept.code}' status changed to {'active' if is_active else 'inactive'}.",
    )
    db.commit()
    db.refresh(dept)
    return dept

