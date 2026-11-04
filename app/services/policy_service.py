"""
Follow-up Policy Service (Stage 10A).
Provides CRUD, department isolation, constraint validation, and audit logging for
department-level reminder intervals, max reminders, and escalation thresholds.
"""
import math
import uuid
from typing import Any, Dict, List, Optional, Tuple
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.roles import RoleName
from app.models.department import Department
from app.models.follow_up_policy import FollowUpPolicy
from app.models.user import User
from app.schemas.policy import (
    FollowUpPolicyCreate,
    FollowUpPolicyStatusUpdate,
    FollowUpPolicyUpdate,
)
from app.services.audit import log_activity


def _check_policy_department_access(current_user: User, department_id: uuid.UUID) -> None:
    """
    Enforce department isolation:
    SUPER_ADMIN has universal access.
    DEPARTMENT_ADMIN can only manage policies for their assigned department.
    """
    if current_user.role.name == RoleName.SUPER_ADMIN:
        return

    if not current_user.department_id or current_user.department_id != department_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Cannot configure policies for another department.",
        )


def create_policy(
    db: Session,
    policy_in: FollowUpPolicyCreate,
    current_user: User,
    ip_address: Optional[str] = None,
) -> FollowUpPolicy:
    """
    Create a new follow-up policy for a department.
    """
    dept = db.get(Department, policy_in.department_id)
    if not dept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found.",
        )
    if not dept.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create policy for an inactive department.",
        )

    _check_policy_department_access(current_user, policy_in.department_id)

    # Validate intervals
    if policy_in.escalation_after_days < policy_in.first_reminder_after_days:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="escalation_after_days must be greater than or equal to first_reminder_after_days.",
        )

    policy = FollowUpPolicy(
        department_id=policy_in.department_id,
        name=policy_in.name.strip(),
        description=policy_in.description.strip() if policy_in.description else None,
        first_reminder_after_days=policy_in.first_reminder_after_days,
        reminder_interval_days=policy_in.reminder_interval_days,
        escalation_after_days=policy_in.escalation_after_days,
        max_reminders=policy_in.max_reminders,
        is_active=policy_in.is_active,
    )
    db.add(policy)
    db.flush()

    log_activity(
        db=db,
        action="POLICY_CREATED",
        entity_type="follow_up_policy",
        entity_id=policy.id,
        user_id=current_user.id,
        new_values={
            "department_id": str(policy.department_id),
            "name": policy.name,
            "first_reminder_after_days": policy.first_reminder_after_days,
            "reminder_interval_days": policy.reminder_interval_days,
            "escalation_after_days": policy.escalation_after_days,
            "max_reminders": policy.max_reminders,
            "is_active": policy.is_active,
        },
        description=f"Follow-up policy '{policy.name}' created for department {dept.name} ({dept.code}).",
        ip_address=ip_address,
    )

    db.commit()
    db.refresh(policy)
    return policy


def get_policy_by_id(
    db: Session,
    policy_id: uuid.UUID,
    current_user: User,
) -> FollowUpPolicy:
    """
    Retrieve a policy by ID, enforcing department isolation.
    """
    policy = db.get(FollowUpPolicy, policy_id)
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Follow-up policy not found.",
        )

    _check_policy_department_access(current_user, policy.department_id)
    return policy


def get_active_department_policy(
    db: Session,
    department_id: uuid.UUID,
    current_user: User,
) -> FollowUpPolicy:
    """
    Retrieve the active follow-up policy for a department.
    """
    dept = db.get(Department, department_id)
    if not dept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found.",
        )

    _check_policy_department_access(current_user, department_id)

    policy = (
        db.query(FollowUpPolicy)
        .filter(FollowUpPolicy.department_id == department_id, FollowUpPolicy.is_active.is_(True))
        .order_by(FollowUpPolicy.updated_at.desc())
        .first()
    )
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active follow-up policy found for department '{dept.name}'.",
        )
    return policy


