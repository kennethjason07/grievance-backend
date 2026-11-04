"""
Activity Log and Audit Service.
Captures administrative and domain actions into the immutable activity_logs table.
"""
import logging
import uuid
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog

logger = logging.getLogger(__name__)


def log_activity(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    user_id: Optional[uuid.UUID] = None,
    grievance_id: Optional[uuid.UUID] = None,
    old_values: Optional[Dict[str, Any]] = None,
    new_values: Optional[Dict[str, Any]] = None,
    description: str = "",
    ip_address: Optional[str] = None,
) -> ActivityLog:
    """
    Append an audit log record into activity_logs within the current database transaction.
    """
    log_entry = ActivityLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        grievance_id=grievance_id,
        old_values=old_values,
        new_values=new_values,
        description=description,
        ip_address=ip_address,
    )
    db.add(log_entry)
    # Flushed as part of transaction; committed by caller
    db.flush()
    logger.info(
        "Activity logged: action=%s entity_type=%s entity_id=%s user_id=%s",
        action,
        entity_type,
        entity_id,
        user_id,
    )
    return log_entry

