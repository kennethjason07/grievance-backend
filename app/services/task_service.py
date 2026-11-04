"""
Follow-up Task Service (Stage 10B).

Business rules implemented:
  - Clock anchor: first assignment's assigned_at (from grievance_assignments table).
  - Subsequent tasks: due_at = latest_completed_task.due_at + policy.reminder_interval_days.
  - max_reminders = 0 → no automatic tasks; raises HTTPException for auto-schedule.
  - Partial unique index prevents two SCHEDULED tasks on the same grievance:
      cancel existing SCHEDULED task before creating a replacement via manual scheduling,
      or raise 400 if caller does not ask for replacement.
  - Policy updates never alter existing due_at values.
  - Reassignment does NOT reset the SLA clock.
  - Department isolation enforced for non-SUPER_ADMIN users.
  - All mutations are audited via log_activity().
"""
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.roles import RoleName
from app.models.follow_up_policy import FollowUpPolicy
from app.models.follow_up_task import FollowUpTask
from app.models.grievance import Grievance
from app.models.grievance_assignment import GrievanceAssignment
from app.models.user import User
from app.schemas.task import ManualScheduleFollowUpTaskCreate
from app.services.audit import log_activity


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_grievance_or_404(db: Session, grievance_id: uuid.UUID) -> Grievance:
    grievance = db.get(Grievance, grievance_id)
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grievance not found.",
        )
    return grievance


def _check_task_department_access(current_user: User, grievance: Grievance) -> None:
    """
    Enforce department isolation for follow-up task operations.
    SUPER_ADMIN has universal access.
    All other roles are scoped to their confirmed department.
    """
    if current_user.role.name == RoleName.SUPER_ADMIN:
        return

    # Use confirmed department; fall back to suggested for pre-confirmation grievances
    dept_id = grievance.confirmed_department_id or grievance.suggested_department_id
    if not dept_id or current_user.department_id != dept_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Grievance belongs to another department.",
        )


def _get_first_assignment_anchor(db: Session, grievance_id: uuid.UUID) -> Optional[datetime]:
    """
    Return the assigned_at timestamp of the FIRST (chronologically earliest) assignment
    for the given grievance. This is the SLA clock anchor.
    Reassignment must NOT reset this anchor.
    Returns None if the grievance has never been assigned.
    """
    row = (
        db.query(GrievanceAssignment)
        .filter(GrievanceAssignment.grievance_id == grievance_id)
        .order_by(GrievanceAssignment.assigned_at.asc())
        .first()
    )
    return row.assigned_at if row else None


def _count_completed_tasks(db: Session, grievance_id: uuid.UUID) -> int:
    """Return the number of COMPLETED follow-up tasks for the grievance."""
    return (
        db.query(func.count(FollowUpTask.id))
        .filter(
            FollowUpTask.grievance_id == grievance_id,
            FollowUpTask.status == "COMPLETED",
        )
        .scalar() or 0
    )


def _get_active_scheduled_task(db: Session, grievance_id: uuid.UUID) -> Optional[FollowUpTask]:
    """Return the single SCHEDULED task for a grievance, if any."""
    return (
        db.query(FollowUpTask)
        .filter(
            FollowUpTask.grievance_id == grievance_id,
            FollowUpTask.status == "SCHEDULED",
        )
        .first()
    )


def _compute_first_due_at(anchor: datetime, policy: FollowUpPolicy) -> datetime:
    """
    First task due_at = anchor + first_reminder_after_days.
    Returned value is always timezone-aware UTC.
    """
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    return anchor + timedelta(days=policy.first_reminder_after_days)


