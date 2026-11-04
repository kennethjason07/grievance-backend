"""
System role constants and permission registry.

Business decisions (Stage 8A):
-------------------------------
1. GRIEVANCE NUMBER FORMAT: GRV-YYYY-NNNNN (5-digit zero-padded sequence per year).
   Example: GRV-2026-00001. Resets to 00001 each calendar year.

2. SEQUENTIAL NUMBERING: Per-year. A PostgreSQL advisory lock is used to prevent
   duplicate numbers under concurrent requests. See app/utils/grievance_number.py.

3. ROLES: Six fixed system roles. Roles are seeded at startup; they are not
   user-configurable. Role-level permissions are enforced at the service layer.

4. OFFICES / STATE FIELD: Defaults to "Karnataka" for the Police Department
   prototype. The column accepts any string — it is not an enum. When the system
   is extended to other states, existing offices are unaffected.

5. NOTIFICATION IDEMPOTENCY KEY FORMAT:
   {grievance_id}:{follow_up_task_id|"none"}:{channel}:{notification_type}:{scheduled_date_iso}
   This guarantees uniqueness per (grievance, task, channel, type, date) tuple while
   allowing the same notification type to repeat on different dates.
   See app/utils/notification_idempotency.py.
"""
from typing import Dict, FrozenSet

# ---------------------------------------------------------------------------
# Role name constants
# ---------------------------------------------------------------------------

class RoleName:
    SUPER_ADMIN = "SUPER_ADMIN"
    DEPARTMENT_ADMIN = "DEPARTMENT_ADMIN"
    SUPERVISOR = "SUPERVISOR"
    OFFICER = "OFFICER"
    DATA_ENTRY_OPERATOR = "DATA_ENTRY_OPERATOR"
    VIEWER = "VIEWER"

    ALL: FrozenSet[str] = frozenset({
        SUPER_ADMIN, DEPARTMENT_ADMIN, SUPERVISOR,
        OFFICER, DATA_ENTRY_OPERATOR, VIEWER,
    })


# ---------------------------------------------------------------------------
# Permission constants
# ---------------------------------------------------------------------------

class Permission:
    # Department management
    MANAGE_DEPARTMENTS = "manage_departments"
    VIEW_DEPARTMENTS = "view_departments"

    # User management
    MANAGE_USERS = "manage_users"
    VIEW_USERS = "view_users"

    # Grievance workflow
    CREATE_GRIEVANCE = "create_grievance"
    VIEW_GRIEVANCE = "view_grievance"
    EDIT_GRIEVANCE = "edit_grievance"
    ASSIGN_GRIEVANCE = "assign_grievance"
    RESOLVE_GRIEVANCE = "resolve_grievance"
    CLOSE_GRIEVANCE = "close_grievance"
    REOPEN_GRIEVANCE = "reopen_grievance"

    # Follow-up management
    MANAGE_FOLLOW_UPS = "manage_follow_ups"
    VIEW_FOLLOW_UPS = "view_follow_ups"
    LOG_FOLLOW_UP_ACTION = "log_follow_up_action"

    # Notifications
    MANAGE_NOTIFICATIONS = "manage_notifications"
    VIEW_NOTIFICATIONS = "view_notifications"

    # Escalations
    ESCALATE_GRIEVANCE = "escalate_grievance"
    VIEW_ESCALATIONS = "view_escalations"
    RESOLVE_ESCALATION = "resolve_escalation"

    # Policies and templates
    MANAGE_POLICIES = "manage_policies"
    MANAGE_TEMPLATES = "manage_templates"

    # Reports and audit
    VIEW_REPORTS = "view_reports"
    VIEW_AUDIT_LOGS = "view_audit_logs"


# ---------------------------------------------------------------------------
# Role → Permission mapping
# ---------------------------------------------------------------------------

