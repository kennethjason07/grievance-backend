"""
Escalation Processing Service (Stage 10E).

Business rules implemented:
  - Escalation clock anchor: First grievance assignment timestamp (grievance_assignments.assigned_at).
  - Reassignment never resets the escalation clock or SLA anchor.
  - Escalation trigger date = anchor + policy.escalation_after_days.
  - Resolved and closed grievances are never escalated.
  - Completed or cancelled follow-up tasks do not independently trigger escalation.
  - Recipient Selection:
      1. Primary: Active SUPERVISOR in the grievance's confirmed department (prioritizing matching office).
         Deterministic sorting: matching office first, then earliest created_at, then id.
      2. Fallback: Active DEPARTMENT_ADMIN in the confirmed department.
      3. If neither exists, logs ESCALATION_PROCESSING_FAILED in activity_logs (never silently discarded).
  - Does NOT automatically reassign the grievance (grievance.assigned_to_user_id preserved).
  - Generates IN_APP notification for the escalation recipient.
  - Concurrency & Idempotency:
      * Enforces at most one active (PENDING/ACKNOWLEDGED) escalation per grievance via
        database partial unique index `uq_escalations_active_grievance`.
      * Uses savepoints (begin_nested) to handle concurrent collisions safely.
  - Acknowledging, resolving, or dismissing an escalation does NOT resolve the grievance.
"""
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.roles import RoleName
from app.models.escalation import Escalation
from app.models.follow_up_policy import FollowUpPolicy
from app.models.follow_up_task import FollowUpTask
from app.models.grievance import Grievance
from app.models.grievance_assignment import GrievanceAssignment
from app.models.notification import Notification
from app.models.role import Role
from app.models.user import User
from app.schemas.escalation import EscalationDismissRequest, EscalationResolveRequest
from app.services.audit import log_activity
from app.utils.notification_idempotency import build_idempotency_key


def _get_first_assignment_anchor(db: Session, grievance_id: uuid.UUID) -> Optional[datetime]:
    """Return the assigned_at timestamp of the earliest assignment for this grievance."""
    assignment = (
        db.query(GrievanceAssignment)
        .filter(GrievanceAssignment.grievance_id == grievance_id)
        .order_by(GrievanceAssignment.assigned_at.asc())
        .first()
    )
    return assignment.assigned_at if assignment else None


def _select_escalation_recipient(db: Session, grievance: Grievance) -> Optional[User]:
    """
    Deterministic selection of escalation recipient:
      1. Active SUPERVISOR in grievance confirmed department (matching office prioritized, then seniority).
      2. Fallback: Active DEPARTMENT_ADMIN in confirmed department (seniority).
      3. Returns None if no eligible user found.
    """
    dept_id = grievance.confirmed_department_id or grievance.suggested_department_id
    if not dept_id:
        return None

    # Primary: SUPERVISOR in department
    supervisor_query = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(
            Role.name == RoleName.SUPERVISOR,
            User.department_id == dept_id,
            User.is_active.is_(True),
        )
    )

    if grievance.office_id:
        supervisor_query = supervisor_query.order_by(
            case((User.office_id == grievance.office_id, 0), else_=1),
            User.created_at.asc(),
            User.id.asc(),
        )
    else:
        supervisor_query = supervisor_query.order_by(
            User.created_at.asc(),
            User.id.asc(),
        )

    supervisor = supervisor_query.first()
    if supervisor:
        return supervisor

    # Fallback: DEPARTMENT_ADMIN in department
    admin_query = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(
            Role.name == RoleName.DEPARTMENT_ADMIN,
            User.department_id == dept_id,
            User.is_active.is_(True),
        )
        .order_by(
            User.created_at.asc(),
            User.id.asc(),
        )
    )
    return admin_query.first()


