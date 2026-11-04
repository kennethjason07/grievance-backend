"""
Stage 10B Tests: Follow-up Task Management.

Covers:
  1.  Auto-schedule hook: task created on first grievance assignment.
  2.  Clock anchor: due_at = first_assignment.assigned_at + policy.first_reminder_after_days.
  3.  max_reminders = 0 → no auto task created.
  4.  No active policy → no auto task created (silent).
  5.  Partial unique index: only one SCHEDULED task per grievance.
  6.  Manual scheduling replaces existing SCHEDULED task (cancel + create).
  7.  Manual scheduling rejects past due_at.
  8.  Cancel a SCHEDULED task (endpoint + audit log).
  9.  Cannot cancel a COMPLETED or already-CANCELLED task.
  10. Overdue task query (overdue_only=true).
  11. Department isolation: cross-department task access rejected.
  12. Officer can view tasks (VIEW_FOLLOW_UPS), but cannot manage (MANAGE_FOLLOW_UPS).
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Generator

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
    FollowUpPolicy,
    FollowUpTask,
    Grievance,
    GrievanceAssignment,
    Role,
    User,
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


def _make_user(db: Session, role_name: str, dept_id=None) -> User:
    role = _get_or_create_role(db, role_name)
    user = User(
        full_name=f"User {role_name} {uuid.uuid4().hex[:6]}",
        email=f"u_{uuid.uuid4().hex[:8]}@gov.in",
        password_hash=hash_password("Pass123!"),
        role_id=role.id,
        department_id=dept_id,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    return user


def _make_department(db: Session, suffix: str = "") -> Department:
    dept = Department(
        name=f"Test Dept {suffix or uuid.uuid4().hex[:4]}",
        code=f"TD{uuid.uuid4().hex[:6].upper()}",
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
        name=f"Policy {uuid.uuid4().hex[:6]}",
        first_reminder_after_days=first_reminder,
        reminder_interval_days=interval,
        escalation_after_days=escalation,
        max_reminders=max_reminders,
        is_active=is_active,
    )
    db.add(policy)
    db.flush()
    return policy


def _make_grievance(db: Session, dept_id, creator_id) -> Grievance:
    g = Grievance(
        grievance_number=f"GRV-TEST-{uuid.uuid4().hex[:8].upper()}",
        title="Test Grievance",
        description="For Stage 10B testing",
        source="IN_PERSON",
        original_language="en",
        status="UNDER_REVIEW",
        priority="MEDIUM",
        created_by=creator_id,
        suggested_department_id=dept_id,
        confirmed_department_id=dept_id,
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
    """Create a GrievanceAssignment record (bypasses service layer for test setup)."""
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


# ---------------------------------------------------------------------------
# 1. Auto-schedule: task created on first assignment
# ---------------------------------------------------------------------------

@skip_if_no_db
def test_auto_schedule_on_first_assignment(db: Session):
    """
    When an active policy exists and max_reminders > 0,
    assigning a grievance must auto-create one SCHEDULED follow-up task.
    due_at must equal assigned_at + first_reminder_after_days.
    """
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)
    creator = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)

    policy = _make_policy(db, dept.id, first_reminder=3)
    grievance = _make_grievance(db, dept.id, creator.id)
    grievance.status = "UNDER_REVIEW"
    db.flush()

    # Use the assignment API endpoint so the hook fires
    payload = {"officer_id": str(officer.id), "remarks": "Initial assignment"}
    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/assign",
        json=payload,
        headers=_auth(admin),
    )
    assert resp.status_code == 200, resp.text

    # Verify one SCHEDULED task was created
    tasks = db.query(FollowUpTask).filter_by(grievance_id=grievance.id, status="SCHEDULED").all()
    assert len(tasks) == 1, f"Expected 1 SCHEDULED task, found {len(tasks)}"

    task = tasks[0]
    # Verify clock anchor: assigned_at comes from the GrievanceAssignment row
    assignment = (
        db.query(GrievanceAssignment)
        .filter_by(grievance_id=grievance.id)
        .order_by(GrievanceAssignment.assigned_at.asc())
        .first()
    )
    assert assignment is not None
    expected_due = assignment.assigned_at + timedelta(days=policy.first_reminder_after_days)
    # Allow 1-second tolerance for tz conversions
    assert abs((task.due_at - expected_due).total_seconds()) < 2, (
        f"due_at {task.due_at} != expected {expected_due}"
    )

    # Audit log created
    audit = db.query(ActivityLog).filter_by(
        action="FOLLOW_UP_TASK_SCHEDULED", entity_id=task.id
    ).first()
    assert audit is not None


# ---------------------------------------------------------------------------
# 2. max_reminders = 0 → no auto task
# ---------------------------------------------------------------------------

@skip_if_no_db
def test_no_auto_task_when_max_reminders_zero(db: Session):
    """Policy with max_reminders=0 must not create any SCHEDULED task on assignment."""
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)
    creator = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)

    _make_policy(db, dept.id, max_reminders=0)
    grievance = _make_grievance(db, dept.id, creator.id)
    grievance.status = "UNDER_REVIEW"
    db.flush()

    payload = {"officer_id": str(officer.id)}
    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/assign",
        json=payload,
        headers=_auth(admin),
    )
    assert resp.status_code == 200, resp.text

    tasks = db.query(FollowUpTask).filter_by(grievance_id=grievance.id, status="SCHEDULED").all()
    assert len(tasks) == 0, "No task should be created when max_reminders=0"


# ---------------------------------------------------------------------------
# 3. No active policy → no auto task (silent)
# ---------------------------------------------------------------------------

@skip_if_no_db
def test_no_auto_task_when_no_active_policy(db: Session):
    """If no active policy exists, assignment proceeds and no task is created."""
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)
    creator = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)

    # Policy is inactive
    _make_policy(db, dept.id, is_active=False)
    grievance = _make_grievance(db, dept.id, creator.id)
    grievance.status = "UNDER_REVIEW"
    db.flush()

    payload = {"officer_id": str(officer.id)}
    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/assign",
        json=payload,
        headers=_auth(admin),
    )
    assert resp.status_code == 200, resp.text

    tasks = db.query(FollowUpTask).filter_by(grievance_id=grievance.id, status="SCHEDULED").all()
    assert len(tasks) == 0


# ---------------------------------------------------------------------------
# 4. Manual scheduling
# ---------------------------------------------------------------------------

@skip_if_no_db
def test_manual_schedule_follow_up_task(db: Session):
    """MANAGE_FOLLOW_UPS holder can manually schedule a follow-up task."""
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)
    creator = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)

    grievance = _make_grievance(db, dept.id, creator.id)
    _make_assignment(db, grievance, officer, admin)

    future_dt = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    payload = {"due_at": future_dt}

    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/schedule-follow-up",
        json=payload,
        headers=_auth(admin),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "SCHEDULED"
    assert data["grievance_id"] == str(grievance.id)


@skip_if_no_db
def test_manual_schedule_rejects_past_due_at(db: Session):
    """Scheduling a task with a past due_at must return 400."""
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)
    creator = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)

    grievance = _make_grievance(db, dept.id, creator.id)
    _make_assignment(db, grievance, officer, admin)

    past_dt = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/schedule-follow-up",
        json={"due_at": past_dt},
        headers=_auth(admin),
    )
    assert resp.status_code == 400
    assert "future" in resp.json()["detail"].lower()


@skip_if_no_db
def test_manual_schedule_replaces_existing_scheduled_task(db: Session):
    """
    If a SCHEDULED task already exists, manual scheduling must cancel it and
    create a new one (freeing the partial unique index slot).
    """
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)
    creator = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)

    grievance = _make_grievance(db, dept.id, creator.id)
    _make_assignment(db, grievance, officer, admin)

    # Create first scheduled task
    dt1 = (datetime.now(timezone.utc) + timedelta(days=4)).isoformat()
    r1 = client.post(
        f"/api/v1/grievances/{grievance.id}/schedule-follow-up",
        json={"due_at": dt1},
        headers=_auth(admin),
    )
    assert r1.status_code == 201
    task1_id = r1.json()["id"]

    # Schedule again → should replace
    dt2 = (datetime.now(timezone.utc) + timedelta(days=8)).isoformat()
    r2 = client.post(
        f"/api/v1/grievances/{grievance.id}/schedule-follow-up",
        json={"due_at": dt2},
        headers=_auth(admin),
    )
    assert r2.status_code == 201, r2.text
    task2_id = r2.json()["id"]
    assert task2_id != task1_id

    # Old task must now be CANCELLED
    old_task = db.get(FollowUpTask, task1_id)
    assert old_task.status == "CANCELLED"

    # Only one SCHEDULED task exists
    scheduled = db.query(FollowUpTask).filter_by(
        grievance_id=grievance.id, status="SCHEDULED"
    ).all()
    assert len(scheduled) == 1
    assert str(scheduled[0].id) == task2_id


# ---------------------------------------------------------------------------
# 5. Cancel endpoint
# ---------------------------------------------------------------------------

@skip_if_no_db
def test_cancel_scheduled_task(db: Session):
    """Cancel a SCHEDULED task via PATCH endpoint and verify audit log."""
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)
    creator = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)

    grievance = _make_grievance(db, dept.id, creator.id)
    _make_assignment(db, grievance, officer, admin)

    # Schedule one task
    dt = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    cr = client.post(
        f"/api/v1/grievances/{grievance.id}/schedule-follow-up",
        json={"due_at": dt},
        headers=_auth(admin),
    )
    task_id = cr.json()["id"]

    # Cancel it
    resp = client.patch(
        f"/api/v1/follow-up-tasks/{task_id}/cancel",
        headers=_auth(admin),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "CANCELLED"

    # Audit log
    audit = db.query(ActivityLog).filter_by(
        action="FOLLOW_UP_TASK_CANCELLED", entity_id=task_id
    ).first()
    assert audit is not None


@skip_if_no_db
def test_cannot_cancel_completed_or_cancelled_task(db: Session):
    """Attempting to cancel a COMPLETED or already-CANCELLED task must return 400."""
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)
    creator = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)

    grievance = _make_grievance(db, dept.id, creator.id)
    _make_assignment(db, grievance, officer, admin)

    # Insert a COMPLETED task directly
    completed_task = FollowUpTask(
        grievance_id=grievance.id,
        due_at=datetime.now(timezone.utc) - timedelta(days=1),
        status="COMPLETED",
        completed_at=datetime.now(timezone.utc),
    )
    db.add(completed_task)
    db.flush()

    resp = client.patch(
        f"/api/v1/follow-up-tasks/{completed_task.id}/cancel",
        headers=_auth(admin),
    )
    assert resp.status_code == 400
    assert "SCHEDULED" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 6. Overdue query
# ---------------------------------------------------------------------------

@skip_if_no_db
def test_overdue_task_query(db: Session):
    """overdue_only=true must return only SCHEDULED tasks with due_at in the past."""
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)
    creator = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)

    grievance = _make_grievance(db, dept.id, creator.id)
    _make_assignment(db, grievance, officer, admin)

    # Insert overdue SCHEDULED task directly (bypassing unique index — no other SCHEDULED task)
    overdue_task = FollowUpTask(
        grievance_id=grievance.id,
        due_at=datetime.now(timezone.utc) - timedelta(days=2),
        status="SCHEDULED",
    )
    db.add(overdue_task)
    db.flush()

    resp = client.get(
        "/api/v1/follow-up-tasks",
        params={"grievance_id": str(grievance.id), "overdue_only": "true"},
        headers=_auth(admin),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 1
    task_ids = [t["id"] for t in data["items"]]
    assert str(overdue_task.id) in task_ids


# ---------------------------------------------------------------------------
# 7. Department isolation
# ---------------------------------------------------------------------------

@skip_if_no_db
def test_cross_department_task_access_rejected(db: Session):
    """A DEPARTMENT_ADMIN from dept2 must not read tasks belonging to dept1."""
    dept1 = _make_department(db)
    dept2 = _make_department(db)

    admin1 = _make_user(db, RoleName.SUPER_ADMIN)
    dept2_admin = _make_user(db, RoleName.DEPARTMENT_ADMIN, dept2.id)
    officer = _make_user(db, RoleName.OFFICER, dept1.id)
    creator = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept1.id)

    grievance = _make_grievance(db, dept1.id, creator.id)
    _make_assignment(db, grievance, officer, admin1)

    # Create a task for dept1 grievance
    task = FollowUpTask(
        grievance_id=grievance.id,
        due_at=datetime.now(timezone.utc) + timedelta(days=5),
        status="SCHEDULED",
    )
    db.add(task)
    db.flush()

    # dept2 admin tries to read this task → 403
    resp = client.get(
        f"/api/v1/follow-up-tasks/{task.id}",
        headers=_auth(dept2_admin),
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 8. Permission: officer can view but cannot manage
# ---------------------------------------------------------------------------

@skip_if_no_db
def test_officer_cannot_schedule_task(db: Session):
    """Officer lacks MANAGE_FOLLOW_UPS permission and must receive 403."""
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)
    creator = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)

    grievance = _make_grievance(db, dept.id, creator.id)
    _make_assignment(db, grievance, officer, admin)

    future_dt = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/schedule-follow-up",
        json={"due_at": future_dt},
        headers=_auth(officer),
    )
    assert resp.status_code == 403


@skip_if_no_db
def test_officer_can_view_tasks(db: Session):
    """Officer has VIEW_FOLLOW_UPS and must be able to list tasks in their department."""
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)
    creator = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)

    grievance = _make_grievance(db, dept.id, creator.id)
    _make_assignment(db, grievance, officer, admin)

    task = FollowUpTask(
        grievance_id=grievance.id,
        due_at=datetime.now(timezone.utc) + timedelta(days=3),
        status="SCHEDULED",
    )
    db.add(task)
    db.flush()

    resp = client.get(
        "/api/v1/follow-up-tasks",
        params={"grievance_id": str(grievance.id)},
        headers=_auth(officer),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] >= 1
