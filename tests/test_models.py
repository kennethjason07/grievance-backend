"""
Database model tests for the Government Grievance Tracking System.

Strategy:
- Uses a separate grievance_test_db database to isolate from development data.
- If grievance_test_db is unavailable the tests are skipped gracefully.
- All tests are wrapped in transactions that roll back after each test,
  so the test database is never left in a dirty state.
- No test data or schema mutations survive a test run.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models import (
    ActivityLog,
    Base,
    Category,
    Department,
    Escalation,
    FollowUpAction,
    FollowUpPolicy,
    FollowUpTask,
    Grievance,
    GrievanceAssignment,
    MessageTemplate,
    Notification,
    Office,
    Role,
    User,
)

# ---------------------------------------------------------------------------
# Test database configuration
# ---------------------------------------------------------------------------

settings = get_settings()

# Derive test database URL by replacing the database name with grievance_test_db
_base_url = settings.DATABASE_URL.rsplit("/", 1)[0]
TEST_DATABASE_URL = f"{_base_url}/grievance_test_db"


def _test_engine_available() -> bool:
    """Check whether grievance_test_db is reachable."""
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
    reason="grievance_test_db is not available. Create it and re-run.",
)


@pytest.fixture(scope="session")
def test_engine():
    """Create SQLAlchemy engine bound to grievance_test_db and provision schema."""
    if not db_available:
        pytest.skip("grievance_test_db unavailable")
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db(test_engine) -> Generator[Session, None, None]:
    """
    Provide a per-test database session wrapped in a savepoint.
    All changes are rolled back after each test — the schema is never dirtied.
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    # Use SAVEPOINT so nested transaction rollbacks work correctly
    session.begin_nested()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Shared helper factories
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def make_department(db: Session, **kwargs) -> Department:
    defaults = dict(name="Police Department", code="POL", is_active=True)
    defaults.update(kwargs)
    dept = Department(**defaults)
    db.add(dept)
    db.flush()
    return dept


def make_role(db: Session, name: str = "OFFICER") -> Role:
    role = Role(name=name, description=f"Role: {name}")
    db.add(role)
    db.flush()
    return role


def make_office(db: Session, department: Department, **kwargs) -> Office:
    defaults = dict(
        department_id=department.id,
        name="Central Police Station",
        office_code=f"OFF-{uuid.uuid4().hex[:6].upper()}",
        district="Bengaluru Urban",
        state="Karnataka",
    )
    defaults.update(kwargs)
    office = Office(**defaults)
    db.add(office)
    db.flush()
    return office


def make_user(db: Session, role: Role, department: Department, **kwargs) -> User:
    defaults = dict(
        full_name="Test Officer",
        email=f"officer_{uuid.uuid4().hex[:8]}@gov.in",
        password_hash="$2b$12$hashedvalue",
        department_id=department.id,
        role_id=role.id,
        is_active=True,
        is_verified=True,
    )
    defaults.update(kwargs)
    user = User(**defaults)
    db.add(user)
    db.flush()
    return user


def make_grievance(db: Session, created_by_user: User, **kwargs) -> Grievance:
    defaults = dict(
        grievance_number=f"GRV-{uuid.uuid4().hex[:8].upper()}",
        title="Test Grievance",
        description="A test grievance description.",
        source="ONLINE_PORTAL",
        original_language="en",
        status="NEW",
        priority="MEDIUM",
        created_by=created_by_user.id,
        date_received=_now(),
    )
    defaults.update(kwargs)
    grievance = Grievance(**defaults)
    db.add(grievance)
    db.flush()
    return grievance


def make_policy(db: Session, department: Department, **kwargs) -> FollowUpPolicy:
    defaults = dict(
        department_id=department.id,
        name="Standard Policy",
        first_reminder_after_days=3,
        reminder_interval_days=2,
        escalation_after_days=7,
        max_reminders=3,
        is_active=True,
    )
    defaults.update(kwargs)
    policy = FollowUpPolicy(**defaults)
    db.add(policy)
    db.flush()
    return policy


# ===========================================================================
# Test 1: Database connectivity
# ===========================================================================
@skip_if_no_db
def test_database_connectivity(test_engine):
    with test_engine.connect() as conn:
        result = conn.execute(text("SELECT 1")).scalar()
    assert result == 1


# ===========================================================================
# Test 2: Department creation
# ===========================================================================
@skip_if_no_db
def test_department_creation(db: Session):
    dept = make_department(db, name="Revenue Department", code="REV")
    assert dept.id is not None
    assert dept.name == "Revenue Department"
    assert dept.code == "REV"
    assert dept.is_active is True
    assert dept.created_at is not None


