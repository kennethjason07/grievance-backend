"""
Password hashing and JWT token utilities.

Design decisions:
- bcrypt is used for password hashing (work factor 12) — industry standard, resistant to GPU attacks.
- python-jose provides JWT encoding/decoding (HS256 symmetric algorithm using the app SECRET_KEY).
- Access tokens: short-lived (default 60 min), carry user identity and role.
- Refresh tokens: longer-lived (default 7 days), used only to issue new access tokens.
- Tokens carry: sub (user UUID), role (role name), type (access|refresh), exp.
- Credentials are NEVER logged or included in exception messages.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from jose import JWTError, jwt
import bcrypt

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Password utilities
# ---------------------------------------------------------------------------

_BCRYPT_ROUNDS = 12


def hash_password(plain_password: str) -> str:
    """
    Hash a plaintext password using bcrypt.
    Returns the hashed string to be stored in password_hash column.
    """
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """
    Verify a plaintext password against a stored bcrypt hash.
    Returns True if matching, False otherwise. Never raises on mismatch.
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except Exception:
        # Malformed hash or encoding error — treat as invalid
        return False


# ---------------------------------------------------------------------------
# JWT token utilities
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def create_access_token(
    user_id: str,
    role_name: str,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Create a signed JWT access token.
    Claims: sub, role, type=access, iat, exp.
    """
    now = _utc_now()
    payload: Dict[str, Any] = {
        "sub": user_id,
        "role": role_name,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    """
    Create a signed JWT refresh token.
    Claims: sub, type=refresh, iat, exp.
    Refresh tokens intentionally carry minimal claims.
    """
    now = _utc_now()
    payload: Dict[str, Any] = {
        "sub": user_id,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    """
    Decode and verify a JWT token.
    Raises JWTError on invalid, expired, or tampered tokens.
    Caller must check the 'type' claim to distinguish access from refresh tokens.
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def decode_access_token(token: str) -> Dict[str, Any]:
    """
    Decode a token and enforce that it is an access token.
    Raises JWTError if the token type is wrong.
    """
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise JWTError("Token is not an access token.")
    return payload


def decode_refresh_token(token: str) -> Dict[str, Any]:
    """
    Decode a token and enforce that it is a refresh token.
    Raises JWTError if the token type is wrong.
    """
    payload = decode_token(token)
    if payload.get("type") != "refresh":
        raise JWTError("Token is not a refresh token.")
    return payload

