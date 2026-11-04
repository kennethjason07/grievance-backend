import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=100)
    phone: Optional[str] = Field(None, max_length=50)
    department_id: Optional[uuid.UUID] = None
    office_id: Optional[uuid.UUID] = None
    role_id: uuid.UUID
    is_verified: bool = True


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    office_id: Optional[uuid.UUID] = None


class UserStatusUpdate(BaseModel):
    is_active: bool = Field(..., description="Whether the user account is active or deactivated")


class UserRoleUpdate(BaseModel):
    role_id: uuid.UUID = Field(..., description="Target role UUID to assign to the user")


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: str
    phone: Optional[str] = None
    department_id: Optional[uuid.UUID] = None
    office_id: Optional[uuid.UUID] = None
    role_id: uuid.UUID
    role_name: str
    is_active: bool
    is_verified: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

