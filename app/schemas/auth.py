import uuid
from typing import Optional
from pydantic import BaseModel, ConfigDict, EmailStr


class LoginRequest(BaseModel):
    """Credentials required for user authentication."""
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Full JWT token pair response on successful login."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    """Request payload for exchanging a refresh token for a new access token."""
    refresh_token: str


class AccessTokenResponse(BaseModel):
    """Response containing a renewed access token."""
    access_token: str
    token_type: str = "bearer"


class UserSummaryResponse(BaseModel):
    """Public user profile information safe to expose in API responses."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: str
    role: str
    department_id: Optional[uuid.UUID] = None
    office_id: Optional[uuid.UUID] = None
    is_active: bool
    is_verified: bool

