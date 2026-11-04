"""
Notification and Reminder Generation Service (Stage 10D).

Business rules implemented:
  - Synchronous process_due_reminders(db, now=None) for deterministic scheduling.
  - Generates notifications based on scheduled_at, NOT purely waiting for task due_at to be overdue.
  - Reminder #1 scheduled_at: task.due_at (anchored to first assignment + first_reminder_after_days).
  - Subsequent reminders scheduled_at: latest_reminder.scheduled_at + reminder_interval_days.
  - Policy max_reminders:
      * max_reminders = 0 disables automatic reminders completely.
      * max_reminders > 0 caps total generated reminder notifications per scheduled cycle.
  - Idempotency & Concurrency:
      * Deterministic keys from app.utils.notification_idempotency.
      * Database UNIQUE constraint on idempotency_key prevents duplicate delivery.
      * Atomic SAVEPOINT (begin_nested) handles concurrent collision safely without
        aborting the outer transaction.
  - Missing and inactive recipients:
      * Never silently discarded.
      * Failure logged with details in ActivityLog under REMINDER_PROCESSING_FAILED.
  - Notification state machine:
      * Validates status transitions: GENERATED -> QUEUED -> SENT -> DELIVERED -> ACKNOWLEDGED.
      * IN_APP notifications can be directly ACKNOWLEDGED by the recipient.
      * Acknowledging a notification NEVER alters grievance status.
  - Department and recipient scoping strictly enforced for all queries and mutations.
"""
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.roles import RoleName
from app.models.follow_up_policy import FollowUpPolicy
from app.models.follow_up_task import FollowUpTask
from app.models.grievance import Grievance
from app.models.message_template import MessageTemplate
from app.models.notification import Notification
from app.models.user import User
from app.services.audit import log_activity
from app.utils.notification_idempotency import build_idempotency_key

# Valid status transition graph for notifications
ALLOWED_NOTIFICATION_TRANSITIONS: Dict[str, Set[str]] = {
    "GENERATED": {"QUEUED", "SENT", "DELIVERED", "ACKNOWLEDGED", "FAILED"},
    "QUEUED": {"SENT", "DELIVERED", "ACKNOWLEDGED", "FAILED"},
    "SENT": {"DELIVERED", "ACKNOWLEDGED", "FAILED"},
    "DELIVERED": {"ACKNOWLEDGED", "RESPONSE_RECEIVED", "FAILED"},
    "ACKNOWLEDGED": {"RESPONSE_RECEIVED"},
    "RESPONSE_RECEIVED": set(),
    "FAILED": {"GENERATED", "QUEUED"},
}


def validate_notification_transition(current_status: str, target_status: str) -> None:
    """Validate that moving from current_status to target_status is permitted."""
    current_status = current_status.strip().upper()
    target_status = target_status.strip().upper()

    if current_status == target_status:
        return

    allowed = ALLOWED_NOTIFICATION_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid notification status transition from '{current_status}' to '{target_status}'. "
                f"Allowed next states: {', '.join(sorted(allowed)) if allowed else 'None'}"
            ),
        )


def _check_notification_access(current_user: User, notification: Notification, grievance: Grievance) -> None:
    """Enforce department and recipient scoping."""
    if current_user.role.name == RoleName.SUPER_ADMIN:
        return

    # Check department access
    dept_id = grievance.confirmed_department_id or grievance.suggested_department_id
    if not dept_id or current_user.department_id != dept_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Notification belongs to another department.",
        )

    # Scoping for non-administrative roles: must be the recipient
    if current_user.role.name not in (RoleName.DEPARTMENT_ADMIN, RoleName.SUPERVISOR):
        if notification.recipient_user_id and notification.recipient_user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: You are not the recipient of this notification.",
            )