def list_policies(
    db: Session,
    current_user: User,
    department_id: Optional[uuid.UUID] = None,
    is_active: Optional[bool] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[FollowUpPolicy], int, int]:
    """
    List policies with department scoping, filtering, and pagination.
    """
    query = select(FollowUpPolicy)

    # Department isolation
    if current_user.role.name != RoleName.SUPER_ADMIN:
        user_dept = current_user.department_id
        if not user_dept:
            return [], 0, 1
        if department_id and department_id != user_dept:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Cannot query policies of another department.",
            )
        query = query.where(FollowUpPolicy.department_id == user_dept)
    else:
        if department_id:
            query = query.where(FollowUpPolicy.department_id == department_id)

    if is_active is not None:
        query = query.where(FollowUpPolicy.is_active.is_(is_active))

    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar_one()
    total_pages = math.ceil(total / page_size) if total > 0 else 1

    query = query.order_by(FollowUpPolicy.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    items = list(db.execute(query).scalars().all())
    return items, total, total_pages


def update_policy(
    db: Session,
    policy_id: uuid.UUID,
    policy_in: FollowUpPolicyUpdate,
    current_user: User,
    ip_address: Optional[str] = None,
) -> FollowUpPolicy:
    """
    Update an existing follow-up policy.
    Rule 6: Modifying a policy does NOT silently alter existing scheduled task due_at values.
    """
    policy = db.get(FollowUpPolicy, policy_id)
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Follow-up policy not found.",
        )

    _check_policy_department_access(current_user, policy.department_id)

    # Validate combined intervals
    first_reminder = (
        policy_in.first_reminder_after_days
        if policy_in.first_reminder_after_days is not None
        else policy.first_reminder_after_days
    )
    escalation = (
        policy_in.escalation_after_days
        if policy_in.escalation_after_days is not None
        else policy.escalation_after_days
    )
    if escalation < first_reminder:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"escalation_after_days ({escalation}) must be >= first_reminder_after_days ({first_reminder}).",
        )

    old_values: Dict[str, Any] = {}
    new_values: Dict[str, Any] = {}

    update_data = policy_in.model_dump(exclude_unset=True)
    for field_name, new_val in update_data.items():
        old_val = getattr(policy, field_name)
        if old_val != new_val:
            old_values[field_name] = old_val
            new_values[field_name] = new_val
            setattr(policy, field_name, new_val)

    if new_values:
        db.flush()
        log_activity(
            db=db,
            action="POLICY_UPDATED",
            entity_type="follow_up_policy",
            entity_id=policy.id,
            user_id=current_user.id,
            old_values=old_values,
            new_values=new_values,
            description=f"Follow-up policy '{policy.name}' updated by {current_user.full_name}.",
            ip_address=ip_address,
        )
        db.commit()
        db.refresh(policy)

    return policy


def set_policy_status(
    db: Session,
    policy_id: uuid.UUID,
    status_in: FollowUpPolicyStatusUpdate,
    current_user: User,
    ip_address: Optional[str] = None,
) -> FollowUpPolicy:
    """
    Activate or deactivate a policy.
    """
    policy = db.get(FollowUpPolicy, policy_id)
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Follow-up policy not found.",
        )

    _check_policy_department_access(current_user, policy.department_id)

    if policy.is_active != status_in.is_active:
        old_status = policy.is_active
        policy.is_active = status_in.is_active
        action_name = "POLICY_ACTIVATED" if status_in.is_active else "POLICY_DEACTIVATED"

        db.flush()
        log_activity(
            db=db,
            action=action_name,
            entity_type="follow_up_policy",
            entity_id=policy.id,
            user_id=current_user.id,
            old_values={"is_active": old_status},
            new_values={"is_active": policy.is_active},
            description=f"Follow-up policy '{policy.name}' {'activated' if policy.is_active else 'deactivated'} by {current_user.full_name}.",
            ip_address=ip_address,
        )
        db.commit()
        db.refresh(policy)

    return policy