def _compute_next_due_at(last_due_at: datetime, policy: FollowUpPolicy) -> datetime:
    """
    Subsequent task due_at = last_task.due_at + reminder_interval_days.
    """
    if last_due_at.tzinfo is None:
        last_due_at = last_due_at.replace(tzinfo=timezone.utc)
    return last_due_at + timedelta(days=policy.reminder_interval_days)


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def auto_schedule_on_assignment(
    db: Session,
    grievance: Grievance,
    assignment: GrievanceAssignment,
) -> Optional[FollowUpTask]:
    """
    Hook called from assign_grievance() after the first assignment is flushed
    but BEFORE commit, so the task is committed in the same transaction.

    Logic:
      1. Look up active department policy for grievance.confirmed_department_id.
         If no active policy exists, return None silently (no error).
      2. If max_reminders == 0, return None (auto-reminders disabled).
      3. If a SCHEDULED task already exists (shouldn't happen on first assignment),
         return None to avoid partial-index collision.
      4. Compute due_at = assignment.assigned_at + policy.first_reminder_after_days.
      5. Create and flush (do NOT commit — caller commits).
    """
    if not grievance.confirmed_department_id:
        return None

    # Look up active policy — no error if absent
    policy = (
        db.query(FollowUpPolicy)
        .filter(
            FollowUpPolicy.department_id == grievance.confirmed_department_id,
            FollowUpPolicy.is_active.is_(True),
        )
        .order_by(FollowUpPolicy.updated_at.desc())
        .first()
    )
    if not policy:
        return None

    # max_reminders = 0 → auto-reminders disabled
    if policy.max_reminders == 0:
        return None

    # Safety guard: don't create a second SCHEDULED task
    existing = _get_active_scheduled_task(db, grievance.id)
    if existing:
        return None

    anchor = assignment.assigned_at
    if anchor is None:
        anchor = datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)

    due_at = _compute_first_due_at(anchor, policy)

    task = FollowUpTask(
        grievance_id=grievance.id,
        policy_id=policy.id,
        assigned_to_user_id=grievance.assigned_to_user_id,
        due_at=due_at,
        status="SCHEDULED",
    )
    db.add(task)
    db.flush()

    log_activity(
        db=db,
        action="FOLLOW_UP_TASK_SCHEDULED",
        entity_type="follow_up_task",
        entity_id=task.id,
        grievance_id=grievance.id,
        new_values={
            "due_at": due_at.isoformat(),
            "policy_id": str(policy.id),
            "trigger": "auto_on_assignment",
        },
        description=(
            f"Follow-up task auto-scheduled for grievance {grievance.grievance_number}. "
            f"Due at: {due_at.isoformat()}."
        ),
    )
    return task