# ===========================================================================
# Test 3: Office-to-department relationship
# ===========================================================================
@skip_if_no_db
def test_office_department_relationship(db: Session):
    dept = make_department(db, name="Health Department", code="HLT")
    office = make_office(db, department=dept, name="City Health Office", district="Mysuru")
    db.refresh(dept)
    db.refresh(office)
    assert office.department_id == dept.id
    assert office.department.code == "HLT"
    assert any(o.id == office.id for o in dept.offices)


# ===========================================================================
# Test 4: User-to-role relationship
# ===========================================================================
@skip_if_no_db
def test_user_role_relationship(db: Session):
    dept = make_department(db)
    role = make_role(db, "SUPERVISOR")
    user = make_user(db, role=role, department=dept)
    db.refresh(user)
    assert user.role.name == "SUPERVISOR"
    assert user.department.code == "POL"


# ===========================================================================
# Test 5: Grievance creation
# ===========================================================================
@skip_if_no_db
def test_grievance_creation(db: Session):
    dept = make_department(db)
    role = make_role(db)
    user = make_user(db, role=role, department=dept)
    grv = make_grievance(db, created_by_user=user, title="Road Damage Near Station")
    assert grv.id is not None
    assert grv.grievance_number.startswith("GRV-")
    assert grv.status == "NEW"
    assert grv.priority == "MEDIUM"
    assert grv.resolved_at is None
    assert grv.closed_at is None


# ===========================================================================
# Test 6: Unique grievance number constraint
# ===========================================================================
@skip_if_no_db
def test_unique_grievance_number(db: Session):
    dept = make_department(db)
    role = make_role(db)
    user = make_user(db, role=role, department=dept)
    grv_num = "GRV-UNIQUE-001"
    make_grievance(db, created_by_user=user, grievance_number=grv_num)
    with pytest.raises(IntegrityError):
        make_grievance(db, created_by_user=user, grievance_number=grv_num)


# ===========================================================================
# Test 7: Assignment history is preserved on reassignment
# ===========================================================================
@skip_if_no_db
def test_assignment_history_preserved(db: Session):
    dept = make_department(db)
    role = make_role(db)
    user1 = make_user(db, role=role, department=dept, email="u1@test.gov")
    user2 = make_user(db, role=role, department=dept, email="u2@test.gov")
    grv = make_grievance(db, created_by_user=user1)

    a1 = GrievanceAssignment(
        grievance_id=grv.id,
        assigned_to_user_id=user1.id,
        assigned_by_user_id=user1.id,
        is_active=True,
    )
    db.add(a1)
    db.flush()

    # Close first assignment
    a1.is_active = False
    a1.unassigned_at = _now()
    db.flush()

    # Create second assignment
    a2 = GrievanceAssignment(
        grievance_id=grv.id,
        assigned_to_user_id=user2.id,
        assigned_by_user_id=user1.id,
        is_active=True,
    )
    db.add(a2)
    db.flush()

    db.refresh(grv)
    all_assignments = grv.assignments
    assert len(all_assignments) == 2
    active = [a for a in all_assignments if a.is_active]
    assert len(active) == 1
    assert active[0].assigned_to_user_id == user2.id


# ===========================================================================
# Test 8: One active assignment constraint
# ===========================================================================
@skip_if_no_db
def test_single_active_assignment_constraint(db: Session):
    dept = make_department(db)
    role = make_role(db)
    user1 = make_user(db, role=role, department=dept, email="ua@test.gov")
    user2 = make_user(db, role=role, department=dept, email="ub@test.gov")
    grv = make_grievance(db, created_by_user=user1)

    db.add(GrievanceAssignment(
        grievance_id=grv.id,
        assigned_to_user_id=user1.id,
        assigned_by_user_id=user1.id,
        is_active=True,
    ))
    db.flush()

    with pytest.raises(IntegrityError):
        db.add(GrievanceAssignment(
            grievance_id=grv.id,
            assigned_to_user_id=user2.id,
            assigned_by_user_id=user1.id,
            is_active=True,
        ))
        db.flush()


# ===========================================================================
# Test 9: Follow-up task creation
# ===========================================================================
@skip_if_no_db
def test_follow_up_task_creation(db: Session):
    dept = make_department(db)
    role = make_role(db)
    user = make_user(db, role=role, department=dept)
    grv = make_grievance(db, created_by_user=user)
    policy = make_policy(db, department=dept)

    task = FollowUpTask(
        grievance_id=grv.id,
        assigned_to_user_id=user.id,
        policy_id=policy.id,
        due_at=_now() + timedelta(days=3),
        status="SCHEDULED",
    )
    db.add(task)
    db.flush()
    assert task.id is not None
    assert task.status == "SCHEDULED"
    assert task.completed_at is None


