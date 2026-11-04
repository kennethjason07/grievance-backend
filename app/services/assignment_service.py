"""
Grievance Assignment and Officer Workload Service (Stage 9D).
Handles:
- Concurrency-safe initial officer assignment
- Atomic reassignment preserving complete historical audit trail
- Workload aggregations
- Officer-specific grievance queues
- Object-level and department-scoped authorization guards

Stage 10B addition:
- auto_schedule_on_assignment hook integrated into assign_grievance()
"""
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.roles import RoleName
from app.models.grievance import Grievance
from app.models.grievance_assignment import GrievanceAssignment
from app.models.user import User
from app.schemas.assignment import (
    AssignmentCreate,
    AssignmentHistoryResponse,
    AssignmentReassign,
    AssignmentResponse,
    OfficerWorkloadResponse,
)
from app.services.audit import log_activity

ELIGIBLE_OFFICER_ROLES = {RoleName.OFFICER, RoleName.SUPERVISOR}


def _check_assignment_department_access(current_user: User, grievance: Grievance) -> None:
    """
    Ensure user is authorized to perform assignment operations on the grievance.
    SUPER_ADMIN has universal access.
    DEPARTMENT_ADMIN and SUPERVISOR are restricted to their confirmed department.
    """
    if current_user.role.name == RoleName.SUPER_ADMIN:
        return

    if not grievance.confirmed_department_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Grievance department must be confirmed prior to officer assignment.",
        )

    if current_user.department_id != grievance.confirmed_department_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Cannot assign grievances belonging to another department.",
        )


def _validate_assignee_officer(db: Session, officer_id: uuid.UUID, confirmed_dept_id: uuid.UUID) -> User:
    """
    Validates that the target assignee exists, is active, belongs to the confirmed department,
    and has an eligible officer role.
    """
    officer = db.get(User, officer_id)
    if not officer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Officer not found.",
        )

    if not officer.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot assign grievance to an inactive officer.",
        )

    if officer.role.name not in ELIGIBLE_OFFICER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User role '{officer.role.name}' is not eligible for grievance assignment.",
        )

    if officer.department_id != confirmed_dept_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Officer does not belong to the grievance's confirmed department.",
        )

    return officer


def assign_grievance(
    db: Session,
    grievance_id: uuid.UUID,
    assignment_in: AssignmentCreate,
    current_user: User,
    ip_address: Optional[str] = None,
) -> GrievanceAssignment:
    """
    Perform initial officer assignment on a confirmed grievance.
    Acquires row-level lock on the grievance record to prevent race conditions.
    Enforces that grievance is not already actively assigned.
    Transitions status to ASSIGNED and logs activity.
    """
    # 1. Acquire row lock on Grievance
    grievance = (
        db.query(Grievance)
        .with_for_update()
        .filter(Grievance.id == grievance_id)
        .first()
    )
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grievance not found.",
        )

    if not grievance.confirmed_department_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Grievance department must be confirmed prior to officer assignment.",
        )

    # 2. Check department access for performing user
    _check_assignment_department_access(current_user, grievance)

    # 3. Check for existing active assignment
    active_assignment = (
        db.query(GrievanceAssignment)
        .filter_by(grievance_id=grievance.id, is_active=True)
        .first()
    )
    if active_assignment:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Grievance is already actively assigned. Use the reassign endpoint to transfer ownership.",
        )

    # 4. Validate target officer
    officer = _validate_assignee_officer(db, assignment_in.officer_id, grievance.confirmed_department_id)

    # 5. Create new assignment record
    now_utc = datetime.now(timezone.utc)
    new_assignment = GrievanceAssignment(
        grievance_id=grievance.id,
        assigned_to_user_id=officer.id,
        assigned_by_user_id=current_user.id,
        remarks=assignment_in.remarks.strip() if assignment_in.remarks else None,
        is_active=True,
        assigned_at=now_utc,
    )
    db.add(new_assignment)

    # 6. Update Grievance core record
    old_status = grievance.status
    grievance.assigned_to_user_id = officer.id
    grievance.status = "ASSIGNED"
    grievance.last_action_at = now_utc

    db.flush()

    # 7. Audit log
    log_activity(
        db=db,
        action="GRIEVANCE_ASSIGNED",
        entity_type="grievance_assignment",
        entity_id=new_assignment.id,
        user_id=current_user.id,
        grievance_id=grievance.id,
        old_values={"assigned_to_user_id": None, "status": old_status},
        new_values={
            "assigned_to_user_id": str(officer.id),
            "status": grievance.status,
            "remarks": new_assignment.remarks,
        },
        description=f"Grievance {grievance.grievance_number} assigned to {officer.full_name} ({officer.email}).",
        ip_address=ip_address,
    )

    # 8. Auto-schedule first follow-up task (Stage 10B hook).
    #    Called before commit so the task is part of the same transaction.
    #    Import is deferred to avoid a circular import between assignment_service
    #    and task_service (task_service already imports assignment models).
    try:
        from app.services.task_service import auto_schedule_on_assignment  # noqa: PLC0415
        auto_schedule_on_assignment(db, grievance, new_assignment)
    except Exception:
        # Swallow any unexpected error — task scheduling must never block assignment.
        pass

    db.commit()
    db.refresh(new_assignment)
    return new_assignment


