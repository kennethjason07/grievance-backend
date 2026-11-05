import json
from functools import lru_cache
from typing import Any, List, Optional, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_NAME: str = "Government Grievance Tracking System"
    APP_ENV: str = "development"
    DEBUG: bool = False

    # Required settings (must be provided via environment or .env)
    DATABASE_URL: str
    SECRET_KEY: str

    # JWT token settings
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    JWT_ALGORITHM: str = "HS256"

    # CORS configuration - accepts JSON list or comma-separated string
    CORS_ORIGINS: Union[List[str], str] = ["http://localhost:3000", "http://localhost:8000"]

    # Document storage settings
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_BYTES: int = 25 * 1024 * 1024  # 25 MB

    # OCR settings
    TESSERACT_CMD: Optional[str] = None
    TESSDATA_PREFIX: Optional[str] = None
    OCR_DEFAULT_LANGUAGES: str = "eng+kan"
    MAX_OCR_PAGES: int = 20
    MAX_RENDER_DPI: int = 200
    OCR_TIMEOUT_SECONDS: int = 120

    # Translation settings (Stage 2)
    TRANSLATION_PROVIDER: str = "mock"
    INDICTRANS2_MODEL_PATH: Optional[str] = None
    TRANSLATION_MAX_CHUNK_CHARS: int = 1500
    TRANSLATION_TIMEOUT_SECONDS: int = 60

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def validate_database_url(cls, v: Any) -> str:
        if isinstance(v, str):
            url = v.strip()
            # Ensure Psycopg 3 dialect is used with SQLAlchemy
            if url.startswith("postgresql://"):
                return url.replace("postgresql://", "postgresql+psycopg://", 1)
            elif url.startswith("postgres://"):
                return url.replace("postgres://", "postgresql+psycopg://", 1)
            return url
        return v

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            cleaned = v.strip()
            if cleaned.startswith("[") and cleaned.endswith("]"):
                try:
                    parsed = json.loads(cleaned)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except Exception:
                    pass
            return [item.strip() for item in cleaned.split(",") if item.strip()]
        elif isinstance(v, (list, tuple)):
            return [str(item).strip() for item in v if str(item).strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Return a cached instance of the application settings."""
    return Settings()
