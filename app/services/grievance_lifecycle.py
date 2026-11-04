"""
Centralized State Machine for Grievance Lifecycle Transitions (Stage 9E).
Enforces:
- Explicit transition matrix
- Terminal state protection (CLOSED is strictly terminal)
- Cross-department isolation
- Officer active assignee authorization
- Supervisory and administrative closure permissions
- Administrative-only grievance reopening (RESOLVED → IN_PROGRESS)
"""
from typing import Dict, Set
from fastapi import HTTPException, status

from app.core.roles import Permission, RoleName, has_permission
from app.models.grievance import Grievance
from app.models.user import User

# Primary forward transitions (officer-driven)
ALLOWED_LIFECYCLE_TRANSITIONS: Dict[str, Set[str]] = {
    "NEW": {"UNDER_REVIEW"},
    "UNDER_REVIEW": {"ASSIGNED"},
    "ASSIGNED": {"IN_PROGRESS"},
    "IN_PROGRESS": {"AWAITING_INFORMATION", "RESOLVED"},
    "AWAITING_INFORMATION": {"IN_PROGRESS"},
    "RESOLVED": {"CLOSED"},   # Reopen (RESOLVED→IN_PROGRESS) handled separately via reopen endpoint
    "CLOSED": set(),          # Terminal state: no transitions permitted
}

TERMINAL_STATES: Set[str] = {"CLOSED"}

# Roles explicitly permitted to reopen a resolved grievance
REOPEN_AUTHORIZED_ROLES: Set[str] = {RoleName.SUPER_ADMIN, RoleName.DEPARTMENT_ADMIN}


def validate_lifecycle_transition(
    grievance: Grievance,
    target_status: str,
    current_user: User,
) -> None:
    """
    Validate that transitioning `grievance` from its current status to `target_status`
    is legally permitted by the state machine, department scope, and user role.
    """
    current_status = grievance.status.strip().upper()
    target_status = target_status.strip().upper()

    # 1. Terminal state check
    if current_status in TERMINAL_STATES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Grievance is in a terminal state ('{current_status}') and cannot undergo further status transitions.",
        )

    # 2. State machine transition check
    allowed_next_states = ALLOWED_LIFECYCLE_TRANSITIONS.get(current_status, set())
    if target_status not in allowed_next_states:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid transition: Cannot move grievance from '{current_status}' to '{target_status}'.",
        )

    # 3. Department isolation
    if current_user.role.name != RoleName.SUPER_ADMIN:
        dept_id = grievance.confirmed_department_id or grievance.suggested_department_id
        if not dept_id or current_user.department_id != dept_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Cannot modify grievance belonging to another department.",
            )

    # 4. Officer assignee check
    if current_user.role.name == RoleName.OFFICER:
        if grievance.assigned_to_user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: You are not the actively assigned officer for this grievance.",
            )
        # Officers cannot close grievances
        if target_status == "CLOSED":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied: Officers cannot close grievances. Closure requires administrative authority.",
            )

    # 5. Closure permission check
    if target_status == "CLOSED":
        if not has_permission(current_user.role.name, Permission.CLOSE_GRIEVANCE):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: '{Permission.CLOSE_GRIEVANCE}' required to close a grievance.",
            )


def validate_reopen_transition(
    grievance: Grievance,
    current_user: User,
) -> None:
    """
    Validate a grievance reopen request (RESOLVED → IN_PROGRESS).
    Reopen is an administrative action explicitly separate from the normal forward transition matrix.
    Permitted only for SUPER_ADMIN and DEPARTMENT_ADMIN within the confirmed department.
    """
    # 1. Must be in RESOLVED status to reopen
    if grievance.status != "RESOLVED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only RESOLVED grievances can be reopened. Current status: '{grievance.status}'.",
        )

    # 2. REOPEN_GRIEVANCE permission
    if not has_permission(current_user.role.name, Permission.REOPEN_GRIEVANCE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: '{Permission.REOPEN_GRIEVANCE}' required to reopen a grievance.",
        )

    # 3. Department isolation
    if current_user.role.name != RoleName.SUPER_ADMIN:
        dept_id = grievance.confirmed_department_id or grievance.suggested_department_id
        if not dept_id or current_user.department_id != dept_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Cannot reopen a grievance belonging to another department.",
            )

