from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.main import app

client = TestClient(app)


def test_root_endpoint():
    """Verify root endpoint returns basic application metadata."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert "environment" in data
    assert data["status"] == "online"
    assert data["api_v1"] == "/api/v1"
    assert data["docs_url"] == "/docs"


def test_health_endpoint():
    """Verify application-level health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "environment" in data


def test_api_v1_health_endpoint():
    """Verify versioned API v1 health check endpoint."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["api_version"] == "v1"
    assert data["service"] == "Government Grievance Tracking API"


def test_api_v1_health_db_success():
    """
    Verify database health check when database is connected.
    Uses dependency override for isolated testing.
    """
    mock_session = MagicMock()
    mock_session.execute.return_value = True

    def override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/v1/health/db")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database"] == "reachable"
        assert "Database connection successful." in data["message"]
    finally:
        app.dependency_overrides.clear()


def test_api_v1_health_db_failure():
    """
    Verify database health check returns 503 when database is unreachable.
    Uses dependency override for isolated testing.
    """
    mock_session = MagicMock()
    mock_session.execute.side_effect = OperationalError("connection failed", None, Exception("unreachable"))

    def override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/v1/health/db")
        assert response.status_code == 503
        data = response.json()
        assert "detail" in data
        assert data["detail"]["status"] == "unhealthy"
        assert data["detail"]["database"] == "unreachable"
    finally:
        app.dependency_overrides.clear()


def test_config_loading():
    """Verify Settings loads with default and customized values."""
    settings = Settings(
        APP_NAME="Test Grievance App",
        APP_ENV="testing",
        DEBUG=True,
        DATABASE_URL="postgresql://test_user:test_pwd@localhost:5432/test_db",
        SECRET_KEY="test-secret-key-32-characters-minimum",
        CORS_ORIGINS="http://localhost:3000,http://localhost:8080",
    )
    assert settings.APP_NAME == "Test Grievance App"
    assert settings.APP_ENV == "testing"
    assert settings.DEBUG is True
    # Verify postgresql:// is automatically mapped to postgresql+psycopg://
    assert settings.DATABASE_URL.startswith("postgresql+psycopg://")
    # Verify CORS_ORIGINS string is parsed into a list
    assert settings.CORS_ORIGINS == ["http://localhost:3000", "http://localhost:8080"]
    assert settings.is_production is False


def test_config_json_cors_origins():
    """Verify CORS_ORIGINS parses JSON array strings properly."""
    settings = Settings(
        DATABASE_URL="postgresql+psycopg://u:p@localhost/db",
        SECRET_KEY="test-secret-key-32-characters-minimum",
        CORS_ORIGINS='["https://example.gov", "https://police.gov"]',
    )
    assert settings.CORS_ORIGINS == ["https://example.gov", "https://police.gov"]

