import logging
from typing import Generator, Tuple
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Initialize SQLAlchemy 2.x engine using psycopg 3 driver
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    echo=settings.DEBUG,
)

# Configure thread-safe session factory
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency yielding a SQLAlchemy session.
    Guarantees the session is properly closed after request completion.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database_connection() -> Tuple[bool, str]:
    """
    Verify database connectivity by executing 'SELECT 1'.
    Returns (True, message) if reachable, or (False, sanitized_error_message).
    Credentials and sensitive host details are never leaked.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True, "Database connection successful."
    except OperationalError:
        logger.warning("Database connectivity check failed: unreachable or authentication failed.")
        return False, "Database service is currently unreachable."
    except SQLAlchemyError:
        logger.warning("Database connectivity check failed: SQLAlchemy error.")
        return False, "Database service encountered a connection error."
    except Exception as exc:
        logger.error(f"Unexpected database connectivity check failure: {type(exc).__name__}")
        return False, "Unexpected database connectivity error."