def process_due_reminders(
    db: Session,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Synchronous reminder generation processor.

    Evaluates active SCHEDULED follow-up tasks against department policies,
    calculates notification scheduled_at dates, checks idempotency keys, and
    generates IN_APP reminder notifications.

    Returns summary metrics.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # 1. Fetch all eligible SCHEDULED tasks
    tasks = (
        db.query(FollowUpTask)
        .join(Grievance, FollowUpTask.grievance_id == Grievance.id)
        .filter(
            FollowUpTask.status == "SCHEDULED",
            Grievance.status.notin_(["RESOLVED", "CLOSED"]),
        )
        .all()
    )

    generated_count = 0
    collision_count = 0
    failure_count = 0

    for task in tasks:
        grievance = task.grievance
        if not grievance:
            continue

        dept_id = grievance.confirmed_department_id or grievance.suggested_department_id

        # 2. Look up applicable policy
        policy: Optional[FollowUpPolicy] = None
        if task.policy_id:
            policy = db.get(FollowUpPolicy, task.policy_id)
        if not policy and dept_id:
            policy = (
                db.query(FollowUpPolicy)
                .filter(FollowUpPolicy.department_id == dept_id, FollowUpPolicy.is_active.is_(True))
                .order_by(FollowUpPolicy.updated_at.desc())
                .first()
            )

        # Policy rule: max_reminders == 0 disables automatic reminders
        if policy and policy.max_reminders == 0:
            continue

        # 3. Query existing REMINDER notifications for this specific task
        existing_reminders = (
            db.query(Notification)
            .filter(
                Notification.follow_up_task_id == task.id,
                Notification.notification_type == "REMINDER",
            )
            .order_by(Notification.scheduled_at.asc())
            .all()
        )
        reminder_count = len(existing_reminders)

        # Cap by max_reminders if policy is defined
        max_reminders = policy.max_reminders if policy else 3
        if reminder_count >= max_reminders:
            continue

        # 4. Determine scheduled_at for the reminder
        if reminder_count == 0:
            # First reminder: scheduled_at = task.due_at
            scheduled_at = task.due_at
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        else:
            # Subsequent reminder: anchored to previous reminder + interval
            interval_days = policy.reminder_interval_days if policy else 2
            prev_reminder = existing_reminders[-1]
            anchor = prev_reminder.scheduled_at or prev_reminder.generated_at
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
            scheduled_at = anchor + timedelta(days=interval_days)

        # Is this reminder due yet?
        if now < scheduled_at:
            continue

        # 5. Resolve and validate recipient
        recipient_id = task.assigned_to_user_id or grievance.assigned_to_user_id
        if not recipient_id:
            failure_count += 1
            log_activity(
                db=db,
                action="REMINDER_PROCESSING_FAILED",
                entity_type="follow_up_task",
                entity_id=task.id,
                grievance_id=grievance.id,
                new_values={"reason": "Missing recipient user ID"},
                description=(
                    f"Reminder processing failed for task {task.id}: "
                    f"Missing recipient officer on grievance {grievance.grievance_number}."
                ),
            )
            continue

        recipient_user = db.get(User, recipient_id)
        if not recipient_user:
            failure_count += 1
            log_activity(
                db=db,
                action="REMINDER_PROCESSING_FAILED",
                entity_type="follow_up_task",
                entity_id=task.id,
                grievance_id=grievance.id,
                new_values={"reason": f"Recipient user ID {recipient_id} not found in database"},
                description=f"Reminder processing failed: User record {recipient_id} not found.",
            )
            continue

        if not recipient_user.is_active:
            failure_count += 1
            log_activity(
                db=db,
                action="REMINDER_PROCESSING_FAILED",
                entity_type="follow_up_task",
                entity_id=task.id,
                grievance_id=grievance.id,
                new_values={"reason": f"Assigned officer {recipient_user.full_name} is inactive"},
                description=(
                    f"Reminder not generated for grievance {grievance.grievance_number}: "
                    f"Assigned officer '{recipient_user.full_name}' ({recipient_user.email}) is inactive."
                ),
            )
            continue

        # 6. Build deterministic idempotency key
        channel = "IN_APP"
        notification_type = "REMINDER"
        scheduled_date = scheduled_at.date()
        idempotency_key = build_idempotency_key(
            grievance_id=grievance.id,
            channel=channel,
            notification_type=notification_type,
            scheduled_date=scheduled_date,
            follow_up_task_id=task.id,
        )

        # Fast-path duplicate check
        already_exists = (
            db.query(Notification.id)
            .filter(Notification.idempotency_key == idempotency_key)
            .first()
        )
        if already_exists:
            collision_count += 1
            continue

        # 7. Formulate subject and message
        subject = f"Follow-up Reminder: Grievance {grievance.grievance_number}"
        message = (
            f"Follow-up reminder #{reminder_count + 1} for grievance '{grievance.title}' "
            f"({grievance.grievance_number}). Follow-up was scheduled for {task.due_at.isoformat()}."
        )

        # Optional template override
        expected_template_type = "FIRST_REMINDER" if reminder_count == 0 else "OVERDUE_REMINDER"
        template = (
            db.query(MessageTemplate)
            .filter(
                MessageTemplate.template_type == expected_template_type,
                MessageTemplate.is_active.is_(True),
            )
            .first()
        )
        if template:
            try:
                subject = template.subject_template.format(
                    grievance_number=grievance.grievance_number,
                    title=grievance.title,
                )
                message = template.body_template.format(
                    grievance_number=grievance.grievance_number,
                    title=grievance.title,
                    due_at=task.due_at.isoformat(),
                )
            except Exception:
                pass  # Fall back to standard text on template format mismatch

        # 8. Atomic insert with savepoint to handle concurrent collisions safely
        savepoint = db.begin_nested()
        try:
            notification = Notification(
                grievance_id=grievance.id,
                follow_up_task_id=task.id,
                recipient_user_id=recipient_user.id,
                recipient_name=recipient_user.full_name,
                recipient_contact=recipient_user.email,
                notification_type=notification_type,
                channel=channel,
                subject=subject,
                message=message,
                status="GENERATED",
                scheduled_at=scheduled_at,
                generated_at=now,
                idempotency_key=idempotency_key,
            )
            db.add(notification)
            db.flush()
            savepoint.commit()
            generated_count += 1

            log_activity(
                db=db,
                action="NOTIFICATION_GENERATED",
                entity_type="notification",
                entity_id=notification.id,
                grievance_id=grievance.id,
                new_values={
                    "notification_type": notification_type,
                    "channel": channel,
                    "recipient_user_id": str(recipient_user.id),
                    "scheduled_at": scheduled_at.isoformat(),
                    "reminder_sequence": reminder_count + 1,
                    "idempotency_key": idempotency_key,
                },
                description=(
                    f"Reminder notification #{reminder_count + 1} generated for "
                    f"{recipient_user.full_name} ({recipient_user.email})."
                ),
            )
        except IntegrityError:
            savepoint.rollback()
            collision_count += 1

    db.commit()

    return {
        "processed_tasks": len(tasks),
        "generated_reminders": generated_count,
        "skipped_or_conflicts": collision_count,
        "failures": failure_count,
        "evaluated_at": now,
    }


def get_notification_by_id(
    db: Session,
    notification_id: uuid.UUID,
    current_user: User,
) -> Notification:
    """Retrieve a single notification enforcing department and recipient scoping."""
    notification = db.get(Notification, notification_id)
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found.",
        )

    grievance = db.get(Grievance, notification.grievance_id)
    if grievance:
        _check_notification_access(current_user, notification, grievance)

    return notification


