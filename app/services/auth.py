"""
Authentication service: login, token refresh, current-user resolution.

Design decisions:
- Login accepts email + password, returns access + refresh tokens.
- Tokens are JWTs (HS256). The SECRET_KEY must be rotated in production.
- Inactive or unverified users are rejected at login.
- Token validation is performed via a FastAPI dependency (get_current_user).
- Role enforcement uses a dependency factory (require_permission).
- Password reset and MFA are out of scope for this stage.
- last_login_at is updated in the same transaction as login.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from jose import JWTError
from sqlalchemy.orm import Session

from app.core.roles import ROLE_PERMISSIONS, has_permission
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    verify_password,
)
from app.models.user import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email.lower().strip()).first()


def _get_user_by_id(db: Session, user_id: uuid.UUID) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def _build_token_pair(user: User) -> dict:
    """Build the standard access + refresh token response payload."""
    access_token = create_access_token(
        user_id=str(user.id),
        role_name=user.role.name,
    )
    refresh_token = create_refresh_token(user_id=str(user.id))
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

class AuthenticationError(Exception):
    """Raised when login credentials are invalid."""


class AccountInactiveError(Exception):
    """Raised when a user account is inactive."""


class AccountUnverifiedError(Exception):
    """Raised when a user account has not been verified."""


def login(db: Session, email: str, password: str) -> dict:
    """
    Authenticate a user by email and password.

    Returns token pair dict on success.
    Raises AuthenticationError, AccountInactiveError, or AccountUnverifiedError.
    Credentials are never logged.
    """
    # Always attempt password verification even on unknown email to prevent timing attacks.
    user = _get_user_by_email(db, email)
    # Constant-time dummy check if no user found
    _dummy_hash = "$2b$12$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    password_match = verify_password(password, user.password_hash if user else _dummy_hash)

    if not user or not password_match:
        logger.warning("Failed login attempt for email (redacted).")
        raise AuthenticationError("Invalid email or password.")

    if not user.is_active:
        raise AccountInactiveError("This account has been deactivated.")

    if not user.is_verified:
        raise AccountUnverifiedError("This account has not been verified.")

    # Eager-load role before we build the token
    _ = user.role.name  # triggers lazy load while session is open

    # Update last_login_at
    user.last_login_at = datetime.now(tz=timezone.utc)
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info("Successful login for user_id=%s", user.id)
    return _build_token_pair(user)


def refresh_access_token(db: Session, refresh_token_str: str) -> dict:
    """
    Issue a new access token from a valid refresh token.
    Does not invalidate the old refresh token (stateless design).
    Token blacklisting is deferred to when Redis is introduced.
    """
    try:
        payload = decode_refresh_token(refresh_token_str)
    except JWTError as exc:
        raise AuthenticationError(f"Invalid or expired refresh token: {exc}") from exc

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise AuthenticationError("Malformed refresh token.")

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise AuthenticationError("Malformed refresh token.")

    user = _get_user_by_id(db, user_id)
    if not user or not user.is_active:
        raise AuthenticationError("User not found or inactive.")

    # Eager-load role
    _ = user.role.name

    access_token = create_access_token(user_id=str(user.id), role_name=user.role.name)
    return {
        "access_token": access_token,
        "token_type": "bearer",
    }


def get_user_from_access_token(db: Session, access_token_str: str) -> User:
    """
    Resolve a User from an access token string.
    Raises AuthenticationError on any failure.
    """
    try:
        payload = decode_access_token(access_token_str)
    except JWTError as exc:
        raise AuthenticationError("Invalid or expired access token.") from exc

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise AuthenticationError("Malformed access token.")

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise AuthenticationError("Malformed access token.")

    user = _get_user_by_id(db, user_id)
    if not user:
        raise AuthenticationError("User not found.")
    if not user.is_active:
        raise AccountInactiveError("This account has been deactivated.")

    return user

