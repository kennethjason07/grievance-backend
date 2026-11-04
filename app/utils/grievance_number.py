"""
Concurrency-safe grievance number generator.

Business convention (Stage 8A):
  Format : GRV-YYYY-NNNNN
  Example: GRV-2026-00001
  - Sequential per calendar year (UTC).
  - Resets to 00001 on the first grievance of each new year.
  - Zero-padded to 5 digits (supports up to 99 999 grievances per year).
  - Concurrency safety is achieved via a PostgreSQL advisory lock
    (pg_try_advisory_xact_lock), which is automatically released at
    transaction commit or rollback.

Advisory lock key: 123456789 (fixed application-namespace constant).
Using a transaction-scoped advisory lock means:
  - Concurrent requests queue behind the lock holder.
  - If the transaction rolls back, the counter is not incremented.
  - No separate counter table is needed.
"""
import logging
from datetime import datetime, timezone
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.grievance import Grievance

logger = logging.getLogger(__name__)

# Arbitrary stable integer key scoped to this application.
_ADVISORY_LOCK_KEY = 123_456_789


def generate_grievance_number(db: Session) -> str:
    """
    Generate the next sequential grievance number for the current UTC year.

    Must be called within an active transaction. The advisory lock is held
    until the transaction is committed or rolled back by the caller.

    Returns a string in the format GRV-YYYY-NNNNN.
    """
    # Acquire transaction-scoped advisory lock — blocks until obtained.
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADVISORY_LOCK_KEY})

    current_year = datetime.now(tz=timezone.utc).year
    year_prefix = f"GRV-{current_year}-"

    # Find the highest existing sequence number for this year.
    latest = db.execute(
        select(func.max(Grievance.grievance_number)).where(
            Grievance.grievance_number.like(f"{year_prefix}%")
        )
    ).scalar()

    if latest is None:
        next_seq = 1
    else:
        # Extract the numeric suffix: "GRV-2026-00042" → 42
        try:
            next_seq = int(latest.split("-")[-1]) + 1
        except (ValueError, IndexError):
            logger.error("Unexpected grievance_number format '%s'; resetting sequence.", latest)
            next_seq = 1

    grievance_number = f"{year_prefix}{next_seq:05d}"
    logger.info("Generated grievance number: %s", grievance_number)
    return grievance_number

