"""
Follow-up Action Service (Stage 10C).

Business rules implemented:
  - Records executed follow-up actions (calls, visits, document requests, responses).
  - Completing a task does NOT resolve or close the grievance.
  - Receiving a response (response_received=True) does NOT auto-resolve the grievance.
  - A failed call or 'no response' still marks the scheduled task COMPLETED.
  - Tasks can only be completed once; duplicate action on an already COMPLETED/CANCELLED
    task is rejected with HTTP 400.
  - Subsequent follow-up task scheduling is optional via next_follow_up_at.
  - If next_follow_up_at is supplied and another SCHEDULED task already exists,
    reject with HTTP 409 Conflict.
  - Department isolation and officer-level scoping strictly enforced.
  - All operations are transactional and audited via log_activity().
"""
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.roles import RoleName
from app.models.follow_up_action import FollowUpAction
from app.models.follow_up_task import FollowUpTask
from app.models.grievance import Grievance
from app.models.user import User
from app.schemas.action import FollowUpActionCreate
from app.services.audit import log_activity


def _check_action_access(
    current_user: User,
    grievance: Grievance,
    task: Optional[FollowUpTask] = None,
) -> None:
    """
    Object-level and department-level access control:
      - SUPER_ADMIN: universal access.
      - DEPARTMENT_ADMIN / SUPERVISOR: restricted to their department.
      - OFFICER: restricted to grievances/tasks assigned to them in their department.
    """
    if current_user.role.name == RoleName.SUPER_ADMIN:
        return

    # Department scoping
    dept_id = grievance.confirmed_department_id or grievance.suggested_department_id
    if not dept_id or current_user.department_id != dept_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Grievance belongs to another department.",
        )

    # Officer-specific assignee scoping
    if current_user.role.name == RoleName.OFFICER:
        is_assigned_to_grievance = grievance.assigned_to_user_id == current_user.id
        is_assigned_to_task = task is not None and task.assigned_to_user_id == current_user.id
        if not (is_assigned_to_grievance or is_assigned_to_task):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: You are not assigned to this grievance or follow-up task.",
            )


def _check_conflicting_scheduled_task(
    db: Session,
    grievance_id: uuid.UUID,
    current_task_id: uuid.UUID,
) -> Optional[FollowUpTask]:
    """Find any OTHER active SCHEDULED task on this grievance."""
    return (
        db.query(FollowUpTask)
        .filter(
            FollowUpTask.grievance_id == grievance_id,
            FollowUpTask.status == "SCHEDULED",
            FollowUpTask.id != current_task_id,
        )
        .first()
    )


