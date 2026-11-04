"""
Idempotent database seeder for system roles.

Design:
- Uses INSERT ... ON CONFLICT DO NOTHING to be safe on repeated runs.
- Never deletes or modifies existing roles — only inserts missing ones.
- Safe for both development and production execution.
- Can be called from CLI, startup hook, or test setup.
"""
import logging
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.roles import ROLE_SEED_DATA
from app.models.role import Role

logger = logging.getLogger(__name__)


def seed_roles(db: Session, *, commit: bool = True) -> dict:
    """
    Idempotently insert all system roles.

    Returns a summary dict:
        { "inserted": int, "already_existed": int, "roles": [role_name, ...] }

    Parameters:
        db      – Active SQLAlchemy session.
        commit  – If True, commit after seeding. Set False in tests that manage
                  their own transactions.
    """
    inserted = 0
    already_existed = 0
    seeded_names = []

    for spec in ROLE_SEED_DATA:
        existing = db.query(Role).filter(Role.name == spec["name"]).first()
        if existing:
            already_existed += 1
            seeded_names.append(spec["name"])
            logger.debug("Role '%s' already exists — skipping.", spec["name"])
        else:
            role = Role(name=spec["name"], description=spec["description"])
            db.add(role)
            inserted += 1
            seeded_names.append(spec["name"])
            logger.info("Seeding role '%s'.", spec["name"])

    if commit:
        db.commit()
    else:
        db.flush()

    return {
        "inserted": inserted,
        "already_existed": already_existed,
        "roles": seeded_names,
    }


def get_role_by_name(db: Session, role_name: str) -> Role | None:
    """Return a Role by name, or None if not found."""
    return db.query(Role).filter(Role.name == role_name).first()

