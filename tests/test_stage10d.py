"""
Stage 10D Tests: Reminder Generation and Scheduling.

Covers:
  1.  First reminder generation.
  2.  Subsequent reminder generation.
  3.  max_reminders=0 disables auto reminders.
  4.  Completed task exclusion.
  5.  Cancelled task exclusion.
  6.  Repeated processor execution is idempotent.
  7.  Idempotency key collision handled safely via atomic savepoint.
  8.  Concurrent processor execution does not create duplicate notifications.
  9.  Missing recipient logged in ActivityLog and safely handled.
  10. Inactive recipient logged in ActivityLog and safely handled.
  11. Department isolation on notification APIs.
  12. Notification acknowledgement.
  13. Invalid notification state transition.
  14. Policy update not changing existing scheduled task dates.
  15. Processor retry after failure (e.g. officer activated).
  16. UTC and timezone handling.
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
    FollowUpPolicy,
    FollowUpTask,
    Grievance,
    GrievanceAssignment,
    Notification,
    Role,
    User,
)
from app.services.notification_service import (
    acknowledge_notification,
    process_due_reminders,
    validate_notification_transition,
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


def _make_user(db: Session, role_name: str, dept_id=None, is_active: bool = True) -> User:
    role = _get_or_create_role(db, role_name)
    user = User(
        full_name=f"User {role_name} {uuid.uuid4().hex[:6]}",
        email=f"u_{uuid.uuid4().hex[:8]}@gov.in",
        password_hash=hash_password("Pass123!"),
        role_id=role.id,
        department_id=dept_id,
        is_active=is_active,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    return user


def _make_department(db: Session, suffix: str = "") -> Department:
    dept = Department(
        name=f"Dept 10D {suffix or uuid.uuid4().hex[:4]}",
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
        name=f"Policy 10D {uuid.uuid4().hex[:6]}",
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
        grievance_number=f"GRV-10D-{uuid.uuid4().hex[:8].upper()}",
        title="10D Grievance",
        description="For Stage 10D reminder tests",
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
# 1. First reminder generation
# ===========================================================================

@skip_if_no_db
def test_first_reminder_generation(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    policy = _make_policy(db, dept.id, first_reminder=3, interval=2, max_reminders=3)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)
    task = _make_task(db, grievance, assigned_to=officer, policy_id=policy.id, due_at=t0 + timedelta(days=3))

    # At T0 + 2 days: before task due_at -> no reminder
    res_early = process_due_reminders(db, now=t0 + timedelta(days=2))
    assert res_early["generated_reminders"] == 0
    notifs = db.query(Notification).filter_by(follow_up_task_id=task.id).all()
    assert len(notifs) == 0

    # At T0 + 3 days: task due_at reached -> reminder #1 generated
    res_due = process_due_reminders(db, now=t0 + timedelta(days=3))
    assert res_due["generated_reminders"] == 1

    notifs = db.query(Notification).filter_by(follow_up_task_id=task.id).all()
    assert len(notifs) == 1
    rem1 = notifs[0]
    assert rem1.notification_type == "REMINDER"
    assert rem1.channel == "IN_APP"
    assert rem1.recipient_user_id == officer.id
    assert rem1.status == "GENERATED"
    assert rem1.scheduled_at == task.due_at


# ===========================================================================
# 2. Subsequent reminder generation
# ===========================================================================

@skip_if_no_db
def test_subsequent_reminder_generation(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    policy = _make_policy(db, dept.id, first_reminder=3, interval=2, max_reminders=3)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)
    task = _make_task(db, grievance, assigned_to=officer, policy_id=policy.id, due_at=t0 + timedelta(days=3))

    # Reminder 1 generated at Day 3
    process_due_reminders(db, now=t0 + timedelta(days=3))
    assert db.query(Notification).filter_by(follow_up_task_id=task.id).count() == 1

    # At Day 4: only 1 day elapsed, interval is 2 days -> no reminder
    res_day4 = process_due_reminders(db, now=t0 + timedelta(days=4))
    assert res_day4["generated_reminders"] == 0

    # At Day 5: 2 days elapsed -> Reminder #2 generated!
    res_day5 = process_due_reminders(db, now=t0 + timedelta(days=5))
    assert res_day5["generated_reminders"] == 1
    assert db.query(Notification).filter_by(follow_up_task_id=task.id).count() == 2

    # At Day 7: 2 more days elapsed -> Reminder #3 generated!
    res_day7 = process_due_reminders(db, now=t0 + timedelta(days=7))
    assert res_day7["generated_reminders"] == 1
    assert db.query(Notification).filter_by(follow_up_task_id=task.id).count() == 3

    # At Day 9: max_reminders=3 reached -> NO more reminders generated
    res_day9 = process_due_reminders(db, now=t0 + timedelta(days=9))
    assert res_day9["generated_reminders"] == 0
    assert db.query(Notification).filter_by(follow_up_task_id=task.id).count() == 3


# ===========================================================================
# 3. max_reminders=0 disables auto reminders
# ===========================================================================

@skip_if_no_db
def test_max_reminders_zero_disables_reminders(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    policy = _make_policy(db, dept.id, first_reminder=3, interval=2, max_reminders=0)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)
    task = _make_task(db, grievance, assigned_to=officer, policy_id=policy.id, due_at=t0 + timedelta(days=3))

    # Even well past due date, max_reminders=0 prevents automatic reminders
    res = process_due_reminders(db, now=t0 + timedelta(days=10))
    assert res["generated_reminders"] == 0
    assert db.query(Notification).filter_by(follow_up_task_id=task.id).count() == 0


# ===========================================================================
# 4. Completed task exclusion
# ===========================================================================

@skip_if_no_db
def test_completed_task_exclusion(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, first_reminder=1)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)
    task = _make_task(db, grievance, assigned_to=officer, status="COMPLETED", due_at=t0 + timedelta(days=1))

    res = process_due_reminders(db, now=t0 + timedelta(days=5))
    assert res["generated_reminders"] == 0
    assert db.query(Notification).filter_by(follow_up_task_id=task.id).count() == 0


# ===========================================================================
# 5. Cancelled task exclusion
# ===========================================================================

@skip_if_no_db
def test_cancelled_task_exclusion(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, first_reminder=1)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)
    task = _make_task(db, grievance, assigned_to=officer, status="CANCELLED", due_at=t0 + timedelta(days=1))

    res = process_due_reminders(db, now=t0 + timedelta(days=5))
    assert res["generated_reminders"] == 0
    assert db.query(Notification).filter_by(follow_up_task_id=task.id).count() == 0


# ===========================================================================
# 6. Repeated processor execution is idempotent
# ===========================================================================

@skip_if_no_db
def test_repeated_processor_execution_is_idempotent(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, first_reminder=2)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)
    task = _make_task(db, grievance, assigned_to=officer, due_at=t0 + timedelta(days=2))

    eval_time = t0 + timedelta(days=2)
    # First run: 1 generated
    r1 = process_due_reminders(db, now=eval_time)
    assert r1["generated_reminders"] == 1
    assert db.query(Notification).filter_by(follow_up_task_id=task.id).count() == 1

    # Second run at same time: 0 new generated, remains exactly 1
    r2 = process_due_reminders(db, now=eval_time)
    assert r2["generated_reminders"] == 0
    assert db.query(Notification).filter_by(follow_up_task_id=task.id).count() == 1


# ===========================================================================
# 7. Idempotency key collision handled safely
# ===========================================================================

@skip_if_no_db
def test_idempotency_key_collision_handled_safely(db: Session):
    """
    If a unique constraint collision occurs in the database, the atomic savepoint
    catches it without aborting the transaction.
    """
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, first_reminder=1)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)
    task = _make_task(db, grievance, assigned_to=officer, due_at=t0 + timedelta(days=1))

    # Pre-insert a notification with the expected idempotency key directly
    # (e.g. from concurrent worker before task association was queried)
    from app.utils.notification_idempotency import build_idempotency_key
    key = build_idempotency_key(
        grievance_id=grievance.id,
        channel="IN_APP",
        notification_type="REMINDER",
        scheduled_date=(t0 + timedelta(days=1)).date(),
        follow_up_task_id=task.id,
    )
    pre_notif = Notification(
        grievance_id=grievance.id,
        follow_up_task_id=None,  # Unlinked so reminder_count remains 0
        recipient_user_id=officer.id,
        recipient_name=officer.full_name,
        recipient_contact=officer.email,
        notification_type="REMINDER",
        channel="IN_APP",
        message="Pre-inserted notification",
        idempotency_key=key,
    )
    db.add(pre_notif)
    db.flush()

    # Now run processor — should detect collision gracefully without crashing
    res = process_due_reminders(db, now=t0 + timedelta(days=1))
    assert res["generated_reminders"] == 0
    assert res["skipped_or_conflicts"] >= 1
    # Transaction remains completely functional
    assert db.get(Notification, pre_notif.id) is not None


# ===========================================================================
# 8. Concurrent processor execution
# ===========================================================================

@skip_if_no_db
def test_concurrent_processor_execution(db: Session):
    """Simulate two concurrent invocations on the same due task."""
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, first_reminder=1)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)
    task = _make_task(db, grievance, assigned_to=officer, due_at=t0 + timedelta(days=1))

    eval_time = t0 + timedelta(days=2)

    # Worker A runs
    r_a = process_due_reminders(db, now=eval_time)
    # Worker B runs
    r_b = process_due_reminders(db, now=eval_time)

    # Combined result: exactly 1 notification in database
    notifs = db.query(Notification).filter_by(follow_up_task_id=task.id).all()
    assert len(notifs) == 1
    assert r_a["generated_reminders"] + r_b["generated_reminders"] == 1


# ===========================================================================
# 9. Missing recipient logged and handled
# ===========================================================================

@skip_if_no_db
def test_missing_recipient_logged_and_handled(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)

    _make_policy(db, dept.id, first_reminder=1)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    # Create task with NO assigned recipient
    task = _make_task(db, grievance, assigned_to=None, due_at=t0 + timedelta(days=1))
    grievance.assigned_to_user_id = None
    task.assigned_to_user_id = None
    db.flush()

    res = process_due_reminders(db, now=t0 + timedelta(days=2))
    assert res["failures"] >= 1
    assert res["generated_reminders"] == 0

    # Verify audit log was recorded for failure
    audit = (
        db.query(ActivityLog)
        .filter_by(action="REMINDER_PROCESSING_FAILED", entity_id=task.id)
        .first()
    )
    assert audit is not None
    assert "recipient" in audit.description.lower()


# ===========================================================================
# 10. Inactive recipient logged and handled
# ===========================================================================

@skip_if_no_db
def test_inactive_recipient_logged_and_handled(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    # Create officer and deactivate
    inactive_officer = _make_user(db, RoleName.OFFICER, dept.id, is_active=False)

    _make_policy(db, dept.id, first_reminder=1)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, inactive_officer, admin, assigned_at=t0)
    task = _make_task(db, grievance, assigned_to=inactive_officer, due_at=t0 + timedelta(days=1))

    res = process_due_reminders(db, now=t0 + timedelta(days=2))
    assert res["failures"] >= 1
    assert res["generated_reminders"] == 0

    # Verify audit log recorded that officer is inactive
    audit = (
        db.query(ActivityLog)
        .filter_by(action="REMINDER_PROCESSING_FAILED", entity_id=task.id)
        .first()
    )
    assert audit is not None
    assert "inactive" in audit.description.lower()


# ===========================================================================
# 11. Department isolation on notifications
# ===========================================================================

@skip_if_no_db
def test_department_isolation_on_notifications(db: Session):
    dept1 = _make_department(db, suffix="Dept1")
    dept2 = _make_department(db, suffix="Dept2")

    admin1 = _make_user(db, RoleName.SUPER_ADMIN)
    officer1 = _make_user(db, RoleName.OFFICER, dept1.id)
    officer2 = _make_user(db, RoleName.OFFICER, dept2.id)

    grievance1 = _make_grievance(db, dept1.id, admin1.id)
    _make_assignment(db, grievance1, officer1, admin1)
    task1 = _make_task(db, grievance1, assigned_to=officer1)

    notif = Notification(
        grievance_id=grievance1.id,
        follow_up_task_id=task1.id,
        recipient_user_id=officer1.id,
        recipient_name=officer1.full_name,
        recipient_contact=officer1.email,
        notification_type="REMINDER",
        channel="IN_APP",
        message="Dept1 notification",
    )
    db.add(notif)
    db.flush()

    # Officer 2 (from Dept 2) attempts to read Dept 1's notification -> 403 Forbidden
    resp = client.get(
        f"/api/v1/notifications/{notif.id}",
        headers=_auth(officer2),
    )
    assert resp.status_code == 403


# ===========================================================================
# 12. Notification acknowledgement
# ===========================================================================

@skip_if_no_db
def test_notification_acknowledgement(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin)
    task = _make_task(db, grievance, assigned_to=officer)

    notif = Notification(
        grievance_id=grievance.id,
        follow_up_task_id=task.id,
        recipient_user_id=officer.id,
        recipient_name=officer.full_name,
        recipient_contact=officer.email,
        notification_type="REMINDER",
        channel="IN_APP",
        status="GENERATED",
        message="Acknowledge me",
    )
    db.add(notif)
    db.flush()

    resp = client.patch(
        f"/api/v1/notifications/{notif.id}/acknowledge",
        headers=_auth(officer),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ACKNOWLEDGED"

    # Grievance status remains ASSIGNED (acknowledging notification does not resolve grievance)
    updated_grievance = db.get(Grievance, grievance.id)
    assert updated_grievance.status == "ASSIGNED"

    # Audit log
    audit = db.query(ActivityLog).filter_by(
        action="NOTIFICATION_ACKNOWLEDGED", entity_id=notif.id
    ).first()
    assert audit is not None


# ===========================================================================
# 13. Invalid notification state transition
# ===========================================================================

@skip_if_no_db
def test_invalid_notification_state_transition(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin)

    notif = Notification(
        grievance_id=grievance.id,
        recipient_user_id=officer.id,
        recipient_name=officer.full_name,
        recipient_contact=officer.email,
        notification_type="REMINDER",
        channel="IN_APP",
        status="RESPONSE_RECEIVED",  # Terminal state
        message="Already received response",
    )
    db.add(notif)
    db.flush()

    # Attempting to acknowledge a RESPONSE_RECEIVED notification is invalid
    resp = client.patch(
        f"/api/v1/notifications/{notif.id}/acknowledge",
        headers=_auth(officer),
    )
    assert resp.status_code == 400
    assert "Invalid notification status transition" in resp.json()["detail"]


# ===========================================================================
# 14. Policy update not changing existing scheduled task dates
# ===========================================================================

@skip_if_no_db
def test_policy_update_not_changing_existing_scheduled_task_dates(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    policy = _make_policy(db, dept.id, first_reminder=3)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)
    original_due = t0 + timedelta(days=3)
    task = _make_task(db, grievance, assigned_to=officer, policy_id=policy.id, due_at=original_due)

    # Update policy to first_reminder_after_days = 7
    client.patch(
        f"/api/v1/policies/{policy.id}",
        json={"first_reminder_after_days": 7},
        headers=_auth(admin),
    )

    # Existing task due_at must remain unchanged
    db.refresh(task)
    assert task.due_at == original_due


# ===========================================================================
# 15. Processor retry after failure
# ===========================================================================

@skip_if_no_db
def test_processor_retry_after_failure(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id, is_active=False)

    _make_policy(db, dept.id, first_reminder=1)
    t0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=t0)
    task = _make_task(db, grievance, assigned_to=officer, due_at=t0 + timedelta(days=1))

    eval_time = t0 + timedelta(days=2)

    # First attempt: officer inactive -> fails
    r1 = process_due_reminders(db, now=eval_time)
    assert r1["failures"] >= 1
    assert r1["generated_reminders"] == 0

    # Officer is activated
    officer.is_active = True
    db.flush()

    # Retry processor: succeeds!
    r2 = process_due_reminders(db, now=eval_time)
    assert r2["generated_reminders"] == 1
    assert db.query(Notification).filter_by(follow_up_task_id=task.id).count() == 1


# ===========================================================================
# 16. UTC and timezone handling
# ===========================================================================

@skip_if_no_db
def test_utc_and_timezone_handling(db: Session):
    dept = _make_department(db)
    admin = _make_user(db, RoleName.SUPER_ADMIN)
    officer = _make_user(db, RoleName.OFFICER, dept.id)

    _make_policy(db, dept.id, first_reminder=1)
    # Pass naive datetime: processor should accept and convert to UTC-aware without error
    naive_t0 = datetime(2026, 11, 1, 12, 0, 0)
    grievance = _make_grievance(db, dept.id, admin.id)
    _make_assignment(db, grievance, officer, admin, assigned_at=naive_t0)
    task = _make_task(db, grievance, assigned_to=officer, due_at=naive_t0 + timedelta(days=1))

    naive_eval = datetime(2026, 11, 3, 12, 0, 0)
    res = process_due_reminders(db, now=naive_eval)
    assert res["generated_reminders"] == 1
    notif = db.query(Notification).filter_by(follow_up_task_id=task.id).first()
    assert notif is not None
    assert notif.generated_at.tzinfo is not None
