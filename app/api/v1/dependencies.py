"""
FastAPI authentication dependencies.

Usage:
    @router.get("/protected")
    def protected(current_user: User = Depends(get_current_user)):
        ...

    @router.post("/admin-only")
    def admin_only(user: User = Depends(require_permission(Permission.MANAGE_USERS))):
        ...
"""
import logging
from typing import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.roles import RoleName, has_permission
from app.models.user import User
from app.services.auth import (
    AccountInactiveError,
    AuthenticationError,
    get_user_from_access_token,
)

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    FastAPI dependency that extracts and validates the current authenticated user
    from the Authorization header Bearer token.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    inactive_exception = HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Account is inactive.",
    )
    try:
        return get_user_from_access_token(db, token)
    except AccountInactiveError:
        raise inactive_exception
    except AuthenticationError:
        raise credentials_exception
    except Exception:
        logger.exception("Unexpected error in get_current_user")
        raise credentials_exception


def require_permission(permission: str) -> Callable:
    """
    Dependency factory that enforces a specific permission.

    Example:
        Depends(require_permission(Permission.MANAGE_USERS))
    """
    def _check(current_user: User = Depends(get_current_user)) -> User:
        if not has_permission(current_user.role.name, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: '{permission}' required.",
            )
        return current_user

    return _check


def check_department_access(current_user: User, department_id: any) -> None:
    """
    Ensure the user is authorized to access or manage the specified department.
    SUPER_ADMIN has universal access.
    All other users can only access their assigned department.
    """
    if current_user.role.name == RoleName.SUPER_ADMIN:
        return
    if str(current_user.department_id) != str(department_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Cross-department access is prohibited.",
        )