def _check_escalation_access(current_user: User, escalation: Escalation, grievance: Grievance) -> None:
    """Enforce department and recipient scoping for escalation access."""
    if current_user.role.name == RoleName.SUPER_ADMIN:
        return

    dept_id = grievance.confirmed_department_id or grievance.suggested_department_id
    if not dept_id or current_user.department_id != dept_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Escalation belongs to another department.",
        )

    # Officers and viewers can only see escalations where they are involved
    if current_user.role.name not in (RoleName.DEPARTMENT_ADMIN, RoleName.SUPERVISOR):
        if current_user.id not in (escalation.escalated_to_user_id, escalation.escalated_from_user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: You are not a party to this escalation.",
            )


def process_due_escalations(
    db: Session,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Synchronous escalation processor.

    Evaluates open grievances against department escalation_after_days policy,
    anchored to the initial assignment timestamp. Creates Escalation records,
    generates IN_APP notifications, and logs audit events.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # 1. Fetch eligible grievances (not resolved or closed)
    grievances = (
        db.query(Grievance)
        .filter(Grievance.status.notin_(["RESOLVED", "CLOSED"]))
        .all()
    )

    escalated_count = 0
    skipped_count = 0
    failure_count = 0

    for grievance in grievances:
        dept_id = grievance.confirmed_department_id or grievance.suggested_department_id
        if not dept_id:
            continue

        # 2. Check for existing active escalation (PENDING or ACKNOWLEDGED)
        active_escalation = (
            db.query(Escalation)
            .filter(
                Escalation.grievance_id == grievance.id,
                Escalation.status.in_(["PENDING", "ACKNOWLEDGED"]),
            )
            .first()
        )
        if active_escalation:
            skipped_count += 1
            continue

        # 3. Determine first assignment clock anchor
        anchor = _get_first_assignment_anchor(db, grievance.id)
        if not anchor:
            # Grievance has never been assigned; cannot escalate
            continue
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)

        # 4. Get active policy for department
        policy = (
            db.query(FollowUpPolicy)
            .filter(
                FollowUpPolicy.department_id == dept_id,
                FollowUpPolicy.is_active.is_(True),
            )
            .order_by(FollowUpPolicy.updated_at.desc())
            .first()
        )
        if not policy:
            continue

        # 5. Evaluate escalation threshold
        trigger_date = anchor + timedelta(days=policy.escalation_after_days)
        if now < trigger_date:
            # SLA threshold not yet crossed
            continue

        # 6. Select escalation recipient
        recipient = _select_escalation_recipient(db, grievance)
        if not recipient:
            failure_count += 1
            log_activity(
                db=db,
                action="ESCALATION_PROCESSING_FAILED",
                entity_type="grievance",
                entity_id=grievance.id,
                grievance_id=grievance.id,
                new_values={"reason": "No eligible supervisor or department admin found"},
                description=(
                    f"Escalation failed for grievance {grievance.grievance_number}: "
                    f"No active supervisor or department admin available in department {dept_id}."
                ),
            )
            continue

        # 7. Check for active scheduled task if any
        active_task = (
            db.query(FollowUpTask)
            .filter(
                FollowUpTask.grievance_id == grievance.id,
                FollowUpTask.status == "SCHEDULED",
            )
            .first()
        )

        # 8. Atomic savepoint insertion of Escalation and Notification
        savepoint = db.begin_nested()
        try:
            escalation = Escalation(
                grievance_id=grievance.id,
                follow_up_task_id=active_task.id if active_task else None,
                escalated_from_user_id=grievance.assigned_to_user_id,
                escalated_to_user_id=recipient.id,
                reason=(
                    f"Grievance {grievance.grievance_number} breached the escalation SLA threshold "
                    f"of {policy.escalation_after_days} days without resolution."
                ),
                trigger_date=trigger_date,
                escalated_at=now,
                status="PENDING",
            )
            db.add(escalation)
            db.flush()

            # Notification generation
            idempotency_key = build_idempotency_key(
                grievance_id=grievance.id,
                channel="IN_APP",
                notification_type="ESCALATION",
                scheduled_date=trigger_date.date(),
                follow_up_task_id=active_task.id if active_task else None,
            )

            # Insert notification if not already present
            existing_notif = (
                db.query(Notification.id)
                .filter(Notification.idempotency_key == idempotency_key)
                .first()
            )
            if not existing_notif:
                notif = Notification(
                    grievance_id=grievance.id,
                    follow_up_task_id=active_task.id if active_task else None,
                    recipient_user_id=recipient.id,
                    recipient_name=recipient.full_name,
                    recipient_contact=recipient.email,
                    notification_type="ESCALATION",
                    channel="IN_APP",
                    subject=f"Escalation Notice: Grievance {grievance.grievance_number}",
                    message=(
                        f"Grievance '{grievance.title}' ({grievance.grievance_number}) has been escalated to you. "
                        f"The SLA threshold of {policy.escalation_after_days} days was exceeded."
                    ),
                    status="GENERATED",
                    scheduled_at=trigger_date,
                    generated_at=now,
                    idempotency_key=idempotency_key,
                )
                db.add(notif)
                db.flush()

            log_activity(
                db=db,
                action="GRIEVANCE_ESCALATED",
                entity_type="escalation",
                entity_id=escalation.id,
                grievance_id=grievance.id,
                new_values={
                    "escalated_from_user_id": str(grievance.assigned_to_user_id) if grievance.assigned_to_user_id else None,
                    "escalated_to_user_id": str(recipient.id),
                    "trigger_date": trigger_date.isoformat(),
                    "policy_id": str(policy.id),
                },
                description=(
                    f"Grievance {grievance.grievance_number} escalated to {recipient.full_name} "
                    f"({recipient.role.name}) after {policy.escalation_after_days} days."
                ),
            )

            savepoint.commit()
            escalated_count += 1
        except IntegrityError:
            savepoint.rollback()
            skipped_count += 1

    db.commit()

    return {
        "evaluated_grievances": len(grievances),
        "escalated_count": escalated_count,
        "skipped_or_existing": skipped_count,
        "failures": failure_count,
        "evaluated_at": now,
    }


