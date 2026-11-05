import logging
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1.actions import actions_router
from app.api.v1.assignments import assignments_router
from app.api.v1.auth import auth_router
from app.api.v1.departments import departments_router
from app.api.v1.documents import documents_router
from app.api.v1.translations import translations_router
from app.api.v1.escalations import escalations_router
from app.api.v1.grievances import grievances_router
from app.api.v1.lifecycle import lifecycle_router
from app.api.v1.notifications import notifications_router
from app.api.v1.offices import offices_router
from app.api.v1.policies import policies_router
from app.api.v1.tasks import tasks_router
from app.api.v1.users import users_router
from app.core.database import check_database_connection, get_db

logger = logging.getLogger(__name__)

api_v1_router = APIRouter()

# Register core routers
api_v1_router.include_router(auth_router)
api_v1_router.include_router(departments_router)
api_v1_router.include_router(offices_router)
api_v1_router.include_router(users_router)
api_v1_router.include_router(grievances_router)
api_v1_router.include_router(assignments_router)
api_v1_router.include_router(lifecycle_router)
api_v1_router.include_router(policies_router)
api_v1_router.include_router(tasks_router)
api_v1_router.include_router(actions_router)
api_v1_router.include_router(notifications_router)
api_v1_router.include_router(escalations_router)
api_v1_router.include_router(documents_router)
api_v1_router.include_router(translations_router)


@api_v1_router.get(
    "/health",
    summary="API v1 Health Check",
    response_model=Dict[str, Any],
    tags=["Health"],
)
def api_v1_health() -> Dict[str, Any]:
    """
    Return the operational status of API version 1.
    """
    return {
        "status": "healthy",
        "api_version": "v1",
        "service": "Government Grievance Tracking API",
    }


@api_v1_router.get(
    "/health/db",
    summary="Database Connectivity Diagnostic Check",
    response_model=Dict[str, Any],
    tags=["Health"],
    responses={
        200: {
            "description": "Database is reachable and responding to queries.",
            "content": {
                "application/json": {
                    "example": {
                        "status": "healthy",
                        "database": "reachable",
                        "message": "Database connection successful.",
                    }
                }
            },
        },
        503: {
            "description": "Database is unreachable or misconfigured.",
            "content": {
                "application/json": {
                    "example": {
                        "status": "unhealthy",
                        "database": "unreachable",
                        "message": "Database service is currently unreachable.",
                    }
                }
            },
        },
    },
)
def api_v1_health_db(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Diagnostic database connectivity check executing 'SELECT 1'.

    This endpoint verifies active communication with the configured PostgreSQL instance.
    No credentials, connection strings, or system paths are exposed in the response.
    """
    try:
        db.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "database": "reachable",
            "message": "Database connection successful.",
        }
    except Exception as exc:
        logger.warning(f"Database health check failed: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "unhealthy",
                "database": "unreachable",
                "message": "Database service is currently unreachable.",
            },
        )


# ============================================================================
# Future API v1 Business Modules Routing Structure
# To be attached as business modules are implemented:
#
# api_v1_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
# api_v1_router.include_router(users_router, prefix="/users", tags=["Users"])
# api_v1_router.include_router(departments_router, prefix="/departments", tags=["Departments"])
# api_v1_router.include_router(grievances_router, prefix="/grievances", tags=["Grievances"])
# api_v1_router.include_router(followups_router, prefix="/follow-ups", tags=["Follow-ups"])
# api_v1_router.include_router(notifications_router, prefix="/notifications", tags=["Notifications"])
# api_v1_router.include_router(escalations_router, prefix="/escalations", tags=["Escalations"])
# api_v1_router.include_router(documents_router, prefix="/documents", tags=["Documents"])
# api_v1_router.include_router(reports_router, prefix="/reports", tags=["Reports"])
# ============================================================================

