"""Pydantic schemas for data validation and serialization."""
from app.schemas.auth import (
    AccessTokenResponse,
    LoginRequest,
    RefreshTokenRequest,
    TokenResponse,
    UserSummaryResponse,
)
from app.schemas.department import (
    DepartmentCreate,
    DepartmentResponse,
    DepartmentStatusUpdate,
    DepartmentUpdate,
)
from app.schemas.office import (
    OfficeCreate,
    OfficeResponse,
    OfficeStatusUpdate,
    OfficeUpdate,
)
from app.schemas.user import (
    UserCreate,
    UserResponse,
    UserRoleUpdate,
    UserStatusUpdate,
    UserUpdate,
)
from app.schemas.grievance import (
    GrievanceCreate,
    GrievanceUpdate,
    GrievanceConfirmDepartment,
    GrievanceListResponse,
    GrievanceDetailResponse,
    PaginatedGrievanceResponse,
)
from app.schemas.assignment import (
    AssignmentCreate,
    AssignmentReassign,
    AssignmentResponse,
    AssignmentHistoryResponse,
    OfficerWorkloadResponse,
)
from app.schemas.lifecycle import (
    ClosureCreate,
    InformationRequestCreate,
    ReopenCreate,
    ResolutionCreate,
    ResumeInvestigationCreate,
    TimelineEvent,
    TimelineResponse,
)
from app.schemas.action import (
    ActionTypeEnum,
    FollowUpActionCreate,
    FollowUpActionResponse,
    PaginatedActionResponse,
)
from app.schemas.escalation import (
    EscalationDismissRequest,
    EscalationResolveRequest,
    EscalationResponse,
    PaginatedEscalationResponse,
    ProcessEscalationsRequest,
    ProcessEscalationsResponse,
)
from app.schemas.notification import (
    NotificationResponse,
    PaginatedNotificationResponse,
    ProcessRemindersRequest,
    ProcessRemindersResponse,
)
from app.schemas.policy import (
    FollowUpPolicyCreate,
    FollowUpPolicyResponse,
    FollowUpPolicyStatusUpdate,
    FollowUpPolicyUpdate,
    PaginatedPolicyResponse,
)
from app.schemas.task import (
    FollowUpTaskResponse,
    ManualScheduleFollowUpTaskCreate,
    PaginatedTaskResponse,
)

__all__ = [
    "LoginRequest",
    "TokenResponse",
    "RefreshTokenRequest",
    "AccessTokenResponse",
    "UserSummaryResponse",
    "DepartmentCreate",
    "DepartmentUpdate",
    "DepartmentStatusUpdate",
    "DepartmentResponse",
    "OfficeCreate",
    "OfficeUpdate",
    "OfficeStatusUpdate",
    "OfficeResponse",
    "UserCreate",
    "UserUpdate",
    "UserStatusUpdate",
    "UserRoleUpdate",
    "UserResponse",
    "GrievanceCreate",
    "GrievanceUpdate",
    "GrievanceConfirmDepartment",
    "GrievanceListResponse",
    "GrievanceDetailResponse",
    "PaginatedGrievanceResponse",
    "AssignmentCreate",
    "AssignmentReassign",
    "AssignmentResponse",
    "AssignmentHistoryResponse",
    "OfficerWorkloadResponse",
    "InformationRequestCreate",
    "ResumeInvestigationCreate",
    "ReopenCreate",
    "ResolutionCreate",
    "ClosureCreate",
    "TimelineEvent",
    "TimelineResponse",
    "FollowUpPolicyCreate",
    "FollowUpPolicyUpdate",
    "FollowUpPolicyStatusUpdate",
    "FollowUpPolicyResponse",
    "PaginatedPolicyResponse",
    "FollowUpTaskResponse",
    "ManualScheduleFollowUpTaskCreate",
    "PaginatedTaskResponse",
    "ActionTypeEnum",
    "FollowUpActionCreate",
    "FollowUpActionResponse",
    "PaginatedActionResponse",
    "NotificationResponse",
    "PaginatedNotificationResponse",
    "ProcessRemindersRequest",
    "ProcessRemindersResponse",
    "EscalationResponse",
    "EscalationResolveRequest",
    "EscalationDismissRequest",
    "PaginatedEscalationResponse",
    "ProcessEscalationsRequest",
    "ProcessEscalationsResponse",
]
