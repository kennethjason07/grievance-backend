"""
Pydantic schemas for Grievance Assignment and Officer Workload (Stage 9D).
"""
import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class AssignmentCreate(BaseModel):
    officer_id: uuid.UUID = Field(..., description="UUID of the officer to assign the grievance to")
    remarks: Optional[str] = Field(default=None, max_length=1000, description="Initial assignment notes or instructions")


class AssignmentReassign(BaseModel):
    officer_id: uuid.UUID = Field(..., description="UUID of the new officer to reassign the grievance to")
    reassignment_reason: str = Field(..., min_length=3, max_length=1000, description="Reason for reassigning the grievance")


class AssignmentResponse(BaseModel):
    id: uuid.UUID
    grievance_id: uuid.UUID
    assigned_to_user_id: uuid.UUID
    assigned_by_user_id: uuid.UUID
    assigned_at: datetime
    unassigned_at: Optional[datetime] = None
    remarks: Optional[str] = None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class AssignmentHistoryResponse(BaseModel):
    grievance_id: uuid.UUID
    total_assignments: int
    history: List[AssignmentResponse]


class OfficerWorkloadResponse(BaseModel):
    officer_id: uuid.UUID
    officer_name: str
    officer_email: str
    department_id: Optional[uuid.UUID] = None
    assigned_count: int = Field(0, description="Number of grievances in ASSIGNED status")
    in_progress_count: int = Field(0, description="Number of grievances in IN_PROGRESS status")
    awaiting_information_count: int = Field(0, description="Number of grievances in AWAITING_INFORMATION status")
    resolved_count: int = Field(0, description="Number of grievances in RESOLVED status")
    total_active_workload: int = Field(0, description="Total unresolved assigned grievances")