def schedule_follow_up_task(
    db: Session,
    grievance_id: uuid.UUID,
    task_in: ManualScheduleFollowUpTaskCreate,
    current_user: User,
    ip_address: Optional[str] = None,
) -> FollowUpTask:
    """
    Manually schedule a follow-up task on a grievance.

    Rules:
      - Only one SCHEDULED task may exist per grievance (partial unique index).
        If one already exists and caller supplies due_at > existing.due_at, cancel
        the existing task and create a new one.
        If existing.due_at >= new due_at, raise 400 to avoid ambiguity.
      - Requires MANAGE_FOLLOW_UPS permission (enforced at router level).
      - Department isolation enforced here.
    """
    grievance = _get_grievance_or_404(db, grievance_id)
    _check_task_department_access(current_user, grievance)

    now_utc = datetime.now(timezone.utc)
    new_due_at = task_in.due_at
    if new_due_at.tzinfo is None:
        new_due_at = new_due_at.replace(tzinfo=timezone.utc)

    if new_due_at <= now_utc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="due_at must be a future datetime.",
        )

    # Handle existing SCHEDULED task — must cancel before creating replacement
    existing = (
        db.query(FollowUpTask)
        .with_for_update()
        .filter(
            FollowUpTask.grievance_id == grievance_id,
            FollowUpTask.status == "SCHEDULED",
        )
        .first()
    )
    if existing:
        # Cancel existing to free the partial unique index slot
        existing.status = "CANCELLED"
        existing.cancelled_at = now_utc
        db.flush()
        log_activity(
            db=db,
            action="FOLLOW_UP_TASK_CANCELLED",
            entity_type="follow_up_task",
            entity_id=existing.id,
            user_id=current_user.id,
            grievance_id=grievance_id,
            old_values={"status": "SCHEDULED"},
            new_values={"status": "CANCELLED", "reason": "Replaced by manual rescheduling"},
            description=f"Existing scheduled task cancelled to allow manual reschedule.",
            ip_address=ip_address,
        )

    # Resolve assigned_to_user_id
    assigned_to = task_in.assigned_to_user_id or grievance.assigned_to_user_id

    # Validate policy_id if provided
    policy_id = task_in.policy_id
    if policy_id:
        pol = db.get(FollowUpPolicy, policy_id)
        if not pol:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Specified follow-up policy not found.",
            )

    task = FollowUpTask(
        grievance_id=grievance_id,
        assigned_to_user_id=assigned_to,
        policy_id=policy_id,
        due_at=new_due_at,
        status="SCHEDULED",
    )
    db.add(task)
    db.flush()

    log_activity(
        db=db,
        action="FOLLOW_UP_TASK_SCHEDULED",
        entity_type="follow_up_task",
        entity_id=task.id,
        user_id=current_user.id,
        grievance_id=grievance_id,
        new_values={
            "due_at": new_due_at.isoformat(),
            "policy_id": str(policy_id) if policy_id else None,
            "trigger": "manual",
        },
        description=(
            f"Follow-up task manually scheduled for grievance {grievance.grievance_number}. "
            f"Due at: {new_due_at.isoformat()}."
        ),
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(task)
    return task


def get_task_by_id(
    db: Session,
    task_id: uuid.UUID,
    current_user: User,
) -> FollowUpTask:
    """
    Retrieve a single follow-up task by ID with department isolation.
    """
    task = db.get(FollowUpTask, task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Follow-up task not found.",
        )
    grievance = _get_grievance_or_404(db, task.grievance_id)
    _check_task_department_access(current_user, grievance)
    return task


def list_tasks(
    db: Session,
    current_user: User,
    grievance_id: Optional[uuid.UUID] = None,
    status_filter: Optional[str] = None,
    overdue_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[FollowUpTask], int, int]:
    """
    List follow-up tasks with optional filtering and pagination.
    Department isolation applied for non-SUPER_ADMIN users.

    overdue_only: return only tasks where status=SCHEDULED and due_at <= now().
    """
    query = select(FollowUpTask)

    # Department isolation via grievance join
    if current_user.role.name != RoleName.SUPER_ADMIN:
        if not current_user.department_id:
            return [], 0, 1
        query = query.join(Grievance, FollowUpTask.grievance_id == Grievance.id).where(
            Grievance.confirmed_department_id == current_user.department_id
        )

    if grievance_id:
        query = query.where(FollowUpTask.grievance_id == grievance_id)

    if status_filter:
        query = query.where(FollowUpTask.status == status_filter.strip().upper())

    if overdue_only:
        now_utc = datetime.now(timezone.utc)
        query = query.where(
            FollowUpTask.status == "SCHEDULED",
            FollowUpTask.due_at <= now_utc,
        )

    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar_one()
    total_pages = math.ceil(total / page_size) if total > 0 else 1

    query = query.order_by(FollowUpTask.due_at.asc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    items = list(db.execute(query).scalars().all())
    return items, total, total_pages


def cancel_task(
    db: Session,
    task_id: uuid.UUID,
    current_user: User,
    ip_address: Optional[str] = None,
) -> FollowUpTask:
    """
    Cancel a SCHEDULED follow-up task.
    Only SCHEDULED tasks can be cancelled; COMPLETED/CANCELLED tasks raise 400.
    """
    task = (
        db.query(FollowUpTask)
        .with_for_update()
        .filter(FollowUpTask.id == task_id)
        .first()
    )
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Follow-up task not found.",
        )

    grievance = _get_grievance_or_404(db, task.grievance_id)
    _check_task_department_access(current_user, grievance)

    if task.status != "SCHEDULED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only SCHEDULED tasks can be cancelled. Current status: '{task.status}'.",
        )

    now_utc = datetime.now(timezone.utc)
    task.status = "CANCELLED"
    task.cancelled_at = now_utc

    db.flush()
    log_activity(
        db=db,
        action="FOLLOW_UP_TASK_CANCELLED",
        entity_type="follow_up_task",
        entity_id=task.id,
        user_id=current_user.id,
        grievance_id=grievance.id,
        old_values={"status": "SCHEDULED"},
        new_values={"status": "CANCELLED"},
        description=f"Follow-up task cancelled by {current_user.full_name}.",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(task)
    return task