# ===========================================================================
# Test 10: Duplicate active follow-up task prevention (partial unique index)
# ===========================================================================
@skip_if_no_db
def test_duplicate_active_task_prevented(db: Session):
    dept = make_department(db)
    role = make_role(db)
    user = make_user(db, role=role, department=dept)
    grv = make_grievance(db, created_by_user=user)
    policy = make_policy(db, department=dept)

    db.add(FollowUpTask(
        grievance_id=grv.id,
        assigned_to_user_id=user.id,
        policy_id=policy.id,
        due_at=_now() + timedelta(days=3),
        status="SCHEDULED",
    ))
    db.flush()

    with pytest.raises(IntegrityError):
        db.add(FollowUpTask(
            grievance_id=grv.id,
            assigned_to_user_id=user.id,
            policy_id=policy.id,
            due_at=_now() + timedelta(days=5),
            status="SCHEDULED",
        ))
        db.flush()


# ===========================================================================
# Test 11: Follow-up action creation
# ===========================================================================
@skip_if_no_db
def test_follow_up_action_creation(db: Session):
    dept = make_department(db)
    role = make_role(db)
    user = make_user(db, role=role, department=dept)
    grv = make_grievance(db, created_by_user=user)

    action = FollowUpAction(
        grievance_id=grv.id,
        performed_by_user_id=user.id,
        action_type="CALL_ATTEMPTED",
        description="Called citizen, no answer.",
        response_received=False,
        action_taken_at=_now(),
    )
    db.add(action)
    db.flush()
    assert action.id is not None
    assert action.action_type == "CALL_ATTEMPTED"
    assert action.response_received is False


# ===========================================================================
# Test 12: Notification status lifecycle — GENERATED is not SENT
# ===========================================================================
@skip_if_no_db
def test_notification_status_lifecycle(db: Session):
    dept = make_department(db)
    role = make_role(db)
    user = make_user(db, role=role, department=dept)
    grv = make_grievance(db, created_by_user=user)

    notif = Notification(
        grievance_id=grv.id,
        recipient_name="Ramesh Kumar",
        recipient_contact="9876543210",
        notification_type="REMINDER",
        channel="SMS",
        message="Follow up reminder for your grievance.",
        status="GENERATED",
        idempotency_key=f"idem-{uuid.uuid4().hex}",
    )
    db.add(notif)
    db.flush()
    assert notif.status == "GENERATED"
    assert notif.sent_at is None
    assert notif.delivered_at is None

    # Progress to SENT
    notif.status = "SENT"
    notif.sent_at = _now()
    db.flush()
    assert notif.status == "SENT"
    assert notif.delivered_at is None


# ===========================================================================
# Test 13: Escalation record creation
# ===========================================================================
@skip_if_no_db
def test_escalation_creation(db: Session):
    dept = make_department(db)
    role = make_role(db)
    supervisor = make_user(db, role=role, department=dept, email="sup@test.gov")
    officer = make_user(db, role=role, department=dept, email="off@test.gov")
    grv = make_grievance(db, created_by_user=supervisor)

    esc = Escalation(
        grievance_id=grv.id,
        escalated_from_user_id=officer.id,
        escalated_to_user_id=supervisor.id,
        reason="No response from officer in 7 days.",
        trigger_date=_now(),
        status="PENDING",
    )
    db.add(esc)
    db.flush()
    assert esc.id is not None
    assert esc.status == "PENDING"
    assert esc.resolved_at is None
    assert esc.resolution_notes is None


# ===========================================================================
# Test 14: Foreign-key integrity — office must belong to a valid department
# ===========================================================================
@skip_if_no_db
def test_foreign_key_integrity(db: Session):
    fake_dept_id = uuid.uuid4()
    office = Office(
        department_id=fake_dept_id,
        name="Ghost Office",
        office_code=f"GHO-{uuid.uuid4().hex[:6].upper()}",
        district="Unknown",
    )
    db.add(office)
    with pytest.raises(IntegrityError):
        db.flush()


# ===========================================================================
# Test 15: Activity log / audit record creation
# ===========================================================================
@skip_if_no_db
def test_audit_log_creation(db: Session):
    dept = make_department(db)
    role = make_role(db)
    user = make_user(db, role=role, department=dept)
    grv = make_grievance(db, created_by_user=user)

    log = ActivityLog(
        grievance_id=grv.id,
        user_id=user.id,
        action="GRIEVANCE_CREATED",
        entity_type="Grievance",
        entity_id=grv.id,
        old_values=None,
        new_values={"status": "NEW", "grievance_number": grv.grievance_number},
        description="Grievance created via ONLINE_PORTAL.",
        ip_address="192.168.1.100",
    )
    db.add(log)
    db.flush()
    assert log.id is not None
    assert log.action == "GRIEVANCE_CREATED"
    assert log.new_values["status"] == "NEW"
    assert log.old_values is None