def list_notifications(
    db: Session,
    current_user: User,
    grievance_id: Optional[uuid.UUID] = None,
    status_filter: Optional[str] = None,
    channel_filter: Optional[str] = None,
    recipient_user_id: Optional[uuid.UUID] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[Notification], int, int]:
    """
    List notifications with pagination and filters.
    Department and recipient scoping enforced.
    """
    query = select(Notification).join(Grievance, Notification.grievance_id == Grievance.id)

    # Scoping
    if current_user.role.name != RoleName.SUPER_ADMIN:
        dept_id = current_user.department_id
        if not dept_id:
            return [], 0, 1
        query = query.where(Grievance.confirmed_department_id == dept_id)

        # Officers and viewers can only see notifications directed to them
        if current_user.role.name not in (RoleName.DEPARTMENT_ADMIN, RoleName.SUPERVISOR):
            query = query.where(Notification.recipient_user_id == current_user.id)

    if grievance_id:
        query = query.where(Notification.grievance_id == grievance_id)

    if status_filter:
        query = query.where(Notification.status == status_filter.strip().upper())

    if channel_filter:
        query = query.where(Notification.channel == channel_filter.strip().upper())

    if recipient_user_id:
        query = query.where(Notification.recipient_user_id == recipient_user_id)

    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar_one()
    total_pages = math.ceil(total / page_size) if total > 0 else 1

    query = query.order_by(Notification.generated_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    items = list(db.execute(query).scalars().all())
    return items, total, total_pages


def acknowledge_notification(
    db: Session,
    notification_id: uuid.UUID,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Notification:
    """
    Acknowledge an IN_APP notification.
    Enforces authorization: recipient or department admin/supervisor.
    Validates state machine transition to ACKNOWLEDGED.
    Does NOT alter grievance status.
    """
    notification = (
        db.query(Notification)
        .with_for_update()
        .filter(Notification.id == notification_id)
        .first()
    )
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found.",
        )

    grievance = db.get(Grievance, notification.grievance_id)
    if grievance:
        _check_notification_access(current_user, notification, grievance)

    # Validate transition
    validate_notification_transition(notification.status, "ACKNOWLEDGED")

    old_status = notification.status
    notification.status = "ACKNOWLEDGED"

    log_activity(
        db=db,
        action="NOTIFICATION_ACKNOWLEDGED",
        entity_type="notification",
        entity_id=notification.id,
        user_id=current_user.id,
        grievance_id=notification.grievance_id,
        old_values={"status": old_status},
        new_values={"status": "ACKNOWLEDGED"},
        description=f"Notification {notification.id} acknowledged by {current_user.full_name}.",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(notification)
    return notification
