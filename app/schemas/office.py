import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class OfficeCreate(BaseModel):
    department_id: uuid.UUID
    name: str = Field(..., min_length=2, max_length=255)
    office_code: str = Field(..., min_length=2, max_length=50)
    address: Optional[str] = Field(None, max_length=1000)
    district: str = Field(..., min_length=2, max_length=100)
    state: str = Field("Karnataka", min_length=2, max_length=100)


class OfficeUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    address: Optional[str] = Field(None, max_length=1000)
    district: Optional[str] = Field(None, min_length=2, max_length=100)
    state: Optional[str] = Field(None, min_length=2, max_length=100)


class OfficeStatusUpdate(BaseModel):
    is_active: bool = Field(..., description="Whether the office is active or deactivated")


class OfficeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    department_id: uuid.UUID
    name: str
    office_code: str
    address: Optional[str] = None
    district: str
    state: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

