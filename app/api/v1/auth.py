import logging
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.auth import (
    AccessTokenResponse,
    LoginRequest,
    RefreshTokenRequest,
    TokenResponse,
    UserSummaryResponse,
)
from app.services.auth import (
    AccountInactiveError,
    AccountUnverifiedError,
    AuthenticationError,
    login as auth_login,
    refresh_access_token,
)

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["Authentication"])


@auth_router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate user and issue JWT tokens",
)
def login_endpoint(
    credentials: LoginRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Authenticate with email and password to receive access and refresh tokens.
    """
    try:
        return auth_login(db, credentials.email, credentials.password)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
    except AccountInactiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except AccountUnverifiedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )


@auth_router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    summary="Renew access token using refresh token",
)
def refresh_token_endpoint(
    payload: RefreshTokenRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Obtain a new access token by presenting a valid refresh token.
    """
    try:
        return refresh_access_token(db, payload.refresh_token)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )


@auth_router.get(
    "/me",
    response_model=UserSummaryResponse,
    summary="Get current authenticated user profile",
)
def get_current_user_profile(
    current_user: User = Depends(get_current_user),
) -> UserSummaryResponse:
    """
    Return basic profile details for the authenticated user.
    """
    return UserSummaryResponse(
        id=current_user.id,
        full_name=current_user.full_name,
        email=current_user.email,
        role=current_user.role.name,
        department_id=current_user.department_id,
        office_id=current_user.office_id,
        is_active=current_user.is_active,
        is_verified=current_user.is_verified,
    )


@auth_router.post(
    "/logout",
    summary="User logout",
)
def logout_endpoint(
    current_user: User = Depends(get_current_user),
) -> Dict[str, str]:
    """
    Acknowledge user logout. In stateless JWT architecture, the client discards stored tokens.
    """
    return {"message": "Successfully logged out."}