def reassign_grievance(
    db: Session,
    grievance_id: uuid.UUID,
    reassign_in: AssignmentReassign,
    current_user: User,
    ip_address: Optional[str] = None,
) -> GrievanceAssignment:
    """
    Reassign an already assigned grievance to another eligible officer.
    Safely deactivates previous active assignment before creating new active assignment,
    preventing partial unique index collision.
    """
    # 1. Acquire row lock on Grievance
    grievance = (
        db.query(Grievance)
        .with_for_update()
        .filter(Grievance.id == grievance_id)
        .first()
    )
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grievance not found.",
        )

    if not grievance.confirmed_department_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Grievance department must be confirmed prior to officer assignment.",
        )

    # 2. Check department access
    _check_assignment_department_access(current_user, grievance)

    # 3. Validate new officer
    new_officer = _validate_assignee_officer(db, reassign_in.officer_id, grievance.confirmed_department_id)

    # 4. Check if already assigned to this officer
    if grievance.assigned_to_user_id == new_officer.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Grievance is already assigned to this officer.",
        )

    now_utc = datetime.now(timezone.utc)
    old_officer_id = grievance.assigned_to_user_id

    # 5. Lock and deactivate current active assignment
    active_assignment = (
        db.query(GrievanceAssignment)
        .with_for_update()
        .filter_by(grievance_id=grievance.id, is_active=True)
        .first()
    )
    if active_assignment:
        active_assignment.is_active = False
        active_assignment.unassigned_at = now_utc
        # Flush immediately to release partial unique index slot
        db.flush()

    # 6. Create replacement active assignment
    new_assignment = GrievanceAssignment(
        grievance_id=grievance.id,
        assigned_to_user_id=new_officer.id,
        assigned_by_user_id=current_user.id,
        remarks=reassign_in.reassignment_reason.strip(),
        is_active=True,
        assigned_at=now_utc,
    )
    db.add(new_assignment)

    # 7. Update grievance
    grievance.assigned_to_user_id = new_officer.id
    grievance.status = "ASSIGNED"
    grievance.last_action_at = now_utc

    db.flush()

    # 8. Audit log
    log_activity(
        db=db,
        action="GRIEVANCE_REASSIGNED",
        entity_type="grievance_assignment",
        entity_id=new_assignment.id,
        user_id=current_user.id,
        grievance_id=grievance.id,
        old_values={"assigned_to_user_id": str(old_officer_id) if old_officer_id else None},
        new_values={
            "assigned_to_user_id": str(new_officer.id),
            "reassignment_reason": reassign_in.reassignment_reason,
            "status": grievance.status,
        },
        description=(
            f"Grievance {grievance.grievance_number} reassigned to {new_officer.full_name} "
            f"({new_officer.email}). Reason: {reassign_in.reassignment_reason}"
        ),
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(new_assignment)
    return new_assignment


def get_assignment_history(
    db: Session,
    grievance_id: uuid.UUID,
    current_user: User,
) -> AssignmentHistoryResponse:
    """
    Retrieve full historical assignments for a grievance, sorted chronologically.
    Enforces object-level department access.
    """
    grievance = db.get(Grievance, grievance_id)
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grievance not found.",
        )

    # Department access check
    if current_user.role.name != RoleName.SUPER_ADMIN:
        dept = grievance.confirmed_department_id or grievance.suggested_department_id
        if current_user.department_id != dept:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Grievance belongs to another department.",
            )

    assignments = (
        db.query(GrievanceAssignment)
        .filter_by(grievance_id=grievance.id)
        .order_by(GrievanceAssignment.assigned_at.desc())
        .all()
    )

    history_items = [AssignmentResponse.model_validate(a) for a in assignments]
    return AssignmentHistoryResponse(
        grievance_id=grievance.id,
        total_assignments=len(history_items),
        history=history_items,
    )


