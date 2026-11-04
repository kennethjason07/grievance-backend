"""
Development data seeder.

Creates a realistic set of sample data so the frontend team can start
integration testing immediately after running migrations.

Usage:
    python seed_dev.py              # seed everything
    python seed_dev.py --roles-only # seed only system roles (safe for production)
    python seed_dev.py --wipe       # DROP all dev data first, then re-seed
                                    # WARNING: destroys all data in the database

What is seeded
--------------
1. System roles (always idempotent — safe to run in production)
2. Two departments  : Public Works Department, Health Department
3. Two offices      : one per department
4. One follow-up policy per department
5. One user per role per department where applicable:
      super_admin@gov.in         password: Admin@1234
      pwd_admin@gov.in           password: Admin@1234
      pwd_supervisor@gov.in      password: Admin@1234
      pwd_officer@gov.in         password: Admin@1234
      pwd_deo@gov.in             password: Admin@1234
      pwd_viewer@gov.in          password: Admin@1234
      health_admin@gov.in        password: Admin@1234
      health_supervisor@gov.in   password: Admin@1234
      health_officer@gov.in      password: Admin@1234
6. 10 sample grievances (mix of statuses, priorities, departments)

All operations are idempotent: re-running does not create duplicates.
"""

import logging
import sys
import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.roles import RoleName
from app.core.security import hash_password
from app.models import (
    Department,
    Grievance,
    GrievanceAssignment,
    Office,
    Role,
    User,
    FollowUpPolicy,
)
from app.services.seeder import seed_roles
from app.utils.grievance_number import generate_grievance_number

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_PASSWORD = "Admin@1234"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_create_department(db: Session, name: str, code: str, description: str) -> Department:
    dept = db.query(Department).filter(Department.name == name).first()
    if dept:
        logger.info("Department '%s' already exists — skipping.", name)
        return dept
    dept = Department(name=name, code=code, description=description)
    db.add(dept)
    db.flush()
    logger.info("Created department: %s", name)
    return dept


def _get_or_create_office(
    db: Session,
    name: str,
    office_code: str,
    district: str,
    state: str,
    department_id,
) -> Office:
    office = db.query(Office).filter(Office.office_code == office_code).first()
    if office:
        logger.info("Office '%s' already exists — skipping.", office_code)
        return office
    office = Office(
        name=name,
        office_code=office_code,
        district=district,
        state=state,
        department_id=department_id,
    )
    db.add(office)
    db.flush()
    logger.info("Created office: %s (%s)", name, office_code)
    return office


def _get_or_create_policy(
    db: Session,
    department_id,
    name: str,
    first_reminder_after_days: int,
    reminder_interval_days: int,
    escalation_after_days: int,
    max_reminders: int,
) -> FollowUpPolicy:
    policy = (
        db.query(FollowUpPolicy)
        .filter(
            FollowUpPolicy.department_id == department_id,
            FollowUpPolicy.name == name,
        )
        .first()
    )
    if policy:
        logger.info("Policy '%s' already exists — skipping.", name)
        return policy
    policy = FollowUpPolicy(
        department_id=department_id,
        name=name,
        description=f"Default follow-up policy for {name}",
        first_reminder_after_days=first_reminder_after_days,
        reminder_interval_days=reminder_interval_days,
        escalation_after_days=escalation_after_days,
        max_reminders=max_reminders,
        is_active=True,
    )
    db.add(policy)
    db.flush()
    logger.info("Created policy: %s", name)
    return policy


