import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class DepartmentCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255, description="Full department name")
    code: str = Field(..., min_length=2, max_length=50, description="Unique department code, e.g., POLICE")
    description: Optional[str] = Field(None, max_length=1000)


class DepartmentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)


class DepartmentStatusUpdate(BaseModel):
    is_active: bool = Field(..., description="Whether the department is active or deactivated")


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str
    description: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

