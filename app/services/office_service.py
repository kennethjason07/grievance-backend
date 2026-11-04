import logging
import uuid
from typing import List, Optional
from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.v1.dependencies import check_department_access
from app.core.roles import RoleName
from app.models.department import Department
from app.models.office import Office
from app.models.user import User
from app.schemas.office import OfficeCreate, OfficeUpdate
from app.services.audit import log_activity

logger = logging.getLogger(__name__)


def create_office(
    db: Session,
    payload: OfficeCreate,
    current_user: User,
) -> Office:
    """
    Create a new office/station under a department.
    SUPER_ADMIN or DEPARTMENT_ADMIN of that department.
    """
    check_department_access(current_user, payload.department_id)

    # Verify department exists
    dept = db.query(Department).filter(Department.id == payload.department_id).first()
    if not dept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Specified department does not exist.",
        )
    if not dept.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create an office under an inactive department.",
        )

    code_clean = payload.office_code.strip().upper()
    existing_code = db.query(Office).filter(
        func.upper(Office.office_code) == code_clean
    ).first()
    if existing_code:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Office code '{code_clean}' is already in use.",
        )

    office = Office(
        department_id=payload.department_id,
        name=payload.name.strip(),
        office_code=code_clean,
        address=payload.address.strip() if payload.address else None,
        district=payload.district.strip(),
        state=payload.state.strip(),
        is_active=True,
    )
    db.add(office)
    db.flush()

    log_activity(
        db=db,
        action="OFFICE_CREATED",
        entity_type="Office",
        entity_id=office.id,
        user_id=current_user.id,
        new_values={
            "department_id": str(office.department_id),
            "name": office.name,
            "office_code": office.office_code,
            "district": office.district,
        },
        description=f"Office '{office.name}' ({office.office_code}) created.",
    )
    db.commit()
    db.refresh(office)
    return office


def list_offices(
    db: Session,
    current_user: User,
    department_id: Optional[uuid.UUID] = None,
    district: Optional[str] = None,
    is_active: Optional[bool] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[Office]:
    """
    List offices with optional filtering.
    Enforces department scoping: departmental users cannot query another department's offices.
    """
    query = db.query(Office)

    if current_user.role.name != RoleName.SUPER_ADMIN:
        # Non-superadmins are strictly locked to their own department
        if not current_user.department_id:
            return []
        query = query.filter(Office.department_id == current_user.department_id)
    else:
        # Superadmin can filter by any department
        if department_id is not None:
            query = query.filter(Office.department_id == department_id)

    if district:
        query = query.filter(func.lower(Office.district) == district.strip().lower())

    if is_active is not None:
        query = query.filter(Office.is_active == is_active)

    return query.order_by(Office.name.asc()).offset(skip).limit(limit).all()


def get_office_by_id(
    db: Session,
    office_id: uuid.UUID,
    current_user: User,
) -> Office:
    """
    Retrieve office by ID with department-access validation.
    """
    office = db.query(Office).filter(Office.id == office_id).first()
    if not office:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Office not found.",
        )

    check_department_access(current_user, office.department_id)
    return office


def update_office(
    db: Session,
    office_id: uuid.UUID,
    payload: OfficeUpdate,
    current_user: User,
) -> Office:
    """
    Update office details.
    """
    office = get_office_by_id(db, office_id, current_user)

    old_values = {
        "name": office.name,
        "address": office.address,
        "district": office.district,
        "state": office.state,
    }
    new_values = {}

    if payload.name is not None:
        office.name = payload.name.strip()
        new_values["name"] = office.name

    if payload.address is not None:
        office.address = payload.address.strip() if payload.address else None
        new_values["address"] = office.address

    if payload.district is not None:
        office.district = payload.district.strip()
        new_values["district"] = office.district

    if payload.state is not None:
        office.state = payload.state.strip()
        new_values["state"] = office.state

    if new_values:
        log_activity(
            db=db,
            action="OFFICE_UPDATED",
            entity_type="Office",
            entity_id=office.id,
            user_id=current_user.id,
            old_values=old_values,
            new_values=new_values,
            description=f"Office '{office.office_code}' updated.",
        )
        db.commit()
        db.refresh(office)

    return office


def update_office_status(
    db: Session,
    office_id: uuid.UUID,
    is_active: bool,
    current_user: User,
) -> Office:
    """
    Activate or deactivate an office.
    """
    office = get_office_by_id(db, office_id, current_user)

    if office.is_active == is_active:
        return office

    old_status = office.is_active
    office.is_active = is_active

    log_activity(
        db=db,
        action="OFFICE_STATUS_CHANGED",
        entity_type="Office",
        entity_id=office.id,
        user_id=current_user.id,
        old_values={"is_active": old_status},
        new_values={"is_active": is_active},
        description=f"Office '{office.office_code}' status changed to {'active' if is_active else 'inactive'}.",
    )
    db.commit()
    db.refresh(office)
    return office

