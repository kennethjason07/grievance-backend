import logging
from typing import Any, Dict
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_v1_router
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="Backend API for Government Grievance Tracking and Follow-up Reminder System",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Configure CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global Exception Handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle uncaught server errors gracefully without exposing internals or credentials.
    """
    logger.exception(f"Unhandled exception on {request.method} {request.url.path}: {exc}")
    detail_msg = "An unexpected server error occurred."
    if settings.DEBUG:
        detail_msg = f"{type(exc).__name__}: {str(exc)}"

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": detail_msg},
    )


# Root Endpoint
@app.get(
    "/",
    summary="Application Root",
    response_model=Dict[str, Any],
    tags=["Root"],
)
def read_root() -> Dict[str, Any]:
    """
    Return basic application information and status.
    """
    return {
        "name": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "version": "1.0.0",
        "status": "online",
        "docs_url": "/docs",
        "api_v1": "/api/v1",
    }


# Application-level Health Endpoint
@app.get(
    "/health",
    summary="Application Health Check",
    response_model=Dict[str, Any],
    tags=["Health"],
)
def health_check() -> Dict[str, Any]:
    """
    Return application running status.
    """
    return {
        "status": "healthy",
        "environment": settings.APP_ENV,
    }


# Register versioned API router
app.include_router(api_v1_router, prefix="/api/v1")

