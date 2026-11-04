"""
Notification idempotency key construction.

Business convention (Stage 8A):
  Format: {grievance_id}:{task_id_or_none}:{channel}:{notification_type}:{scheduled_date}

  Components:
    grievance_id      – UUID of the grievance (str, no dashes).
    task_id_or_none   – UUID of the linked follow_up_task (str, no dashes) or "none".
    channel           – Delivery channel: EMAIL, SMS, WHATSAPP, IN_APP, POSTAL.
    notification_type – Type: REMINDER, ESCALATION, STATUS_UPDATE, ACKNOWLEDGMENT.
    scheduled_date    – ISO-8601 date string in UTC (YYYY-MM-DD), not datetime,
                        to allow one notification per channel per day.

  Examples:
    "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4:none:SMS:REMINDER:2026-11-04"
    "a1b2....:f7e8...:EMAIL:ESCALATION:2026-11-05"

  Guarantees:
    - Same (grievance, task, channel, type, day) → identical key → DB rejects duplicates.
    - Different day → different key → distinct notification is allowed.
    - No task → "none" segment prevents collisions with task-linked notifications.

  Collision handling:
    Callers must catch IntegrityError from the DB unique constraint and treat it
    as a "notification already scheduled for today" condition, not a server error.
"""
import uuid
from datetime import date, datetime, timezone
from typing import Optional


def build_idempotency_key(
    grievance_id: uuid.UUID,
    channel: str,
    notification_type: str,
    scheduled_date: Optional[date] = None,
    follow_up_task_id: Optional[uuid.UUID] = None,
) -> str:
    """
    Build a deterministic idempotency key for a notification.

    Parameters:
        grievance_id       – Grievance UUID.
        channel            – Delivery channel (must match DB check constraint).
        notification_type  – Notification type (must match DB check constraint).
        scheduled_date     – The UTC date the notification is scheduled for.
                             Defaults to today (UTC) if not provided.
        follow_up_task_id  – Optional follow-up task UUID.

    Returns:
        A string of at most 255 characters suitable for the idempotency_key column.
    """
    if scheduled_date is None:
        scheduled_date = datetime.now(tz=timezone.utc).date()

    grievance_hex = grievance_id.hex
    task_hex = follow_up_task_id.hex if follow_up_task_id else "none"
    date_str = scheduled_date.isoformat()

    key = f"{grievance_hex}:{task_hex}:{channel}:{notification_type}:{date_str}"

    # Defensive length check — the DB column is VARCHAR(255).
    if len(key) > 255:
        raise ValueError(f"Idempotency key exceeds 255 characters: {len(key)}")

    return key

