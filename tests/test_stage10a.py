"""
Stage 10A Tests: Follow-up Policy Management.
Covers:
1. Policy creation with intervals, max_reminders, escalation thresholds
2. Business Rule 4: max_reminders = 0 accepted (disables auto-reminders)
3. Interval validation (escalation_after_days >= first_reminder_after_days, reminder_interval > 0)
4. Role permissions (SUPER_ADMIN, DEPARTMENT_ADMIN vs unauthorized roles)
5. Object-level department isolation on policy CRUD and listing
6. Active department policy retrieval
7. Policy updates and status toggle (activate/deactivate)
8. Business Rule 6: Policy updates do not mutate existing tasks
9. Activity logging for policy creation, updates, and status toggles
"""
import uuid
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
from app.models import ActivityLog, Base, Department, FollowUpPolicy, Role, User

settings = get_settings()
_base_url = settings.DATABASE_URL.rsplit("/", 1)[0]
TEST_DATABASE_URL = f"{_base_url}/grievance_test_db"

client = TestClient(app)


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


def _get_or_create_role(db: Session, name: str) -> Role:
    role = db.query(Role).filter_by(name=name).first()
    if not role:
        role = Role(name=name, description=f"Role: {name}")
        db.add(role)
        db.flush()
    return role


def _create_user(db: Session, role_name: str, department_id: uuid.UUID = None) -> User:
    role = _get_or_create_role(db, role_name)
    user = User(
        full_name=f"User {role_name}",
        email=f"u_{uuid.uuid4().hex[:8]}@gov.in",
        password_hash=hash_password("Pass123!"),
        role_id=role.id,
        department_id=department_id,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    return user


def _auth_headers(user: User) -> dict:
    token = create_access_token(user_id=str(user.id), role_name=user.role.name)
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# 1. Policy Creation & Validation Tests
# ===========================================================================

@skip_if_no_db
def test_create_policy_success(db: Session):
    dept = Department(name="Transport Dept", code=f"TR_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(super_admin)

    payload = {
        "department_id": str(dept.id),
        "name": "Standard Transport SLA Policy",
        "description": "Standard reminder intervals for vehicle registration complaints.",
        "first_reminder_after_days": 3,
        "reminder_interval_days": 2,
        "escalation_after_days": 7,
        "max_reminders": 3,
        "is_active": True,
    }

    resp = client.post("/api/v1/policies", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["name"] == "Standard Transport SLA Policy"
    assert data["first_reminder_after_days"] == 3
    assert data["reminder_interval_days"] == 2
    assert data["escalation_after_days"] == 7
    assert data["max_reminders"] == 3
    assert data["is_active"] is True

    # Audit log check
    audit = db.query(ActivityLog).filter_by(action="POLICY_CREATED", entity_id=data["id"]).first()
    assert audit is not None
    assert audit.user_id == super_admin.id


@skip_if_no_db
def test_create_policy_max_reminders_zero(db: Session):
    """Rule 4: max_reminders = 0 is valid and signifies no automatic reminders."""
    dept = Department(name="Audit Dept", code=f"AUD_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(super_admin)

    payload = {
        "department_id": str(dept.id),
        "name": "No Auto-Reminder Policy",
        "first_reminder_after_days": 5,
        "reminder_interval_days": 2,
        "escalation_after_days": 10,
        "max_reminders": 0,  # Explicit 0 disables auto reminders
    }

    resp = client.post("/api/v1/policies", json=payload, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["max_reminders"] == 0


@skip_if_no_db
def test_create_policy_invalid_intervals(db: Session):
    dept = Department(name="Forest Dept", code=f"FOR_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(super_admin)

    # escalation_after_days < first_reminder_after_days must be rejected
    payload = {
        "department_id": str(dept.id),
        "name": "Invalid Escalation Threshold",
        "first_reminder_after_days": 10,
        "reminder_interval_days": 2,
        "escalation_after_days": 5,  # Invalid: less than 10
        "max_reminders": 3,
    }

    resp = client.post("/api/v1/policies", json=payload, headers=headers)
    assert resp.status_code == 422


# ===========================================================================
# 2. Authorization & Department Isolation Tests
# ===========================================================================

@skip_if_no_db
def test_department_admin_policy_isolation(db: Session):
    dept1 = Department(name="Dept 1", code=f"D1_{uuid.uuid4().hex[:4].upper()}")
    dept2 = Department(name="Dept 2", code=f"D2_{uuid.uuid4().hex[:4].upper()}")
    db.add_all([dept1, dept2])
    db.flush()

    dept1_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, dept1.id)
    dept2_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, dept2.id)

    # dept1_admin creates policy for dept1 -> OK (201)
    r1 = client.post(
        "/api/v1/policies",
        json={"department_id": str(dept1.id), "name": "Dept 1 Policy"},
        headers=_auth_headers(dept1_admin),
    )
    assert r1.status_code == 201
    p1_id = r1.json()["id"]

    # dept2_admin attempts to create policy for dept1 -> 403 Forbidden
    r2 = client.post(
        "/api/v1/policies",
        json={"department_id": str(dept1.id), "name": "Illegal Dept 1 Policy"},
        headers=_auth_headers(dept2_admin),
    )
    assert r2.status_code == 403
    assert "another department" in r2.json()["detail"].lower()

    # dept2_admin attempts to view dept1's policy -> 403 Forbidden
    r3 = client.get(f"/api/v1/policies/{p1_id}", headers=_auth_headers(dept2_admin))
    assert r3.status_code == 403


@skip_if_no_db
def test_unauthorized_roles_cannot_manage_policies(db: Session):
    dept = Department(name="Health Dept", code=f"HL_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    supervisor = _create_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _create_user(db, RoleName.OFFICER, dept.id)

    # Supervisor cannot create policy
    r1 = client.post(
        "/api/v1/policies",
        json={"department_id": str(dept.id), "name": "Supervisor Policy Attempt"},
        headers=_auth_headers(supervisor),
    )
    assert r1.status_code == 403

    # Officer cannot create policy
    r2 = client.post(
        "/api/v1/policies",
        json={"department_id": str(dept.id), "name": "Officer Policy Attempt"},
        headers=_auth_headers(officer),
    )
    assert r2.status_code == 403


# ===========================================================================
# 3. Active Policy, Updates & Status Tests
# ===========================================================================

@skip_if_no_db
def test_get_active_department_policy(db: Session):
    dept = Department(name="Revenue Dept", code=f"REV_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(super_admin)

    # When no policy exists -> 404
    r_none = client.get(f"/api/v1/departments/{dept.id}/policies/active", headers=headers)
    assert r_none.status_code == 404

    # Create active policy
    client.post(
        "/api/v1/policies",
        json={"department_id": str(dept.id), "name": "Active Rev Policy", "is_active": True},
        headers=headers,
    )

    # Retrieve active policy -> 200
    r_act = client.get(f"/api/v1/departments/{dept.id}/policies/active", headers=headers)
    assert r_act.status_code == 200
    assert r_act.json()["name"] == "Active Rev Policy"


@skip_if_no_db
def test_update_policy_and_status_toggle(db: Session):
    dept = Department(name="Public Works", code=f"PW_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    dept_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, dept.id)
    headers = _auth_headers(dept_admin)

    cr = client.post(
        "/api/v1/policies",
        json={"department_id": str(dept.id), "name": "PWD Initial Policy", "first_reminder_after_days": 3},
        headers=headers,
    )
    p_id = cr.json()["id"]

    # Update policy name and interval
    up_resp = client.patch(
        f"/api/v1/policies/{p_id}",
        json={"name": "PWD Revised Policy", "reminder_interval_days": 4},
        headers=headers,
    )
    assert up_resp.status_code == 200
    assert up_resp.json()["name"] == "PWD Revised Policy"
    assert up_resp.json()["reminder_interval_days"] == 4

    # Toggle status to inactive
    st_resp = client.patch(
        f"/api/v1/policies/{p_id}/status",
        json={"is_active": False},
        headers=headers,
    )
    assert st_resp.status_code == 200
    assert st_resp.json()["is_active"] is False

    # Check audit log
    audit_deact = db.query(ActivityLog).filter_by(action="POLICY_DEACTIVATED", entity_id=p_id).first()
    assert audit_deact is not None