def _get_or_create_user(
    db: Session,
    full_name: str,
    email: str,
    role_name: str,
    department_id=None,
    office_id=None,
    phone: str = None,
) -> User:
    user = db.query(User).filter(User.email == email).first()
    if user:
        logger.info("User '%s' already exists — skipping.", email)
        return user
    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        raise RuntimeError(f"Role '{role_name}' not found — run seed_roles first.")
    user = User(
        full_name=full_name,
        email=email,
        password_hash=hash_password(DEFAULT_PASSWORD),
        role_id=role.id,
        department_id=department_id,
        office_id=office_id,
        phone=phone,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    logger.info("Created user: %s (%s)", email, role_name)
    return user


def _get_or_create_grievance(
    db: Session,
    title: str,
    description: str,
    department_id,
    office_id,
    created_by_id,
    status: str,
    priority: str,
    assigned_to_id=None,
    assigned_at: datetime = None,
) -> Grievance:
    existing = db.query(Grievance).filter(Grievance.title == title).first()
    if existing:
        logger.info("Grievance '%s' already exists — skipping.", title)
        return existing

    grv_number = generate_grievance_number(db)
    grievance = Grievance(
        grievance_number=grv_number,
        title=title,
        description=description,
        source="ONLINE_PORTAL",
        original_language="en",
        suggested_department_id=department_id,
        confirmed_department_id=department_id,
        office_id=office_id,
        status=status,
        priority=priority,
        assigned_to_user_id=assigned_to_id,
        created_by=created_by_id,
    )
    db.add(grievance)
    db.flush()

    if assigned_to_id and assigned_at:
        assignment = GrievanceAssignment(
            grievance_id=grievance.id,
            assigned_to_user_id=assigned_to_id,
            assigned_by_user_id=created_by_id,
            assigned_at=assigned_at,
            remarks="Assigned during dev seeding",
        )
        db.add(assignment)
        db.flush()

    logger.info("Created grievance [%s]: %s (%s)", grv_number, title, status)
    return grievance


# ---------------------------------------------------------------------------
# Main seed function
# ---------------------------------------------------------------------------

def seed_dev_data(db: Session) -> None:
    logger.info("=" * 60)
    logger.info("Step 1: System roles")
    logger.info("=" * 60)
    seed_roles(db, commit=False)

    # -----------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("Step 2: Departments")
    logger.info("=" * 60)
    pwd_dept = _get_or_create_department(
        db,
        name="Public Works Department",
        code="PWD",
        description="Responsible for roads, bridges, buildings, and public infrastructure.",
    )
    health_dept = _get_or_create_department(
        db,
        name="Health Department",
        code="HLTH",
        description="Manages public health services, hospitals, and sanitation.",
    )

    # -----------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("Step 3: Offices")
    logger.info("=" * 60)
    pwd_hq = _get_or_create_office(
        db,
        name="PWD Headquarters",
        office_code="PWD-HQ-01",
        district="Bangalore Urban",
        state="Karnataka",
        department_id=pwd_dept.id,
    )
    health_hq = _get_or_create_office(
        db,
        name="Health Dept Headquarters",
        office_code="HLTH-HQ-01",
        district="Bangalore Urban",
        state="Karnataka",
        department_id=health_dept.id,
    )

    # -----------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("Step 4: Follow-up policies")
    logger.info("=" * 60)
    _get_or_create_policy(
        db,
        department_id=pwd_dept.id,
        name="PWD Standard Policy",
        first_reminder_after_days=3,
        reminder_interval_days=2,
        escalation_after_days=10,
        max_reminders=3,
    )
    _get_or_create_policy(
        db,
        department_id=health_dept.id,
        name="Health Standard Policy",
        first_reminder_after_days=2,
        reminder_interval_days=2,
        escalation_after_days=7,
        max_reminders=4,
    )

    # -----------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("Step 5: Users")
    logger.info("=" * 60)

    # Super admin — no department
    super_admin = _get_or_create_user(
        db,
        full_name="Super Administrator",
        email="super_admin@gov.in",
        role_name=RoleName.SUPER_ADMIN,
        phone="9000000001",
    )

    # PWD users
    pwd_admin = _get_or_create_user(
        db,
        full_name="PWD Admin",
        email="pwd_admin@gov.in",
        role_name=RoleName.DEPARTMENT_ADMIN,
        department_id=pwd_dept.id,
        office_id=pwd_hq.id,
        phone="9000000002",
    )
    pwd_supervisor = _get_or_create_user(
        db,
        full_name="PWD Supervisor",
        email="pwd_supervisor@gov.in",
        role_name=RoleName.SUPERVISOR,
        department_id=pwd_dept.id,
        office_id=pwd_hq.id,
        phone="9000000003",
    )
    pwd_officer = _get_or_create_user(
        db,
        full_name="PWD Officer",
        email="pwd_officer@gov.in",
        role_name=RoleName.OFFICER,
        department_id=pwd_dept.id,
        office_id=pwd_hq.id,
        phone="9000000004",
    )
    _get_or_create_user(
        db,
        full_name="PWD Data Entry Operator",
        email="pwd_deo@gov.in",
        role_name=RoleName.DATA_ENTRY_OPERATOR,
        department_id=pwd_dept.id,
        office_id=pwd_hq.id,
        phone="9000000005",
    )
    _get_or_create_user(
        db,
        full_name="PWD Viewer",
        email="pwd_viewer@gov.in",
        role_name=RoleName.VIEWER,
        department_id=pwd_dept.id,
        phone="9000000006",
    )

    # Health users
    health_admin = _get_or_create_user(
        db,
        full_name="Health Admin",
        email="health_admin@gov.in",
        role_name=RoleName.DEPARTMENT_ADMIN,
        department_id=health_dept.id,
        office_id=health_hq.id,
        phone="9000000007",
    )
    _get_or_create_user(
        db,
        full_name="Health Supervisor",
        email="health_supervisor@gov.in",
        role_name=RoleName.SUPERVISOR,
        department_id=health_dept.id,
        office_id=health_hq.id,
        phone="9000000008",
    )
    health_officer = _get_or_create_user(
        db,
        full_name="Health Officer",
        email="health_officer@gov.in",
        role_name=RoleName.OFFICER,
        department_id=health_dept.id,
        office_id=health_hq.id,
        phone="9000000009",
    )

    # -----------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("Step 6: Sample grievances")
    logger.info("=" * 60)
    now = datetime.now(timezone.utc)

    sample_grievances = [
        # (title, description, dept, office, registered_by, status, priority, assigned_to, assigned_at_offset_days)
        (
            "Road outside Market Circle needs repair",
            "Large potholes have formed on the main road near Market Circle, causing accidents.",
            pwd_dept, pwd_hq, super_admin, "ASSIGNED", "HIGH",
            pwd_officer, -5,
        ),
        (
            "Street lights not working in Sector 4",
            "Street lights in Sector 4 have been non-functional for over two weeks.",
            pwd_dept, pwd_hq, super_admin, "IN_PROGRESS", "MEDIUM",
            pwd_officer, -8,
        ),
        (
            "Broken footpath near school",
            "The footpath outside Government School No.3 is cracked and dangerous for children.",
            pwd_dept, pwd_hq, pwd_admin, "NEW", "LOW",
            None, None,
        ),
        (
            "Sewage overflow on Main Road",
            "Sewage water is overflowing onto Main Road creating a health hazard.",
            pwd_dept, pwd_hq, pwd_admin, "UNDER_REVIEW", "HIGH",
            None, None,
        ),
        (
            "Bridge inspection request",
            "Citizens request urgent structural inspection of the old river bridge.",
            pwd_dept, pwd_hq, super_admin, "RESOLVED", "HIGH",
            pwd_officer, -15,
        ),
        (
            "Garbage not collected for 10 days",
            "Municipal garbage collection has not happened in our locality for over ten days.",
            health_dept, health_hq, super_admin, "ASSIGNED", "HIGH",
            health_officer, -3,
        ),
        (
            "Hospital OPD closed on working days",
            "The OPD at the community health centre remains closed on Monday and Tuesday.",
            health_dept, health_hq, health_admin, "IN_PROGRESS", "MEDIUM",
            health_officer, -6,
        ),
        (
            "Request for sanitation drive in slum area",
            "Residents request a sanitation and fumigation drive in the slum near Lotus Nagar.",
            health_dept, health_hq, health_admin, "NEW", "MEDIUM",
            None, None,
        ),
        (
            "Ambulance service not responding",
            "Emergency ambulance service (108) did not respond to two distress calls this week.",
            health_dept, health_hq, super_admin, "AWAITING_INFORMATION", "HIGH",
            health_officer, -4,
        ),
        (
            "Drinking water quality complaint",
            "Residents report foul smell and discolouration in tap water supply for the past week.",
            health_dept, health_hq, super_admin, "CLOSED", "MEDIUM",
            health_officer, -20,
        ),
    ]

    for (
        title, description, dept, office, registered_by,
        status, priority, assigned_to, offset_days
    ) in sample_grievances:
        assigned_at = (now + timedelta(days=offset_days)) if offset_days is not None else None
        _get_or_create_grievance(
            db,
            title=title,
            description=description,
            department_id=dept.id,
            office_id=office.id,
            created_by_id=registered_by.id,
            status=status,
            priority=priority,
            assigned_to_id=assigned_to.id if assigned_to else None,
            assigned_at=assigned_at,
        )

    db.commit()
    logger.info("=" * 60)
    logger.info("Dev seeding complete.")
    logger.info("=" * 60)


def wipe_data(db: Session) -> None:
    """
    Delete all application data (preserves schema).
    Use only in development. Irreversible.
    """
    from sqlalchemy import text
    logger.warning("Wiping all data from the database...")
    # Order respects FK constraints
    tables = [
        "activity_logs", "notifications", "escalations",
        "follow_up_actions", "follow_up_tasks", "follow_up_policies",
        "grievance_assignments", "grievances",
        "users", "offices", "departments", "roles",
    ]
    for table in tables:
        db.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
    db.commit()
    logger.warning("All data wiped.")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Seed the grievance system database with development data."
    )
    parser.add_argument(
        "--roles-only",
        action="store_true",
        help="Seed only system roles (safe for production).",
    )
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="Drop all data before seeding. DEVELOPMENT ONLY. Irreversible.",
    )
    args = parser.parse_args()

    db: Session = SessionLocal()
    try:
        if args.wipe:
            confirm = input(
                "\n⚠️  WARNING: This will DELETE all data. Type 'yes' to confirm: "
            ).strip()
            if confirm.lower() != "yes":
                logger.info("Wipe cancelled.")
                return
            wipe_data(db)

        if args.roles_only:
            logger.info("Seeding system roles only...")
            result = seed_roles(db, commit=True)
            logger.info(
                "Done: %d inserted, %d already existed.",
                result["inserted"],
                result["already_existed"],
            )
        else:
            seed_dev_data(db)

        logger.info("")
        logger.info("Login credentials (all use password: %s)", DEFAULT_PASSWORD)
        logger.info("  super_admin@gov.in       — SUPER_ADMIN")
        logger.info("  pwd_admin@gov.in         — DEPARTMENT_ADMIN (PWD)")
        logger.info("  pwd_supervisor@gov.in    — SUPERVISOR       (PWD)")
        logger.info("  pwd_officer@gov.in       — OFFICER          (PWD)")
        logger.info("  pwd_deo@gov.in           — DATA_ENTRY_OP    (PWD)")
        logger.info("  pwd_viewer@gov.in        — VIEWER           (PWD)")
        logger.info("  health_admin@gov.in      — DEPARTMENT_ADMIN (Health)")
        logger.info("  health_supervisor@gov.in — SUPERVISOR       (Health)")
        logger.info("  health_officer@gov.in    — OFFICER          (Health)")

    except Exception as exc:
        logger.error("Seeding failed: %s", exc)
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
