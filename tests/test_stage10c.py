"""
Stage 10C Tests: Follow-up Action Recording.

Covers:
  1. Successful action recording.
  2. Missing or invalid action type.
  3. Unauthorised user (VIEWER, DATA_ENTRY_OPERATOR).
  4. Cross-department access rejected.
  5. Failed call attempt completing a task.
  6. No response not resolving grievance.
  7. Response received not resolving grievance.
  8. Optional next follow-up scheduling.
  9. Existing active scheduled task conflict.
  10. Duplicate task completion rejected.
  11. Audit log creation for action recording and task completion.
  12. Transaction rollback on failure.
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
    FollowUpAction,
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
        name=f"Dept 10C {suffix or uuid.uuid4().hex[:4]}",
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
        grievance_number=f"GRV-10C-{uuid.uuid4().hex[:8].upper()}",
        title="10C Grievance",
        description="For Stage 10C action tests",
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


def _make_task(
    db: Session,
    grievance: Grievance,
    assigned_to: User = None,
    policy_id: uuid.UUID = None,
    status: str = "SCHEDULED",
    due_at: datetime = None,
) -> FollowUpTask:
    now = datetime.now(timezone.utc)
    due = due_at or (now + timedelta(days=3))
    task = FollowUpTask(
        grievance_id=grievance.id,
        assigned_to_user_id=assigned_to.id if assigned_to else grievance.assigned_to_user_id,
        policy_id=policy_id,
        due_at=due,
        status=status,
    )
    db.add(task)
    db.flush()
    return task


def _auth(user: User) -> dict:
    token = create_access_token(user_id=str(user.id), role_name=user.role.name)
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# 1. Successful action recording
# ===========================================================================

@skip_if_no_db
def test_successful_action_recording(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)
    creator = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)

    policy = _make_policy(db, dept.id)
    grievance = _make_grievance(db, dept.id, creator.id)
    _make_assignment(db, grievance, officer, admin)
    task = _make_task(db, grievance, assigned_to=officer, policy_id=policy.id)

    payload = {
        "action_type": "CALL_ATTEMPTED",
        "description": "Called citizen on phone to request additional property details.",
        "outcome": "Citizen answered and will provide copies within 2 days.",
        "notes": "Citizen was cooperative.",
    }

    resp = client.post(
        f"/api/v1/follow-up-tasks/{task.id}/actions",
        json=payload,
        headers=_auth(officer),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["action_type"] == "CALL_ATTEMPTED"
    assert data["grievance_id"] == str(grievance.id)
    assert data["follow_up_task_id"] == str(task.id)
    assert data["performed_by_user_id"] == str(officer.id)
    assert "Notes: Citizen was cooperative." in data["outcome"]

    # Verify task is COMPLETED
    updated_task = db.get(FollowUpTask, task.id)
    assert updated_task.status == "COMPLETED"
    assert updated_task.completed_at is not None

    # Verify grievance status remains ASSIGNED (not resolved or closed)
    updated_grievance = db.get(Grievance, grievance.id)
    assert updated_grievance.status == "ASSIGNED"
    assert updated_grievance.last_action_at is not None


# ===========================================================================
# 2. Missing or invalid action type
# ===========================================================================

@skip_if_no_db
def test_missing_or_invalid_action_type(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)
    creator = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)

    grievance = _make_grievance(db, dept.id, creator.id)
    _make_assignment(db, grievance, officer, admin)
    task = _make_task(db, grievance, assigned_to=officer)

    # Missing action_type
    resp_missing = client.post(
        f"/api/v1/follow-up-tasks/{task.id}/actions",
        json={"description": "Missing action type"},
        headers=_auth(officer),
    )
    assert resp_missing.status_code == 422

    # Invalid action_type
    resp_invalid = client.post(
        f"/api/v1/follow-up-tasks/{task.id}/actions",
        json={"action_type": "UNKNOWN_ACTION_XYZ", "description": "Invalid action"},
        headers=_auth(officer),
    )
    assert resp_invalid.status_code == 422


# ===========================================================================
# 3. Unauthorised user
# ===========================================================================

@skip_if_no_db
def test_unauthorised_user_cannot_record_action(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)
    viewer = _make_user(db, RoleName.VIEWER, dept.id)
    deo = _make_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)

    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin)
    task = _make_task(db, grievance, assigned_to=officer)

    payload = {
        "action_type": "CALL_ATTEMPTED",
        "description": "Unauthorized attempt",
    }

    # VIEWER cannot record actions
    resp_viewer = client.post(
        f"/api/v1/follow-up-tasks/{task.id}/actions",
        json=payload,
        headers=_auth(viewer),
    )
    assert resp_viewer.status_code == 403

    # DATA_ENTRY_OPERATOR cannot record actions
    resp_deo = client.post(
        f"/api/v1/follow-up-tasks/{task.id}/actions",
        json=payload,
        headers=_auth(deo),
    )
    assert resp_deo.status_code == 403


# ===========================================================================
# 4. Cross-department access
# ===========================================================================

@skip_if_no_db
def test_cross_department_access_rejected(db: Session):
    dept1 = _make_department(db, suffix="Dept1")
    dept2 = _make_department(db, suffix="Dept2")

    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer1 = _make_user(db, RoleName.OFFICER, dept1.id)
    officer2 = _make_user(db, RoleName.OFFICER, dept2.id)

    grievance1 = _make_grievance(db, dept1.id, admin.id)
    _make_assignment(db, grievance1, officer1, admin)
    task1 = _make_task(db, grievance1, assigned_to=officer1)

    payload = {
        "action_type": "CALL_ATTEMPTED",
        "description": "Officer from dept2 attempting action on dept1 task",
    }

    resp = client.post(
        f"/api/v1/follow-up-tasks/{task1.id}/actions",
        json=payload,
        headers=_auth(officer2),
    )
    assert resp.status_code == 403
    assert "another department" in resp.json()["detail"].lower()


# ===========================================================================
# 5. Failed call attempt completing a task
# ===========================================================================

@skip_if_no_db
def test_failed_call_attempt_completes_task(db: Session):
    """
    A failed phone call still completes the scheduled follow-up task.
    The grievance must NOT be marked resolved.
    """
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin)
    task = _make_task(db, grievance, assigned_to=officer)

    payload = {
        "action_type": "CALL_ATTEMPTED",
        "description": "Dialled citizen; phone rang with no answer.",
        "outcome": "Call failed / unreachable.",
        "response_received": False,
    }

    resp = client.post(
        f"/api/v1/follow-up-tasks/{task.id}/actions",
        json=payload,
        headers=_auth(officer),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["response_received"] is False

    # Task is completed
    updated_task = db.get(FollowUpTask, task.id)
    assert updated_task.status == "COMPLETED"

    # Grievance is NOT resolved
    updated_grievance = db.get(Grievance, grievance.id)
    assert updated_grievance.status == "ASSIGNED"


# ===========================================================================
# 6. No response not resolving grievance
# ===========================================================================

@skip_if_no_db
def test_no_response_not_resolving_grievance(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin)
    task = _make_task(db, grievance, assigned_to=officer)

    payload = {
        "action_type": "NO_RESPONSE",
        "description": "Reminder letter sent; no citizen response received.",
        "response_received": False,
    }

    resp = client.post(
        f"/api/v1/follow-up-tasks/{task.id}/actions",
        json=payload,
        headers=_auth(officer),
    )
    assert resp.status_code == 201

    updated_task = db.get(FollowUpTask, task.id)
    assert updated_task.status == "COMPLETED"

    updated_grievance = db.get(Grievance, grievance.id)
    assert updated_grievance.status != "RESOLVED"
    assert updated_grievance.status != "CLOSED"


# ===========================================================================
# 7. Response received not resolving grievance
# ===========================================================================

@skip_if_no_db
def test_response_received_not_resolving_grievance(db: Session):
    """
    Substantive response received completes the follow-up task,
    but does NOT automatically resolve or close the grievance.
    """
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin)
    task = _make_task(db, grievance, assigned_to=officer)

    payload = {
        "action_type": "RESPONSE_RECEIVED",
        "description": "Citizen provided electricity bill as proof of address.",
        "outcome": "Verification underway.",
        "response_received": True,
    }

    resp = client.post(
        f"/api/v1/follow-up-tasks/{task.id}/actions",
        json=payload,
        headers=_auth(officer),
    )
    assert resp.status_code == 201
    assert resp.json()["response_received"] is True

    # Task completed
    updated_task = db.get(FollowUpTask, task.id)
    assert updated_task.status == "COMPLETED"

    # Grievance must NOT be resolved
    updated_grievance = db.get(Grievance, grievance.id)
    assert updated_grievance.status == "ASSIGNED"


# ===========================================================================
# 8. Optional next follow-up scheduling
# ===========================================================================

@skip_if_no_db
def test_optional_next_follow_up_scheduling(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin)
    task = _make_task(db, grievance, assigned_to=officer)

    next_date = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    payload = {
        "action_type": "CITIZEN_CONTACTED",
        "description": "Citizen agreed to provide documents on Monday.",
        "next_follow_up_at": next_date,
    }

    resp = client.post(
        f"/api/v1/follow-up-tasks/{task.id}/actions",
        json=payload,
        headers=_auth(officer),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["next_scheduled_task_id"] is not None

    # Verify original task is COMPLETED
    assert db.get(FollowUpTask, task.id).status == "COMPLETED"

    # Verify new task is SCHEDULED
    new_task = db.get(FollowUpTask, data["next_scheduled_task_id"])
    assert new_task is not None
    assert new_task.status == "SCHEDULED"
    assert new_task.grievance_id == grievance.id


# ===========================================================================
# 9. Existing active scheduled task conflict
# ===========================================================================

@skip_if_no_db
def test_existing_active_scheduled_task_conflict(db: Session):
    """
    If another active SCHEDULED task already exists for this grievance,
    supplying next_follow_up_at must be rejected with 409 Conflict.
    """
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin)
    task = _make_task(db, grievance, assigned_to=officer)

    # 1. Past date check returns 400
    resp_past = client.post(
        f"/api/v1/follow-up-tasks/{task.id}/actions",
        json={
            "action_type": "CALL_ATTEMPTED",
            "description": "Attempt with past date",
            "next_follow_up_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        },
        headers=_auth(officer),
    )
    assert resp_past.status_code == 400
    assert "future" in resp_past.json()["detail"].lower()

    # 2. Existing active scheduled task triggers 409 Conflict
    dummy_conflict = FollowUpTask(
        id=uuid.uuid4(),
        grievance_id=grievance.id,
        status="SCHEDULED",
        due_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    next_date = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    with patch("app.services.action_service._check_conflicting_scheduled_task", return_value=dummy_conflict):
        resp_conflict = client.post(
            f"/api/v1/follow-up-tasks/{task.id}/actions",
            json={
                "action_type": "CALL_ATTEMPTED",
                "description": "Attempt with conflicting task",
                "next_follow_up_at": next_date,
            },
            headers=_auth(officer),
        )
        assert resp_conflict.status_code == 409, resp_conflict.text
        assert "already exists" in resp_conflict.json()["detail"].lower()


# ===========================================================================
# 10. Duplicate task completion
# ===========================================================================

@skip_if_no_db
def test_duplicate_task_completion_rejected(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin)
    task = _make_task(db, grievance, assigned_to=officer)

    payload = {
        "action_type": "FIELD_VISIT",
        "description": "Visited citizen residence.",
    }

    # First attempt: succeeds (201)
    r1 = client.post(
        f"/api/v1/follow-up-tasks/{task.id}/actions",
        json=payload,
        headers=_auth(officer),
    )
    assert r1.status_code == 201

    # Second attempt on same task: rejected (400)
    r2 = client.post(
        f"/api/v1/follow-up-tasks/{task.id}/actions",
        json=payload,
        headers=_auth(officer),
    )
    assert r2.status_code == 400
    assert "COMPLETED" in r2.json()["detail"]


# ===========================================================================
# 11. Audit log creation
# ===========================================================================

@skip_if_no_db
def test_audit_log_creation(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin)
    task = _make_task(db, grievance, assigned_to=officer)

    payload = {
        "action_type": "DOCUMENTS_REQUESTED",
        "description": "Requested verified land survey map.",
    }

    resp = client.post(
        f"/api/v1/follow-up-tasks/{task.id}/actions",
        json=payload,
        headers=_auth(officer),
    )
    assert resp.status_code == 201
    action_id = resp.json()["id"]

    # Verify FOLLOW_UP_ACTION_RECORDED
    log_action = (
        db.query(ActivityLog)
        .filter_by(action="FOLLOW_UP_ACTION_RECORDED", entity_id=action_id)
        .first()
    )
    assert log_action is not None
    assert log_action.user_id == officer.id
    assert log_action.grievance_id == grievance.id

    # Verify FOLLOW_UP_TASK_COMPLETED
    log_task = (
        db.query(ActivityLog)
        .filter_by(action="FOLLOW_UP_TASK_COMPLETED", entity_id=task.id)
        .first()
    )
    assert log_task is not None
    assert log_task.user_id == officer.id


# ===========================================================================
# 12. Transaction rollback on failure
# ===========================================================================

@skip_if_no_db
def test_transaction_rollback_on_failure(db: Session):
    """
    If a failure occurs during the transaction, the task status must remain SCHEDULED
    and no FollowUpAction record must be committed.
    """
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin)
    task = _make_task(db, grievance, assigned_to=officer)

    payload = {
        "action_type": "FIELD_VISIT",
        "description": "Site visit attempted",
    }

    # Start a nested savepoint so we can rollback to it on simulated failure
    savepoint = db.begin_nested()
    try:
        with patch("app.services.action_service.log_activity", side_effect=RuntimeError("Simulated DB audit crash")):
            from app.services.action_service import record_follow_up_action
            from app.schemas.action import FollowUpActionCreate

            action_in = FollowUpActionCreate(**payload)
            record_follow_up_action(db=db, task_id=task.id, action_in=action_in, current_user=officer)
    except RuntimeError:
        savepoint.rollback()

    # Verify task in DB is still SCHEDULED
    db.refresh(task)
    assert task.status == "SCHEDULED"
    assert task.completed_at is None

    # Verify no action was committed
    actions = db.query(FollowUpAction).filter_by(follow_up_task_id=task.id).all()
    assert len(actions) == 0
