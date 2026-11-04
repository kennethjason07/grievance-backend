"""
Grievance Service.
Handles grievance registration, concurrency-safe numbering, department confirmation,
department-isolated retrieval, filtering, search, and controlled updates.
"""
import math
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.roles import RoleName
from app.models.category import Category
from app.models.department import Department
from app.models.grievance import Grievance
from app.models.office import Office
from app.models.user import User
from app.schemas.grievance import (
    GrievanceConfirmDepartment,
    GrievanceCreate,
    GrievanceUpdate,
)
from app.services.audit import log_activity
from app.utils.grievance_number import generate_grievance_number


def _check_grievance_department_access(current_user: User, grievance: Grievance) -> None:
    """
    Ensure the current user is authorized to access the given grievance.
    SUPER_ADMIN has universal access.
    All other users can only access grievances belonging to their department
    (either confirmed_department_id or suggested_department_id if not yet confirmed).
    """
    if current_user.role.name == RoleName.SUPER_ADMIN:
        return

    user_dept_id = str(current_user.department_id) if current_user.department_id else None
    grievance_dept_id = (
        str(grievance.confirmed_department_id)
        if grievance.confirmed_department_id
        else (str(grievance.suggested_department_id) if grievance.suggested_department_id else None)
    )

    if not user_dept_id or user_dept_id != grievance_dept_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Grievance belongs to another department.",
        )


