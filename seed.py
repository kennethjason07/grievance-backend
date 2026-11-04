"""
Database seeding entrypoint.
Idempotently seeds required system roles and permissions into the database.
Can be run repeatedly in both development and production environments.

Usage:
    python seed.py
"""
import logging
import sys
from app.core.database import SessionLocal
from app.services.seeder import seed_roles

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    logger.info("Starting role and permission seeding...")
    db = SessionLocal()
    try:
        result = seed_roles(db, commit=True)
        logger.info(
            f"Seeding completed successfully: {result['inserted']} inserted, "
            f"{result['already_existed']} already existed. Total roles: {len(result['roles'])}"
        )
    except Exception as exc:
        logger.error(f"Error during seeding: {exc}")
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()