def get_escalation_by_id(
    db: Session,
    escalation_id: uuid.UUID,
    current_user: User,
) -> Escalation:
    """Retrieve an escalation by ID with department scoping."""
    escalation = db.get(Escalation, escalation_id)
    if not escalation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Escalation not found.",
        )
    grievance = db.get(Grievance, escalation.grievance_id)
    if grievance:
        _check_escalation_access(current_user, escalation, grievance)
    return escalation


def list_escalations(
    db: Session,
    current_user: User,
    grievance_id: Optional[uuid.UUID] = None,
    status_filter: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[Escalation], int, int]:
    """List escalations with pagination, filters, and department scoping."""
    query = select(Escalation).join(Grievance, Escalation.grievance_id == Grievance.id)

    # Scoping
    if current_user.role.name != RoleName.SUPER_ADMIN:
        dept_id = current_user.department_id
        if not dept_id:
            return [], 0, 1
        query = query.where(Grievance.confirmed_department_id == dept_id)

        if current_user.role.name not in (RoleName.DEPARTMENT_ADMIN, RoleName.SUPERVISOR):
            query = query.where(
                (Escalation.escalated_to_user_id == current_user.id)
                | (Escalation.escalated_from_user_id == current_user.id)
            )

    if grievance_id:
        query = query.where(Escalation.grievance_id == grievance_id)

    if status_filter:
        query = query.where(Escalation.status == status_filter.strip().upper())

    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar_one()
    total_pages = math.ceil(total / page_size) if total > 0 else 1

    query = query.order_by(Escalation.escalated_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    items = list(db.execute(query).scalars().all())
    return items, total, total_pages


def acknowledge_escalation(
    db: Session,
    escalation_id: uuid.UUID,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Escalation:
    """Acknowledge an escalation (PENDING -> ACKNOWLEDGED). Grievance status unchanged."""
    escalation = (
        db.query(Escalation)
        .with_for_update()
        .filter(Escalation.id == escalation_id)
        .first()
    )
    if not escalation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Escalation not found.",
        )

    grievance = db.get(Grievance, escalation.grievance_id)
    if grievance:
        _check_escalation_access(current_user, escalation, grievance)

    if escalation.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only PENDING escalations can be acknowledged. Current status: '{escalation.status}'.",
        )

    escalation.status = "ACKNOWLEDGED"

    log_activity(
        db=db,
        action="ESCALATION_ACKNOWLEDGED",
        entity_type="escalation",
        entity_id=escalation.id,
        user_id=current_user.id,
        grievance_id=escalation.grievance_id,
        old_values={"status": "PENDING"},
        new_values={"status": "ACKNOWLEDGED"},
        description=f"Escalation {escalation.id} acknowledged by {current_user.full_name}.",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(escalation)
    return escalation


def resolve_escalation(
    db: Session,
    escalation_id: uuid.UUID,
    resolve_in: EscalationResolveRequest,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Escalation:
    """Resolve an active escalation (PENDING/ACKNOWLEDGED -> RESOLVED). Grievance status unchanged."""
    escalation = (
        db.query(Escalation)
        .with_for_update()
        .filter(Escalation.id == escalation_id)
        .first()
    )
    if not escalation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Escalation not found.",
        )

    grievance = db.get(Grievance, escalation.grievance_id)
    if grievance:
        _check_escalation_access(current_user, escalation, grievance)

    if escalation.status not in ("PENDING", "ACKNOWLEDGED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot resolve escalation with status '{escalation.status}'. Must be PENDING or ACKNOWLEDGED.",
        )

    now_utc = datetime.now(timezone.utc)
    old_status = escalation.status
    escalation.status = "RESOLVED"
    escalation.resolved_at = now_utc
    if resolve_in.resolution_notes:
        escalation.resolution_notes = resolve_in.resolution_notes.strip()

    log_activity(
        db=db,
        action="ESCALATION_RESOLVED",
        entity_type="escalation",
        entity_id=escalation.id,
        user_id=current_user.id,
        grievance_id=escalation.grievance_id,
        old_values={"status": old_status},
        new_values={"status": "RESOLVED", "resolved_at": now_utc.isoformat()},
        description=f"Escalation {escalation.id} resolved by {current_user.full_name}.",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(escalation)
    return escalation


def dismiss_escalation(
    db: Session,
    escalation_id: uuid.UUID,
    dismiss_in: EscalationDismissRequest,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Escalation:
    """Dismiss an active escalation with mandatory reason. Grievance status unchanged."""
    escalation = (
        db.query(Escalation)
        .with_for_update()
        .filter(Escalation.id == escalation_id)
        .first()
    )
    if not escalation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Escalation not found.",
        )

    grievance = db.get(Grievance, escalation.grievance_id)
    if grievance:
        _check_escalation_access(current_user, escalation, grievance)

    if escalation.status not in ("PENDING", "ACKNOWLEDGED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot dismiss escalation with status '{escalation.status}'. Must be PENDING or ACKNOWLEDGED.",
        )

    now_utc = datetime.now(timezone.utc)
    old_status = escalation.status
    escalation.status = "DISMISSED"
    escalation.resolved_at = now_utc
    escalation.resolution_notes = dismiss_in.reason.strip()

    log_activity(
        db=db,
        action="ESCALATION_DISMISSED",
        entity_type="escalation",
        entity_id=escalation.id,
        user_id=current_user.id,
        grievance_id=escalation.grievance_id,
        old_values={"status": old_status},
        new_values={"status": "DISMISSED", "reason": dismiss_in.reason.strip()},
        description=f"Escalation {escalation.id} dismissed by {current_user.full_name}. Reason: {dismiss_in.reason.strip()}",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(escalation)
    return escalation