ROLE_PERMISSIONS: Dict[str, FrozenSet[str]] = {
    RoleName.SUPER_ADMIN: frozenset({
        Permission.MANAGE_DEPARTMENTS, Permission.VIEW_DEPARTMENTS,
        Permission.MANAGE_USERS, Permission.VIEW_USERS,
        Permission.CREATE_GRIEVANCE, Permission.VIEW_GRIEVANCE,
        Permission.EDIT_GRIEVANCE, Permission.ASSIGN_GRIEVANCE,
        Permission.RESOLVE_GRIEVANCE, Permission.CLOSE_GRIEVANCE, Permission.REOPEN_GRIEVANCE,
        Permission.MANAGE_FOLLOW_UPS, Permission.VIEW_FOLLOW_UPS,
        Permission.LOG_FOLLOW_UP_ACTION,
        Permission.MANAGE_NOTIFICATIONS, Permission.VIEW_NOTIFICATIONS,
        Permission.ESCALATE_GRIEVANCE, Permission.VIEW_ESCALATIONS,
        Permission.RESOLVE_ESCALATION,
        Permission.MANAGE_POLICIES, Permission.MANAGE_TEMPLATES,
        Permission.VIEW_REPORTS, Permission.VIEW_AUDIT_LOGS,
    }),
    RoleName.DEPARTMENT_ADMIN: frozenset({
        Permission.VIEW_DEPARTMENTS,
        Permission.MANAGE_USERS, Permission.VIEW_USERS,
        Permission.CREATE_GRIEVANCE, Permission.VIEW_GRIEVANCE,
        Permission.EDIT_GRIEVANCE, Permission.ASSIGN_GRIEVANCE,
        Permission.RESOLVE_GRIEVANCE, Permission.CLOSE_GRIEVANCE, Permission.REOPEN_GRIEVANCE,
        Permission.MANAGE_FOLLOW_UPS, Permission.VIEW_FOLLOW_UPS,
        Permission.LOG_FOLLOW_UP_ACTION,
        Permission.MANAGE_NOTIFICATIONS, Permission.VIEW_NOTIFICATIONS,
        Permission.ESCALATE_GRIEVANCE, Permission.VIEW_ESCALATIONS,
        Permission.RESOLVE_ESCALATION,
        Permission.MANAGE_POLICIES, Permission.MANAGE_TEMPLATES,
        Permission.VIEW_REPORTS, Permission.VIEW_AUDIT_LOGS,
    }),
    RoleName.SUPERVISOR: frozenset({
        Permission.VIEW_DEPARTMENTS,
        Permission.VIEW_USERS,
        Permission.CREATE_GRIEVANCE, Permission.VIEW_GRIEVANCE,
        Permission.EDIT_GRIEVANCE, Permission.ASSIGN_GRIEVANCE,
        Permission.RESOLVE_GRIEVANCE,
        Permission.MANAGE_FOLLOW_UPS, Permission.VIEW_FOLLOW_UPS,
        Permission.LOG_FOLLOW_UP_ACTION,
        Permission.VIEW_NOTIFICATIONS,
        Permission.ESCALATE_GRIEVANCE, Permission.VIEW_ESCALATIONS,
        Permission.RESOLVE_ESCALATION,
        Permission.VIEW_REPORTS,
    }),
    RoleName.OFFICER: frozenset({
        Permission.VIEW_GRIEVANCE,
        Permission.EDIT_GRIEVANCE,
        Permission.RESOLVE_GRIEVANCE,
        Permission.VIEW_FOLLOW_UPS, Permission.LOG_FOLLOW_UP_ACTION,
        Permission.VIEW_NOTIFICATIONS,
        Permission.VIEW_ESCALATIONS,
    }),
    RoleName.DATA_ENTRY_OPERATOR: frozenset({
        Permission.CREATE_GRIEVANCE, Permission.VIEW_GRIEVANCE,
        Permission.EDIT_GRIEVANCE,
        Permission.VIEW_FOLLOW_UPS,
    }),
    RoleName.VIEWER: frozenset({
        Permission.VIEW_GRIEVANCE,
        Permission.VIEW_FOLLOW_UPS,
        Permission.VIEW_NOTIFICATIONS,
        Permission.VIEW_ESCALATIONS,
        Permission.VIEW_DEPARTMENTS,
    }),
}


def has_permission(role_name: str, permission: str) -> bool:
    """Return True if the given role has the specified permission."""
    return permission in ROLE_PERMISSIONS.get(role_name, frozenset())


# ---------------------------------------------------------------------------
# Initial seed data specifications
# ---------------------------------------------------------------------------

ROLE_SEED_DATA = [
    {
        "name": RoleName.SUPER_ADMIN,
        "description": "Full system access. Manages all departments, users, and configurations.",
    },
    {
        "name": RoleName.DEPARTMENT_ADMIN,
        "description": "Department-scoped administrator. Manages department users, policies, and grievances.",
    },
    {
        "name": RoleName.SUPERVISOR,
        "description": "Supervises officers and grievance workflows within a department. Can escalate and resolve.",
    },
    {
        "name": RoleName.OFFICER,
        "description": "Handles assigned grievances. Records follow-up actions and updates grievance status.",
    },
    {
        "name": RoleName.DATA_ENTRY_OPERATOR,
        "description": "Registers grievances received in person or by post. Limited editing access.",
    },
    {
        "name": RoleName.VIEWER,
        "description": "Read-only access for monitoring, audit, and reporting purposes.",
    },
]