def get_officer_assignments(
    db: Session,
    officer_id: uuid.UUID,
    current_user: User,
    page: int = 1,
    page_size: int = 20,
    status_filter: Optional[str] = None,
    priority_filter: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Tuple[List[Grievance], int, int]:
    """
    Retrieve grievances currently assigned to an officer.
    - An OFFICER can only view their own assigned grievances.
    - SUPERVISOR / DEPARTMENT_ADMIN can view officers in their department.
    - SUPER_ADMIN has universal visibility.
    """
    officer = db.get(User, officer_id)
    if not officer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Officer not found.",
        )

    # Authorization
    if current_user.id != officer_id:
        if current_user.role.name != RoleName.SUPER_ADMIN:
            if current_user.role.name not in (RoleName.DEPARTMENT_ADMIN, RoleName.SUPERVISOR):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: You can only view your own assigned grievances.",
                )
            if officer.department_id != current_user.department_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Officer belongs to another department.",
                )

    query = select(Grievance).where(Grievance.assigned_to_user_id == officer_id)

    if status_filter:
        query = query.where(Grievance.status == status_filter.strip().upper())
    if priority_filter:
        query = query.where(Grievance.priority == priority_filter.strip().upper())
    if date_from:
        query = query.where(Grievance.date_received >= date_from)
    if date_to:
        query = query.where(Grievance.date_received <= date_to)

    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar_one()
    total_pages = math.ceil(total / page_size) if total > 0 else 1

    query = query.order_by(Grievance.date_received.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    items = list(db.execute(query).scalars().all())
    return items, total, total_pages


def get_officer_workload(
    db: Session,
    officer_id: uuid.UUID,
    current_user: User,
) -> OfficerWorkloadResponse:
    """
    Calculate real-time workload for an officer:
    assigned, in_progress, awaiting_information, resolved, and total active workload.
    """
    officer = db.get(User, officer_id)
    if not officer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Officer not found.",
        )

    if not officer.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Officer is inactive.",
        )

    # Authorization check
    if current_user.id != officer_id:
        if current_user.role.name != RoleName.SUPER_ADMIN:
            if current_user.role.name not in (RoleName.DEPARTMENT_ADMIN, RoleName.SUPERVISOR):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: You can only view your own workload.",
                )
            if officer.department_id != current_user.department_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Officer belongs to another department.",
                )

    # Efficient aggregate group by query
    rows = db.execute(
        select(Grievance.status, func.count(Grievance.id))
        .where(Grievance.assigned_to_user_id == officer_id)
        .group_by(Grievance.status)
    ).all()

    counts: Dict[str, int] = {status_name: count for status_name, count in rows}

    assigned_count = counts.get("ASSIGNED", 0)
    in_progress_count = counts.get("IN_PROGRESS", 0)
    awaiting_info_count = counts.get("AWAITING_INFORMATION", 0)
    resolved_count = counts.get("RESOLVED", 0)
    total_active = assigned_count + in_progress_count + awaiting_info_count

    return OfficerWorkloadResponse(
        officer_id=officer.id,
        officer_name=officer.full_name,
        officer_email=officer.email,
        department_id=officer.department_id,
        assigned_count=assigned_count,
        in_progress_count=in_progress_count,
        awaiting_information_count=awaiting_info_count,
        resolved_count=resolved_count,
        total_active_workload=total_active,
    )