def record_follow_up_action(
    db: Session,
    task_id: uuid.UUID,
    action_in: FollowUpActionCreate,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Tuple[FollowUpAction, Optional[uuid.UUID]]:
    """
    Record an action against a follow-up task, mark the task as COMPLETED, and
    optionally schedule the next follow-up task.

    Returns:
        (FollowUpAction, Optional[uuid.UUID next_scheduled_task_id])
    """
    now_utc = datetime.now(timezone.utc)

    # 1. Row-lock target FollowUpTask
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

    # 2. Duplicate task completion guard
    if task.status != "SCHEDULED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot record action on task with status '{task.status}'. "
                "Only SCHEDULED tasks can be completed with follow-up actions."
            ),
        )

    # 3. Row-lock target Grievance
    grievance = (
        db.query(Grievance)
        .with_for_update()
        .filter(Grievance.id == task.grievance_id)
        .first()
    )
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grievance associated with this task not found.",
        )

    # 4. Department and object-level authorization
    _check_action_access(current_user, grievance, task)

    # 5. Process action timestamp & outcome
    action_taken_at = action_in.action_taken_at or now_utc
    if action_taken_at.tzinfo is None:
        action_taken_at = action_taken_at.replace(tzinfo=timezone.utc)

    outcome = action_in.outcome
    if action_in.notes:
        outcome = f"{outcome}\nNotes: {action_in.notes}" if outcome else action_in.notes

    # Response received resolution
    if action_in.response_received is not None:
        response_received = action_in.response_received
    elif action_in.action_type == "RESPONSE_RECEIVED":
        response_received = True
    else:
        response_received = False

    # 6. Validate subsequent follow-up datetime if provided
    next_task_id: Optional[uuid.UUID] = None
    next_due: Optional[datetime] = None
    if action_in.next_follow_up_at:
        next_due = action_in.next_follow_up_at
        if next_due.tzinfo is None:
            next_due = next_due.replace(tzinfo=timezone.utc)
        if next_due <= now_utc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="next_follow_up_at must be a future datetime.",
            )

        # Conflict check: ensure no OTHER active SCHEDULED task exists
        conflicting_task = _check_conflicting_scheduled_task(db, grievance.id, task.id)
        if conflicting_task:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An active scheduled follow-up task already exists for this grievance.",
            )

    # 7. Create FollowUpAction record
    action = FollowUpAction(
        grievance_id=grievance.id,
        follow_up_task_id=task.id,
        performed_by_user_id=current_user.id,
        action_type=action_in.action_type,
        description=action_in.description.strip(),
        outcome=outcome.strip() if outcome else None,
        response_received=response_received,
        action_taken_at=action_taken_at,
        next_follow_up_at=next_due,
    )
    db.add(action)
    db.flush()

    # 8. Mark current task as COMPLETED
    task.status = "COMPLETED"
    task.completed_at = now_utc
    db.flush()

    # 9. Update Grievance last_action_at (Preserve status - do NOT auto-resolve or auto-close)
    grievance.last_action_at = now_utc

    # 10. Schedule subsequent task if next_follow_up_at was specified
    if next_due:
        new_task = FollowUpTask(
            grievance_id=grievance.id,
            assigned_to_user_id=task.assigned_to_user_id or grievance.assigned_to_user_id,
            policy_id=task.policy_id,
            due_at=next_due,
            status="SCHEDULED",
        )
        db.add(new_task)
        db.flush()
        next_task_id = new_task.id
        grievance.next_follow_up_at = next_due

        log_activity(
            db=db,
            action="FOLLOW_UP_TASK_SCHEDULED",
            entity_type="follow_up_task",
            entity_id=new_task.id,
            user_id=current_user.id,
            grievance_id=grievance.id,
            new_values={
                "due_at": next_due.isoformat(),
                "policy_id": str(task.policy_id) if task.policy_id else None,
                "trigger": "subsequent_from_action",
            },
            description=f"Subsequent follow-up task scheduled for {next_due.isoformat()} after action.",
            ip_address=ip_address,
        )

    # 11. Audit logging for action recording and task completion
    log_activity(
        db=db,
        action="FOLLOW_UP_ACTION_RECORDED",
        entity_type="follow_up_action",
        entity_id=action.id,
        user_id=current_user.id,
        grievance_id=grievance.id,
        new_values={
            "action_type": action.action_type,
            "response_received": action.response_received,
            "follow_up_task_id": str(task.id),
            "next_follow_up_at": next_due.isoformat() if next_due else None,
        },
        description=f"Follow-up action '{action.action_type}' recorded by {current_user.full_name}.",
        ip_address=ip_address,
    )

    log_activity(
        db=db,
        action="FOLLOW_UP_TASK_COMPLETED",
        entity_type="follow_up_task",
        entity_id=task.id,
        user_id=current_user.id,
        grievance_id=grievance.id,
        old_values={"status": "SCHEDULED"},
        new_values={"status": "COMPLETED", "completed_at": task.completed_at.isoformat()},
        description=f"Follow-up task completed via action '{action.action_type}'.",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(action)
    return action, next_task_id


def get_action_by_id(
    db: Session,
    action_id: uuid.UUID,
    current_user: User,
) -> FollowUpAction:
    """Retrieve an action by ID with department scoping."""
    action = db.get(FollowUpAction, action_id)
    if not action:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Follow-up action not found.",
        )
    grievance = db.get(Grievance, action.grievance_id)
    if grievance:
        _check_action_access(current_user, grievance)
    return action


def list_actions_for_task(
    db: Session,
    task_id: uuid.UUID,
    current_user: User,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[FollowUpAction], int, int]:
    """List all actions recorded against a specific follow-up task."""
    task = db.get(FollowUpTask, task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Follow-up task not found.",
        )
    grievance = db.get(Grievance, task.grievance_id)
    if grievance:
        _check_action_access(current_user, grievance, task)

    query = select(FollowUpAction).where(FollowUpAction.follow_up_task_id == task_id)
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar_one()
    total_pages = math.ceil(total / page_size) if total > 0 else 1

    query = query.order_by(FollowUpAction.action_taken_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    items = list(db.execute(query).scalars().all())
    return items, total, total_pages


def list_actions_for_grievance(
    db: Session,
    grievance_id: uuid.UUID,
    current_user: User,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[FollowUpAction], int, int]:
    """List all follow-up actions recorded for a grievance."""
    grievance = db.get(Grievance, grievance_id)
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grievance not found.",
        )
    _check_action_access(current_user, grievance)

    query = select(FollowUpAction).where(FollowUpAction.grievance_id == grievance_id)
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar_one()
    total_pages = math.ceil(total / page_size) if total > 0 else 1

    query = query.order_by(FollowUpAction.action_taken_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    items = list(db.execute(query).scalars().all())
    return items, total, total_pages