def create_grievance(
    db: Session,
    grievance_in: GrievanceCreate,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Grievance:
    """
    Register a new citizen grievance.
    Generates a concurrency-safe unique grievance number, enforces reference integrity,
    initializes status to NEW, and leaves confirmed_department_id unassigned.
    """
    # 1. Validate suggested_department_id if provided
    if grievance_in.suggested_department_id:
        dept = db.get(Department, grievance_in.suggested_department_id)
        if not dept:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Suggested department not found.",
            )
        if not dept.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Suggested department is inactive.",
            )

    # 2. Validate category_id if provided
    if grievance_in.category_id:
        cat = db.get(Category, grievance_in.category_id)
        if not cat:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Category not found.",
            )
        if not cat.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Category is inactive.",
            )
        if grievance_in.suggested_department_id and cat.department_id != grievance_in.suggested_department_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Category does not belong to the suggested department.",
            )

    # 3. Validate office_id if provided
    if grievance_in.office_id:
        office = db.get(Office, grievance_in.office_id)
        if not office:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Office not found.",
            )
        if not office.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Office is inactive.",
            )
        if grievance_in.suggested_department_id and office.department_id != grievance_in.suggested_department_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Office does not belong to the suggested department.",
            )

    # 4. Generate concurrency-safe sequential grievance number
    grievance_number = generate_grievance_number(db)

    # 5. Create grievance record
    grievance = Grievance(
        grievance_number=grievance_number,
        title=grievance_in.title.strip(),
        description=grievance_in.description.strip(),
        source=grievance_in.source,
        original_language=grievance_in.original_language,
        original_text=grievance_in.original_text,
        translated_language=grievance_in.translated_language,
        translated_text=grievance_in.translated_text,
        neutral_summary=grievance_in.neutral_summary.strip() if grievance_in.neutral_summary else None,
        suggested_department_id=grievance_in.suggested_department_id,
        confirmed_department_id=None,  # Explicit separation; requires confirmation
        category_id=grievance_in.category_id,
        office_id=grievance_in.office_id,
        assigned_to_user_id=None,
        status="NEW",
        priority=grievance_in.priority,
        estimated_resolution_date=grievance_in.estimated_resolution_date,
        created_by=current_user.id,
    )

    db.add(grievance)
    db.flush()

    # 6. Audit logging
    log_activity(
        db=db,
        action="GRIEVANCE_CREATED",
        entity_type="grievance",
        entity_id=grievance.id,
        user_id=current_user.id,
        grievance_id=grievance.id,
        new_values={
            "grievance_number": grievance.grievance_number,
            "title": grievance.title,
            "source": grievance.source,
            "priority": grievance.priority,
            "status": grievance.status,
            "original_language": grievance.original_language,
            "suggested_department_id": str(grievance.suggested_department_id) if grievance.suggested_department_id else None,
        },
        description=f"Grievance {grievance.grievance_number} registered by {current_user.email}.",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(grievance)
    return grievance


def confirm_grievance_department(
    db: Session,
    grievance_id: uuid.UUID,
    confirm_in: GrievanceConfirmDepartment,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Grievance:
    """
    Confirm the department for an incoming grievance.
    - SUPER_ADMIN can confirm any department.
    - DEPARTMENT_ADMIN can confirm grievances to their own department.
    - Transitions status from NEW to UNDER_REVIEW.
    - Records an audit log with confirmation notes.
    """
    grievance = db.get(Grievance, grievance_id)
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grievance not found.",
        )

    # Validate target department
    dept = db.get(Department, confirm_in.department_id)
    if not dept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found.",
        )
    if not dept.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Department is inactive.",
        )

    # Authorization check:
    # SUPER_ADMIN has full authority.
    # DEPARTMENT_ADMIN can only confirm grievances into their own department.
    if current_user.role.name != RoleName.SUPER_ADMIN:
        if str(current_user.department_id) != str(confirm_in.department_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: You can only confirm grievances for your own department.",
            )

    old_confirmed_dept = grievance.confirmed_department_id
    old_status = grievance.status

    # Update confirmed department
    grievance.confirmed_department_id = confirm_in.department_id

    # If category exists but doesn't belong to the confirmed department, clear it to maintain consistency
    if grievance.category_id:
        cat = db.get(Category, grievance.category_id)
        if cat and cat.department_id != confirm_in.department_id:
            grievance.category_id = None

    # Transition status: NEW -> UNDER_REVIEW
    if grievance.status == "NEW":
        grievance.status = "UNDER_REVIEW"

    db.flush()

    log_activity(
        db=db,
        action="GRIEVANCE_DEPARTMENT_CONFIRMED",
        entity_type="grievance",
        entity_id=grievance.id,
        user_id=current_user.id,
        grievance_id=grievance.id,
        old_values={
            "confirmed_department_id": str(old_confirmed_dept) if old_confirmed_dept else None,
            "status": old_status,
        },
        new_values={
            "confirmed_department_id": str(grievance.confirmed_department_id),
            "status": grievance.status,
            "confirmation_notes": confirm_in.confirmation_notes,
        },
        description=f"Department confirmed as {dept.name} ({dept.code}) for grievance {grievance.grievance_number}.",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(grievance)
    return grievance


def get_grievance_by_id(
    db: Session,
    grievance_id: uuid.UUID,
    current_user: User,
) -> Grievance:
    """
    Retrieve full grievance details by ID.
    Enforces object-level cross-department isolation.
    """
    grievance = db.get(Grievance, grievance_id)
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grievance not found.",
        )

    _check_grievance_department_access(current_user, grievance)
    return grievance


