"""
Grievance Lifecycle Service (Stage 9E).
Manages workflow transitions from ASSIGNED through IN_PROGRESS, AWAITING_INFORMATION,
RESOLVED, and CLOSED with row-level locking, atomic transactions, and timeline construction.
"""
import uuid
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.roles import RoleName
from app.models.activity_log import ActivityLog
from app.models.grievance import Grievance
from app.models.user import User
from app.schemas.lifecycle import (
    ClosureCreate,
    InformationRequestCreate,
    ReopenCreate,
    ResolutionCreate,
    ResumeInvestigationCreate,
    TimelineEvent,
    TimelineResponse,
)
from app.services.audit import log_activity
from app.services.grievance_lifecycle import validate_lifecycle_transition, validate_reopen_transition


def _lock_and_get_grievance(db: Session, grievance_id: uuid.UUID) -> Grievance:
    """
    Acquires a row-level lock (SELECT FOR UPDATE) on the target grievance to serialize
    concurrent status updates.
    """
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
    return grievance


def start_investigation(
    db: Session,
    grievance_id: uuid.UUID,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Grievance:
    """
    Transition grievance status: ASSIGNED -> IN_PROGRESS.
    Requires caller to be the active assigned officer, supervisor, or administrator.
    """
    grievance = _lock_and_get_grievance(db, grievance_id)
    old_status = grievance.status

    validate_lifecycle_transition(grievance, "IN_PROGRESS", current_user)

    now_utc = datetime.now(timezone.utc)
    grievance.status = "IN_PROGRESS"
    grievance.last_action_at = now_utc

    db.flush()

    log_activity(
        db=db,
        action="INVESTIGATION_STARTED",
        entity_type="grievance",
        entity_id=grievance.id,
        user_id=current_user.id,
        grievance_id=grievance.id,
        old_values={"status": old_status},
        new_values={"status": grievance.status},
        description=f"Investigation initiated on grievance {grievance.grievance_number} by {current_user.full_name}.",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(grievance)
    return grievance


def request_information(
    db: Session,
    grievance_id: uuid.UUID,
    info_in: InformationRequestCreate,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Grievance:
    """
    Transition grievance status: IN_PROGRESS -> AWAITING_INFORMATION.
    Records details of information requested and optionally updates next_follow_up_at.
    """
    grievance = _lock_and_get_grievance(db, grievance_id)
    old_status = grievance.status

    validate_lifecycle_transition(grievance, "AWAITING_INFORMATION", current_user)

    now_utc = datetime.now(timezone.utc)
    grievance.status = "AWAITING_INFORMATION"
    grievance.last_action_at = now_utc

    if info_in.next_follow_up_at:
        grievance.next_follow_up_at = info_in.next_follow_up_at

    db.flush()

    log_activity(
        db=db,
        action="INFORMATION_REQUESTED",
        entity_type="grievance",
        entity_id=grievance.id,
        user_id=current_user.id,
        grievance_id=grievance.id,
        old_values={"status": old_status},
        new_values={
            "status": grievance.status,
            "information_required": info_in.information_required,
            "next_follow_up_at": str(grievance.next_follow_up_at) if grievance.next_follow_up_at else None,
        },
        description=f"Information requested for grievance {grievance.grievance_number}: {info_in.information_required}",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(grievance)
    return grievance


def resume_investigation(
    db: Session,
    grievance_id: uuid.UUID,
    resume_in: ResumeInvestigationCreate,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Grievance:
    """
    Transition grievance status: AWAITING_INFORMATION -> IN_PROGRESS.
    Notes received information or supervisory override.
    """
    grievance = _lock_and_get_grievance(db, grievance_id)
    old_status = grievance.status

    validate_lifecycle_transition(grievance, "IN_PROGRESS", current_user)

    now_utc = datetime.now(timezone.utc)
    grievance.status = "IN_PROGRESS"
    grievance.last_action_at = now_utc

    db.flush()

    log_activity(
        db=db,
        action="INVESTIGATION_RESUMED",
        entity_type="grievance",
        entity_id=grievance.id,
        user_id=current_user.id,
        grievance_id=grievance.id,
        old_values={"status": old_status},
        new_values={
            "status": grievance.status,
            "information_received": resume_in.information_received,
            "notes": resume_in.notes,
        },
        description=f"Investigation resumed for grievance {grievance.grievance_number}.",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(grievance)
    return grievance


def resolve_grievance(
    db: Session,
    grievance_id: uuid.UUID,
    resolution_in: ResolutionCreate,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Grievance:
    """
    Transition grievance status: IN_PROGRESS -> RESOLVED.
    Sets resolved_at, stores resolution findings, and records full audit log.
    """
    grievance = _lock_and_get_grievance(db, grievance_id)
    old_status = grievance.status

    validate_lifecycle_transition(grievance, "RESOLVED", current_user)

    now_utc = datetime.now(timezone.utc)
    resolution_time = resolution_in.resolution_date or now_utc

    grievance.status = "RESOLVED"
    grievance.last_action_at = now_utc
    grievance.resolved_at = resolution_time
    grievance.neutral_summary = resolution_in.resolution_summary

    db.flush()

    log_activity(
        db=db,
        action="GRIEVANCE_RESOLVED",
        entity_type="grievance",
        entity_id=grievance.id,
        user_id=current_user.id,
        grievance_id=grievance.id,
        old_values={"status": old_status},
        new_values={
            "status": grievance.status,
            "resolution_summary": resolution_in.resolution_summary,
            "resolution_details": resolution_in.resolution_details,
            "supporting_notes": resolution_in.supporting_notes,
            "resolved_at": str(resolution_time),
        },
        description=f"Grievance {grievance.grievance_number} resolved by {current_user.full_name}: {resolution_in.resolution_summary}",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(grievance)
    return grievance


def close_grievance(
    db: Session,
    grievance_id: uuid.UUID,
    closure_in: ClosureCreate,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Grievance:
    """
    Transition grievance status: RESOLVED -> CLOSED.
    Marks grievance as CLOSED (terminal state), sets closed_at, and records closure notes.
    Requires administrative authority (SUPER_ADMIN or DEPARTMENT_ADMIN).
    """
    grievance = _lock_and_get_grievance(db, grievance_id)
    old_status = grievance.status

    validate_lifecycle_transition(grievance, "CLOSED", current_user)

    now_utc = datetime.now(timezone.utc)
    grievance.status = "CLOSED"
    grievance.last_action_at = now_utc
    grievance.closed_at = now_utc

    db.flush()

    log_activity(
        db=db,
        action="GRIEVANCE_CLOSED",
        entity_type="grievance",
        entity_id=grievance.id,
        user_id=current_user.id,
        grievance_id=grievance.id,
        old_values={"status": old_status},
        new_values={
            "status": grievance.status,
            "closure_reason": closure_in.closure_reason,
            "closure_notes": closure_in.closure_notes,
            "closed_at": str(now_utc),
        },
        description=f"Grievance {grievance.grievance_number} closed by {current_user.full_name}. Reason: {closure_in.closure_reason}",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(grievance)
    return grievance


def reopen_grievance(
    db: Session,
    grievance_id: uuid.UUID,
    reopen_in: ReopenCreate,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Grievance:
    """
    Administratively reopen a RESOLVED grievance back to IN_PROGRESS.
    Policy:
      - Only SUPER_ADMIN or DEPARTMENT_ADMIN may reopen.
      - A mandatory reopen_reason must be supplied and recorded in activity_logs.
      - closed_at and resolved_at are cleared; last_action_at is updated to now.
      - CLOSED grievances cannot be reopened (terminal state is preserved).
    """
    grievance = _lock_and_get_grievance(db, grievance_id)
    old_status = grievance.status

    # Dedicated reopen validation (not part of forward transition matrix)
    validate_reopen_transition(grievance, current_user)

    now_utc = datetime.now(timezone.utc)
    grievance.status = "IN_PROGRESS"
    grievance.last_action_at = now_utc
    grievance.resolved_at = None   # Clear previous resolution timestamp

    db.flush()

    log_activity(
        db=db,
        action="GRIEVANCE_REOPENED",
        entity_type="grievance",
        entity_id=grievance.id,
        user_id=current_user.id,
        grievance_id=grievance.id,
        old_values={"status": old_status},
        new_values={
            "status": grievance.status,
            "reopen_reason": reopen_in.reopen_reason,
            "reopened_by": current_user.email,
        },
        description=f"Grievance {grievance.grievance_number} reopened by {current_user.full_name}. Reason: {reopen_in.reopen_reason}",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(grievance)
    return grievance


def get_grievance_timeline(
    db: Session,
    grievance_id: uuid.UUID,
    current_user: User,
) -> TimelineResponse:
    """
    Construct a unified, chronological grievance lifecycle timeline from activity logs.
    Enforces cross-department isolation.
    """
    grievance = db.get(Grievance, grievance_id)
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grievance not found.",
        )

    if current_user.role.name != RoleName.SUPER_ADMIN:
        dept_id = grievance.confirmed_department_id or grievance.suggested_department_id
        if current_user.department_id != dept_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Grievance belongs to another department.",
            )

    logs = (
        db.query(ActivityLog)
        .filter(ActivityLog.grievance_id == grievance.id)
        .order_by(ActivityLog.created_at.asc())
        .all()
    )

    events: List[TimelineEvent] = []
    for log in logs:
        old_status = log.old_values.get("status") if log.old_values else None
        new_status = log.new_values.get("status") if log.new_values else None
        events.append(
            TimelineEvent(
                id=log.id,
                action=log.action,
                created_at=log.created_at,
                performing_user_id=log.user_id,
                performing_user_name=log.user.full_name if log.user else None,
                description=log.description,
                old_status=old_status,
                new_status=new_status,
                details=log.new_values,
            )
        )

    return TimelineResponse(
        grievance_id=grievance.id,
        grievance_number=grievance.grievance_number,
        current_status=grievance.status,
        total_events=len(events),
        timeline=events,
    )

