"""
Pydantic schemas for Follow-up Policy Management (Stage 10A).
"""
import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class FollowUpPolicyCreate(BaseModel):
    department_id: uuid.UUID = Field(..., description="Target department UUID")
    name: str = Field(..., min_length=3, max_length=255, description="Descriptive policy name")
    description: Optional[str] = Field(default=None, max_length=1000, description="Policy operational notes")

    first_reminder_after_days: int = Field(
        default=3,
        ge=0,
        description="Days after initial assignment clock anchor before first reminder",
    )
    reminder_interval_days: int = Field(
        default=2,
        gt=0,
        description="Cadence interval in days between subsequent reminders",
    )
    escalation_after_days: int = Field(
        default=7,
        ge=0,
        description="Days without resolution after which supervisor escalation triggers",
    )
    max_reminders: int = Field(
        default=3,
        ge=0,
        description="Maximum reminders in cycle. 0 disables automatic reminders; positive integer sets limit.",
    )
    is_active: bool = Field(default=True, description="Whether this policy is active")

    @model_validator(mode="after")
    def validate_escalation_after_reminder(self) -> "FollowUpPolicyCreate":
        if self.escalation_after_days < self.first_reminder_after_days:
            raise ValueError(
                f"escalation_after_days ({self.escalation_after_days}) must be greater than or equal to "
                f"first_reminder_after_days ({self.first_reminder_after_days})"
            )
        return self


class FollowUpPolicyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1000)
    first_reminder_after_days: Optional[int] = Field(default=None, ge=0)
    reminder_interval_days: Optional[int] = Field(default=None, gt=0)
    escalation_after_days: Optional[int] = Field(default=None, ge=0)
    max_reminders: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_intervals(self) -> "FollowUpPolicyUpdate":
        if self.first_reminder_after_days is not None and self.escalation_after_days is not None:
            if self.escalation_after_days < self.first_reminder_after_days:
                raise ValueError(
                    f"escalation_after_days ({self.escalation_after_days}) must be greater than or equal to "
                    f"first_reminder_after_days ({self.first_reminder_after_days})"
                )
        return self


class FollowUpPolicyStatusUpdate(BaseModel):
    is_active: bool = Field(..., description="Activation state for this policy")

    model_config = ConfigDict(extra="forbid")


class FollowUpPolicyResponse(BaseModel):
    id: uuid.UUID
    department_id: uuid.UUID
    name: str
    description: Optional[str] = None
    first_reminder_after_days: int
    reminder_interval_days: int
    escalation_after_days: int
    max_reminders: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaginatedPolicyResponse(BaseModel):
    items: List[FollowUpPolicyResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