def list_grievances(
    db: Session,
    current_user: User,
    page: int = 1,
    page_size: int = 20,
    status_filter: Optional[str] = None,
    priority_filter: Optional[str] = None,
    department_id: Optional[uuid.UUID] = None,
    assigned_to_user_id: Optional[uuid.UUID] = None,
    office_id: Optional[uuid.UUID] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    search: Optional[str] = None,
) -> Tuple[List[Grievance], int, int]:
    """
    List grievances with department scoping, filtering, search, and pagination.
    Returns (items, total_count, total_pages).
    """
    query = select(Grievance)

    # 1. Department isolation scoping
    if current_user.role.name != RoleName.SUPER_ADMIN:
        user_dept = current_user.department_id
        if not user_dept:
            return [], 0, 1

        # Non-super admins cannot filter for another department
        if department_id and department_id != user_dept:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Cannot query grievances of another department.",
            )

        # Scoped to grievances where confirmed_department_id matches OR (confirmed is NULL and suggested matches)
        query = query.where(
            or_(
                Grievance.confirmed_department_id == user_dept,
                and_(
                    Grievance.confirmed_department_id.is_(None),
                    Grievance.suggested_department_id == user_dept,
                ),
            )
        )
    else:
        # SUPER_ADMIN filtering by department
        if department_id:
            query = query.where(
                or_(
                    Grievance.confirmed_department_id == department_id,
                    and_(
                        Grievance.confirmed_department_id.is_(None),
                        Grievance.suggested_department_id == department_id,
                    ),
                )
            )

    # 2. Status filter
    if status_filter:
        query = query.where(Grievance.status == status_filter.strip().upper())

    # 3. Priority filter
    if priority_filter:
        query = query.where(Grievance.priority == priority_filter.strip().upper())

    # 4. Assigned officer filter
    if assigned_to_user_id:
        query = query.where(Grievance.assigned_to_user_id == assigned_to_user_id)

    # 5. Office filter
    if office_id:
        query = query.where(Grievance.office_id == office_id)

    # 6. Date range filter
    if date_from:
        query = query.where(Grievance.date_received >= date_from)
    if date_to:
        query = query.where(Grievance.date_received <= date_to)

    # 7. Search filter (grievance number or title)
    if search:
        search_term = f"%{search.strip()}%"
        query = query.where(
            or_(
                Grievance.grievance_number.ilike(search_term),
                Grievance.title.ilike(search_term),
            )
        )

    # Total count
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar_one()

    total_pages = math.ceil(total / page_size) if total > 0 else 1

    # Order and paginate
    query = query.order_by(Grievance.date_received.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    items = list(db.execute(query).scalars().all())
    return items, total, total_pages


def update_grievance(
    db: Session,
    grievance_id: uuid.UUID,
    grievance_in: GrievanceUpdate,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Grievance:
    """
    Controlled grievance modification.
    Allows updating title, description, neutral_summary, priority, category_id,
    translated_text, estimated_resolution_date.
    Explicitly prohibits modifications to status, grievance_number, created_by,
    confirmed_department_id, and assigned_to_user_id.
    """
    grievance = db.get(Grievance, grievance_id)
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grievance not found.",
        )

    _check_grievance_department_access(current_user, grievance)

    # If category is updated, validate category
    if grievance_in.category_id is not None:
        cat = db.get(Category, grievance_in.category_id)
        if not cat:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Category not found.",
            )
        if not cat.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Category is inactive.",
            )
        dept_id = grievance.confirmed_department_id or grievance.suggested_department_id
        if dept_id and cat.department_id != dept_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Category does not belong to the grievance's department.",
            )

    old_values: Dict[str, Any] = {}
    new_values: Dict[str, Any] = {}

    update_data = grievance_in.model_dump(exclude_unset=True)
    for field_name, new_val in update_data.items():
        old_val = getattr(grievance, field_name)
        if old_val != new_val:
            old_values[field_name] = str(old_val) if isinstance(old_val, (uuid.UUID, datetime)) else old_val
            new_values[field_name] = str(new_val) if isinstance(new_val, (uuid.UUID, datetime)) else new_val
            setattr(grievance, field_name, new_val)

    if new_values:
        db.flush()
        log_activity(
            db=db,
            action="GRIEVANCE_UPDATED",
            entity_type="grievance",
            entity_id=grievance.id,
            user_id=current_user.id,
            grievance_id=grievance.id,
            old_values=old_values,
            new_values=new_values,
            description=f"Grievance {grievance.grievance_number} updated by {current_user.email}.",
            ip_address=ip_address,
        )
        db.commit()
        db.refresh(grievance)

    return grievance

