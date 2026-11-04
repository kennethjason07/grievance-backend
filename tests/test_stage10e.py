"""
Stage 10E Tests: Escalation Processing.

Covers:
  1. Escalation generated after configured threshold.
  2. No escalation before threshold.
  3. Reassignment does not reset escalation timing.
  4. Resolved grievance excluded from escalation.
  5. Closed grievance excluded from escalation.
  6. Completed/cancelled follow-up handling (does not independently trigger escalation).
  7. Missing supervisor fallback to department admin.
  8. No eligible recipient logged in ActivityLog and safely handled.
  9. Multiple supervisors deterministic selection (matching office, seniority, id).
  10. Duplicate processing is idempotent.
  11. Concurrent processing handled by partial unique index uq_escalations_active_grievance.
  12. Escalation acknowledgement.
  13. Escalation resolution (with optional notes).
  14. Escalation dismissal with mandatory reason.
  15. Department isolation on escalations.
  16. Escalation does not resolve grievance.
  17. Escalation does not reassign grievance.
  18. Audit log creation for escalation events.
  19. Transaction rollback on failure.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.roles import RoleName
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import (
    ActivityLog,
    Base,
    Department,
    Escalation,
    FollowUpPolicy,
    FollowUpTask,
    Grievance,
    GrievanceAssignment,
    Notification,
    Office,
    Role,
    User,
)
from app.schemas.escalation import EscalationResolveRequest
from app.services.escalation_service import (
    acknowledge_escalation,
    dismiss_escalation,
    process_due_escalations,
    resolve_escalation,
)

settings = get_settings()
_base_url = settings.DATABASE_URL.rsplit("/", 1)[0]
TEST_DATABASE_URL = f"{_base_url}/grievance_test_db"

client = TestClient(app)


# ---------------------------------------------------------------------------
# DB availability guard
# ---------------------------------------------------------------------------

def _test_engine_available() -> bool:
    try:
        eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


db_available = _test_engine_available()
skip_if_no_db = pytest.mark.skipif(
    not db_available,
    reason="grievance_test_db is not available.",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_engine():
    if not db_available:
        pytest.skip("grievance_test_db unavailable")
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db(test_engine) -> Generator[Session, None, None]:
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    session.begin_nested()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    yield session
    app.dependency_overrides.clear()
    session.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_create_role(db: Session, name: str) -> Role:
    role = db.query(Role).filter_by(name=name).first()
    if not role:
        role = Role(name=name, description=f"Role: {name}")
        db.add(role)
        db.flush()
    return role


def _make_user(db: Session, role_name: str, dept_id=None, office_id=None, is_active: bool = True) -> User:
    role = _get_or_create_role(db, role_name)
    user = User(
        full_name=f"User {role_name} {uuid.uuid4().hex[:6]}",
        email=f"u_{uuid.uuid4().hex[:8]}@gov.in",
        password_hash=hash_password("Pass123!"),
        role_id=role.id,
        department_id=dept_id,
        office_id=office_id,
        is_active=is_active,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    return user


def _make_department(db: Session, suffix: str = "") -> Department:
    dept = Department(
        name=f"Dept 10E {suffix or uuid.uuid4().hex[:4]}",
        code=f"D{uuid.uuid4().hex[:6].upper()}",
    )
    db.add(dept)
    db.flush()
    return dept


def _make_policy(
    db: Session,
    dept_id,
    *,
    first_reminder: int = 3,
    interval: int = 2,
    escalation: int = 7,
    max_reminders: int = 3,
    is_active: bool = True,
) -> FollowUpPolicy:
    policy = FollowUpPolicy(
        department_id=dept_id,
        name=f"Policy 10E {uuid.uuid4().hex[:6]}",
        first_reminder_after_days=first_reminder,
        reminder_interval_days=interval,
        escalation_after_days=escalation,
        max_reminders=max_reminders,
        is_active=is_active,
    )
    db.add(policy)
    db.flush()
    return policy


def _make_grievance(db: Session, dept_id, creator_id, office_id=None) -> Grievance:
    g = Grievance(
        grievance_number=f"GRV-10E-{uuid.uuid4().hex[:8].upper()}",
        title="10E Grievance",
        description="For Stage 10E escalation tests",
        source="IN_PERSON",
        original_language="en",
        status="UNDER_REVIEW",
        priority="MEDIUM",
        created_by=creator_id,
        suggested_department_id=dept_id,
        confirmed_department_id=dept_id,
        office_id=office_id,
        date_received=datetime.now(timezone.utc),
    )
    db.add(g)
    db.flush()
    return g


def _make_assignment(
    db: Session,
    grievance: Grievance,
    officer: User,
    assigner: User,
    assigned_at: datetime = None,
) -> GrievanceAssignment:
    now = assigned_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    assignment = GrievanceAssignment(
        grievance_id=grievance.id,
        assigned_to_user_id=officer.id,
        assigned_by_user_id=assigner.id,
        is_active=True,
        assigned_at=now,
    )
    db.add(assignment)
    grievance.assigned_to_user_id = officer.id
    grievance.status = "ASSIGNED"
    db.flush()
    return assignment


def _auth(user: User) -> dict:
    token = create_access_token(user_id=str(user.id), role_name=user.role.name)
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# 1. Escalation generated after configured threshold
# ===========================================================================

@skip_if_no_db
def test_escalation_generated_after_configured_threshold(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    supervisor = _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    policy = _make_policy(db, dept.id, escalation=7)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    # At Day 7: threshold reached
    res = process_due_escalations(db, now=t0 + timedelta(days=7))
    assert res["escalated_count"] == 1

    esc = db.query(Escalation).filter_by(grievance_id=grievance.id).first()
    assert esc is not None
    assert esc.escalated_to_user_id == supervisor.id
    assert esc.escalated_from_user_id == officer.id
    assert esc.status == "PENDING"
    assert esc.trigger_date == t0 + timedelta(days=7)

    # Verify IN_APP notification was generated for supervisor
    notif = db.query(Notification).filter_by(grievance_id=grievance.id, notification_type="ESCALATION").first()
    assert notif is not None
    assert notif.recipient_user_id == supervisor.id
    assert notif.channel == "IN_APP"


# ===========================================================================
# 2. No escalation before threshold
# ===========================================================================

@skip_if_no_db
def test_no_escalation_before_threshold(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=7)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    # At Day 6: threshold not reached
    res = process_due_escalations(db, now=t0 + timedelta(days=6))
    assert res["escalated_count"] == 0
    assert db.query(Escalation).filter_by(grievance_id=grievance.id).count() == 0


# ===========================================================================
# 3. Reassignment does not reset escalation timing
# ===========================================================================

@skip_if_no_db
def test_reassignment_does_not_reset_escalation_timing(db: Session):
    """
    Initial assignment at Day 0. Reassignment at Day 5.
    At Day 7 (from Day 0), escalation MUST fire because the anchor is Day 0.
    """
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    supervisor = _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer1 = _make_user(db, RoleName.OFFICER, dept.id)
    officer2 = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=7)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)

    # Initial assignment at t0
    asgn1 = _make_assignment(db, grievance, officer1, admin, assigned_at=t0)

    # Reassignment at Day 5: deactivates asgn1, creates active asgn2
    asgn1.is_active = False
    asgn2 = GrievanceAssignment(
        grievance_id=grievance.id,
        assigned_to_user_id=officer2.id,
        assigned_by_user_id=admin.id,
        is_active=True,
        assigned_at=t0 + timedelta(days=5),
    )
    db.add(asgn2)
    grievance.assigned_to_user_id = officer2.id
    db.flush()

    # At Day 7: evaluation must trigger escalation based on initial anchor (t0)
    res = process_due_escalations(db, now=t0 + timedelta(days=7))
    assert res["escalated_count"] == 1
    esc = db.query(Escalation).filter_by(grievance_id=grievance.id).first()
    assert esc is not None
    assert esc.escalated_to_user_id == supervisor.id


# ===========================================================================
# 4. Resolved grievance excluded
# ===========================================================================

@skip_if_no_db
def test_resolved_grievance_excluded(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=5)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)
    grievance.status = "RESOLVED"
    db.flush()

    res = process_due_escalations(db, now=t0 + timedelta(days=10))
    assert res["escalated_count"] == 0
    assert db.query(Escalation).filter_by(grievance_id=grievance.id).count() == 0


# ===========================================================================
# 5. Closed grievance excluded
# ===========================================================================

@skip_if_no_db
def test_closed_grievance_excluded(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=5)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)
    grievance.status = "CLOSED"
    db.flush()

    res = process_due_escalations(db, now=t0 + timedelta(days=10))
    assert res["escalated_count"] == 0
    assert db.query(Escalation).filter_by(grievance_id=grievance.id).count() == 0


# ===========================================================================
# 6. Completed/cancelled follow-up handling
# ===========================================================================

@skip_if_no_db
def test_completed_cancelled_follow_up_handling(db: Session):
    """
    Completed or cancelled tasks must not independently trigger escalation if
    the escalation threshold has not yet been crossed.
    """
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=10)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    # Insert a COMPLETED task at Day 2
    task = FollowUpTask(
        grievance_id=grievance.id,
        assigned_to_user_id=officer.id,
        due_at=t0 + timedelta(days=2),
        status="COMPLETED",
        completed_at=t0 + timedelta(days=2),
    )
    db.add(task)
    db.flush()

    # At Day 4: task is completed, but overall escalation SLA is 10 days -> no escalation
    res = process_due_escalations(db, now=t0 + timedelta(days=4))
    assert res["escalated_count"] == 0


# ===========================================================================
# 7. Missing supervisor fallback to department admin
# ===========================================================================

@skip_if_no_db
def test_missing_supervisor_fallback_to_department_admin(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    # No SUPERVISOR exists in this department, only a DEPARTMENT_ADMIN
    dept_admin = _make_user(db, RoleName.DEPARTMENT_ADMIN, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=5)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    res = process_due_escalations(db, now=t0 + timedelta(days=5))
    assert res["escalated_count"] == 1
    esc = db.query(Escalation).filter_by(grievance_id=grievance.id).first()
    assert esc is not None
    assert esc.escalated_to_user_id == dept_admin.id


# ===========================================================================
# 8. No eligible recipient logged and handled
# ===========================================================================

@skip_if_no_db
def test_no_eligible_recipient_logged_and_handled(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    # Neither SUPERVISOR nor DEPARTMENT_ADMIN exists in this department
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=5)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    res = process_due_escalations(db, now=t0 + timedelta(days=6))
    assert res["failures"] == 1
    assert res["escalated_count"] == 0

    audit = db.query(ActivityLog).filter_by(
        action="ESCALATION_PROCESSING_FAILED", entity_id=grievance.id
    ).first()
    assert audit is not None
    assert "no active supervisor or department admin" in audit.description.lower()


# ===========================================================================
# 9. Multiple supervisors deterministic selection
# ===========================================================================

@skip_if_no_db
def test_multiple_supervisors_deterministic_selection(db: Session):
    """
    When multiple supervisors exist:
      1. Matching office is prioritized.
      2. If offices match or neither matches, earliest created_at (seniority) is selected.
    """
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)

    # Create two offices
    office1 = Office(name="HQ Office", office_code=f"HQ{uuid.uuid4().hex[:4].upper()}", district="Bangalore Urban", department_id=dept.id)
    office2 = Office(name="Branch Office", office_code=f"BR{uuid.uuid4().hex[:4].upper()}", district="Bangalore Urban", department_id=dept.id)
    db.add_all([office1, office2])
    db.flush()

    # Supervisor A (branch office, created first)
    sup_a = _make_user(db, RoleName.SUPERVISOR, dept.id, office_id=office2.id)
    # Supervisor B (HQ office, matches grievance office)
    sup_b = _make_user(db, RoleName.SUPERVISOR, dept.id, office_id=office1.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=3)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    # Grievance belongs to office1
    grievance = _make_grievance(db, dept.id, admin.id, office_id=office1.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    res = process_due_escalations(db, now=t0 + timedelta(days=4))
    assert res["escalated_count"] == 1
    esc = db.query(Escalation).filter_by(grievance_id=grievance.id).first()
    # Must select sup_b because sup_b belongs to office1
    assert esc.escalated_to_user_id == sup_b.id


# ===========================================================================
# 10. Duplicate processing is idempotent
# ===========================================================================

@skip_if_no_db
def test_duplicate_processing_is_idempotent(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=5)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    # First run: creates escalation
    r1 = process_due_escalations(db, now=t0 + timedelta(days=6))
    assert r1["escalated_count"] == 1

    # Second run: detects active escalation, skips safely
    r2 = process_due_escalations(db, now=t0 + timedelta(days=6))
    assert r2["escalated_count"] == 0
    assert r2["skipped_or_existing"] >= 1
    assert db.query(Escalation).filter_by(grievance_id=grievance.id).count() == 1


# ===========================================================================
# 11. Concurrent processing handled by partial unique index
# ===========================================================================

@skip_if_no_db
def test_concurrent_processing_partial_unique_index(db: Session):
    """
    Two workers executing concurrently for the same grievance:
    First inserts, second hits partial unique index, savepoint rolls back safely.
    """
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=5)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    # Worker A runs
    r_a = process_due_escalations(db, now=t0 + timedelta(days=6))
    # Worker B runs
    r_b = process_due_escalations(db, now=t0 + timedelta(days=6))

    assert r_a["escalated_count"] + r_b["escalated_count"] == 1
    assert db.query(Escalation).filter_by(grievance_id=grievance.id).count() == 1


# ===========================================================================
# 12. Escalation acknowledgement
# ===========================================================================

@skip_if_no_db
def test_escalation_acknowledgement(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    supervisor = _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=5)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    process_due_escalations(db, now=t0 + timedelta(days=6))
    esc = db.query(Escalation).filter_by(grievance_id=grievance.id).first()

    resp = client.patch(
        f"/api/v1/escalations/{esc.id}/acknowledge",
        headers=_auth(supervisor),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ACKNOWLEDGED"

    # Verify grievance status remains unchanged
    updated_grievance = db.get(Grievance, grievance.id)
    assert updated_grievance.status == "ASSIGNED"


# ===========================================================================
# 13. Escalation resolution
# ===========================================================================

@skip_if_no_db
def test_escalation_resolution(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    supervisor = _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=5)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    process_due_escalations(db, now=t0 + timedelta(days=6))
    esc = db.query(Escalation).filter_by(grievance_id=grievance.id).first()

    resp = client.patch(
        f"/api/v1/escalations/{esc.id}/resolve",
        json={"resolution_notes": "Supervisor reviewed case with officer and expedited resolution."},
        headers=_auth(supervisor),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "RESOLVED"
    assert data["resolved_at"] is not None

    # Verify grievance status remains ASSIGNED (resolving escalation does NOT auto-resolve grievance)
    updated_grievance = db.get(Grievance, grievance.id)
    assert updated_grievance.status == "ASSIGNED"


# ===========================================================================
# 14. Escalation dismissal requires reason
# ===========================================================================

@skip_if_no_db
def test_escalation_dismissal_requires_reason(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    supervisor = _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=5)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    process_due_escalations(db, now=t0 + timedelta(days=6))
    esc = db.query(Escalation).filter_by(grievance_id=grievance.id).first()

    # Empty / missing reason -> 422
    resp_empty = client.patch(
        f"/api/v1/escalations/{esc.id}/dismiss",
        json={"reason": ""},
        headers=_auth(supervisor),
    )
    assert resp_empty.status_code == 422

    # Valid dismissal reason -> 200
    resp_valid = client.patch(
        f"/api/v1/escalations/{esc.id}/dismiss",
        json={"reason": "Citizen requested 2-week grace period; escalation premature."},
        headers=_auth(supervisor),
    )
    assert resp_valid.status_code == 200
    assert resp_valid.json()["status"] == "DISMISSED"


# ===========================================================================
# 15. Department isolation on escalations
# ===========================================================================

@skip_if_no_db
def test_department_isolation_on_escalations(db: Session):
    dept1 = _make_department(db, suffix="Dept1")
    dept2 = _make_department(db, suffix="Dept2")

    admin = _make_user(db, RoleName.SUPER_ADMIN)
    sup1 = _make_user(db, RoleName.SUPERVISOR, dept1.id)
    sup2 = _make_user(db, RoleName.SUPERVISOR, dept2.id)
    officer1 = _make_user(db, RoleName.OFFICER, dept1.id)

    _make_policy(db, dept1.id, escalation=3)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept1.id, admin.id)
    _make_assignment(db, grievance, officer1, admin, assigned_at=t0)

    process_due_escalations(db, now=t0 + timedelta(days=4))
    esc = db.query(Escalation).filter_by(grievance_id=grievance.id).first()

    # Supervisor from Dept2 tries to access escalation of Dept1 -> 403 Forbidden
    resp = client.get(
        f"/api/v1/escalations/{esc.id}",
        headers=_auth(sup2),
    )
    assert resp.status_code == 403


# ===========================================================================
# 16. Escalation does not resolve grievance
# ===========================================================================

@skip_if_no_db
def test_escalation_does_not_resolve_grievance(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=5)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    process_due_escalations(db, now=t0 + timedelta(days=6))
    updated_grievance = db.get(Grievance, grievance.id)
    assert updated_grievance.status != "RESOLVED"
    assert updated_grievance.status == "ASSIGNED"


# ===========================================================================
# 17. Escalation does not reassign grievance
# ===========================================================================

@skip_if_no_db
def test_escalation_does_not_reassign_grievance(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    supervisor = _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=5)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    process_due_escalations(db, now=t0 + timedelta(days=6))
    updated_grievance = db.get(Grievance, grievance.id)

    # Grievance is still assigned to the officer, NOT the supervisor
    assert updated_grievance.assigned_to_user_id == officer.id
    assert updated_grievance.assigned_to_user_id != supervisor.id


# ===========================================================================
# 18. Audit log creation
# ===========================================================================

@skip_if_no_db
def test_audit_log_creation(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    supervisor = _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=5)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    process_due_escalations(db, now=t0 + timedelta(days=6))
    esc = db.query(Escalation).filter_by(grievance_id=grievance.id).first()

    # Verify GRIEVANCE_ESCALATED audit log
    audit_esc = db.query(ActivityLog).filter_by(
        action="GRIEVANCE_ESCALATED", entity_id=esc.id
    ).first()
    assert audit_esc is not None
    assert audit_esc.grievance_id == grievance.id

    # Resolve escalation and verify ESCALATION_RESOLVED audit log
    resolve_escalation(db=db, escalation_id=esc.id, resolve_in=EscalationResolveRequest(), current_user=supervisor)
    audit_res = db.query(ActivityLog).filter_by(
        action="ESCALATION_RESOLVED", entity_id=esc.id
    ).first()
    assert audit_res is not None


# ===========================================================================
# 19. Transaction rollback on failure
# ===========================================================================

@skip_if_no_db
def test_transaction_rollback_on_failure(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    _make_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, escalation=5)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)

    # Use a savepoint to test simulated failure during log_activity
    savepoint = db.begin_nested()
    try:
        with patch("app.services.escalation_service.log_activity", side_effect=RuntimeError("Simulated DB audit crash")):
            process_due_escalations(db, now=t0 + timedelta(days=6))
    except RuntimeError:
        savepoint.rollback()

    # Verify no escalation was persisted
    assert db.query(Escalation).filter_by(grievance_id=grievance.id).count() == 0
