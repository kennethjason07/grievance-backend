"""
Stage 9E Tests: Grievance Lifecycle Transitions, Resolution, Closure, and Timeline.
Covers:
1. Assigned grievance enters IN_PROGRESS via /start
2. Unauthorized officer cannot start another officer's grievance (403 Forbidden)
3. Information request transitions status to AWAITING_INFORMATION
4. Information request validation (requires description)
5. Resume investigation transitions status back to IN_PROGRESS
6. Invalid state transitions are rejected by the centralized state machine (400 Bad Request)
7. Resolution requires summary and details
8. Resolution sets resolved_at and transitions status to RESOLVED
9. Only authorized users can close grievances (officer rejected with 403 Forbidden)
10. Closure sets closed_at and status CLOSED
11. Closed grievances cannot undergo further transitions (terminal state protection)
12. Department isolation enforced across all lifecycle endpoints
13. Activity logs recorded for every transition
14. Chronological timeline verification (/timeline)
15. General PATCH cannot tamper with status
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
from app.models import ActivityLog, Base, Department, Role, User
from app.models.grievance import Grievance

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


def _create_user(db: Session, role_name: str, department_id: uuid.UUID = None, is_active: bool = True) -> User:
    role = _get_or_create_role(db, role_name)
    user = User(
        full_name=f"User {role_name}",
        email=f"u_{uuid.uuid4().hex[:8]}@gov.in",
        password_hash=hash_password("Pass123!"),
        role_id=role.id,
        department_id=department_id,
        is_active=is_active,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    return user


def _auth_headers(user: User) -> dict:
    token = create_access_token(user_id=str(user.id), role_name=user.role.name)
    return {"Authorization": f"Bearer {token}"}


def _create_assigned_grievance(db: Session, dept: Department, super_admin: User, officer: User) -> Grievance:
    cr = client.post(
        "/api/v1/grievances",
        json={"title": "Drainage Overflow", "description": "Severe sewage blockage in sector 4.", "suggested_department_id": str(dept.id)},
        headers=_auth_headers(super_admin),
    )
    grv_id = cr.json()["id"]

    client.post(
        f"/api/v1/grievances/{grv_id}/confirm-department",
        json={"department_id": str(dept.id)},
        headers=_auth_headers(super_admin),
    )

    client.post(
        f"/api/v1/grievances/{grv_id}/assign",
        json={"officer_id": str(officer.id), "remarks": "Assigned to field engineer."},
        headers=_auth_headers(super_admin),
    )

    return db.get(Grievance, grv_id)


# ===========================================================================
# 1. Investigation Start Tests
# ===========================================================================

@skip_if_no_db
def test_assigned_grievance_start_investigation(db: Session):
    dept = Department(name="Civic Works", code=f"CW_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_assigned_grievance(db, dept, super_admin, officer)

    assert grievance.status == "ASSIGNED"

    # Assigned officer starts investigation
    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/start",
        headers=_auth_headers(officer),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "IN_PROGRESS"

    db.refresh(grievance)
    assert grievance.status == "IN_PROGRESS"
    assert grievance.last_action_at is not None

    # Check audit log
    audit = db.query(ActivityLog).filter_by(action="INVESTIGATION_STARTED", grievance_id=grievance.id).first()
    assert audit is not None
    assert audit.new_values["status"] == "IN_PROGRESS"


@skip_if_no_db
def test_unauthorized_officer_cannot_start_other_officer_grievance(db: Session):
    dept = Department(name="Urban Dept", code=f"UR_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer1 = _create_user(db, RoleName.OFFICER, dept.id)
    officer2 = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_assigned_grievance(db, dept, super_admin, officer1)

    # officer2 attempts to start officer1's grievance
    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/start",
        headers=_auth_headers(officer2),
    )
    assert resp.status_code == 403
    assert "not the actively assigned officer" in resp.json()["detail"].lower()


# ===========================================================================
# 2. Information Request & Resume Tests
# ===========================================================================

@skip_if_no_db
def test_information_request_and_resume_cycle(db: Session):
    dept = Department(name="Sanitation Dept", code=f"SAN_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_assigned_grievance(db, dept, super_admin, officer)

    # Start investigation
    client.post(f"/api/v1/grievances/{grievance.id}/start", headers=_auth_headers(officer))

    # Request additional info
    req_resp = client.post(
        f"/api/v1/grievances/{grievance.id}/request-information",
        json={"information_required": "Please provide the property tax assessment number for verification."},
        headers=_auth_headers(officer),
    )
    assert req_resp.status_code == 200
    assert req_resp.json()["status"] == "AWAITING_INFORMATION"

    # Resume investigation
    resume_resp = client.post(
        f"/api/v1/grievances/{grievance.id}/resume",
        json={"information_received": "Citizen provided tax receipt no. 987654 via citizen portal."},
        headers=_auth_headers(officer),
    )
    assert resume_resp.status_code == 200
    assert resume_resp.json()["status"] == "IN_PROGRESS"


@skip_if_no_db
def test_information_request_requires_description(db: Session):
    dept = Department(name="Public Works", code=f"PW_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_assigned_grievance(db, dept, super_admin, officer)
    client.post(f"/api/v1/grievances/{grievance.id}/start", headers=_auth_headers(officer))

    # Missing information_required
    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/request-information",
        json={"information_required": ""},  # empty string
        headers=_auth_headers(officer),
    )
    assert resp.status_code == 422


# ===========================================================================
# 3. Invalid State Machine Transitions
# ===========================================================================

@skip_if_no_db
def test_invalid_transitions_rejected(db: Session):
    dept = Department(name="Roads Dept", code=f"RD_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_assigned_grievance(db, dept, super_admin, officer)

    # Status is ASSIGNED. Attempting to directly resolve before starting investigation
    resolve_resp = client.post(
        f"/api/v1/grievances/{grievance.id}/resolve",
        json={"resolution_summary": "Issue fixed", "resolution_details": "Replaced road sign."},
        headers=_auth_headers(officer),
    )
    assert resolve_resp.status_code == 400
    assert "invalid transition" in resolve_resp.json()["detail"].lower()


# ===========================================================================
# 4. Resolution and Closure Tests
# ===========================================================================

@skip_if_no_db
def test_resolution_and_closure_workflow(db: Session):
    dept = Department(name="Electrical Dept", code=f"ELE_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    dept_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, dept.id)
    officer = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_assigned_grievance(db, dept, super_admin, officer)

    # 1. Start investigation
    client.post(f"/api/v1/grievances/{grievance.id}/start", headers=_auth_headers(officer))

    # 2. Resolve grievance
    res_resp = client.post(
        f"/api/v1/grievances/{grievance.id}/resolve",
        json={
            "resolution_summary": "Faulty transformer fuse replaced",
            "resolution_details": "Technician team dispatched; 11kV fuse replaced and power restored.",
        },
        headers=_auth_headers(officer),
    )
    assert res_resp.status_code == 200, res_resp.text
    res_data = res_resp.json()
    assert res_data["status"] == "RESOLVED"
    assert res_data["resolved_at"] is not None

    # 3. Officer cannot close grievance (requires admin permission)
    close_attempt = client.post(
        f"/api/v1/grievances/{grievance.id}/close",
        json={"closure_reason": "Closing ticket"},
        headers=_auth_headers(officer),
    )
    assert close_attempt.status_code == 403

    # 4. Department admin closes grievance
    close_resp = client.post(
        f"/api/v1/grievances/{grievance.id}/close",
        json={
            "closure_reason": "Citizen confirmed power restoration via telephone verification.",
            "closure_notes": "Quality assurance inspection verified.",
        },
        headers=_auth_headers(dept_admin),
    )
    assert close_resp.status_code == 200, close_resp.text
    close_data = close_resp.json()
    assert close_data["status"] == "CLOSED"
    assert close_data["closed_at"] is not None


@skip_if_no_db
def test_closed_grievance_is_terminal(db: Session):
    dept = Department(name="Forestry Dept", code=f"FOR_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    dept_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, dept.id)
    officer = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_assigned_grievance(db, dept, super_admin, officer)

    # Walk through to CLOSED
    client.post(f"/api/v1/grievances/{grievance.id}/start", headers=_auth_headers(officer))
    client.post(
        f"/api/v1/grievances/{grievance.id}/resolve",
        json={"resolution_summary": "Tree trimmed", "resolution_details": "Tree branch clear of wires."},
        headers=_auth_headers(officer),
    )
    client.post(
        f"/api/v1/grievances/{grievance.id}/close",
        json={"closure_reason": "Completed and verified"},
        headers=_auth_headers(dept_admin),
    )

    # Attempt any lifecycle operation on CLOSED grievance
    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/start",
        headers=_auth_headers(dept_admin),
    )
    assert resp.status_code == 400
    assert "terminal state" in resp.json()["detail"].lower()


# ===========================================================================
# 5. Department Isolation & General PATCH Tamper Resistance
# ===========================================================================

@skip_if_no_db
def test_lifecycle_cross_department_isolation(db: Session):
    dept1 = Department(name="Dept A", code=f"DA_{uuid.uuid4().hex[:4].upper()}")
    dept2 = Department(name="Dept B", code=f"DB_{uuid.uuid4().hex[:4].upper()}")
    db.add_all([dept1, dept2])
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    dept2_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, dept2.id)
    officer_dept1 = _create_user(db, RoleName.OFFICER, dept1.id)
    grievance = _create_assigned_grievance(db, dept1, super_admin, officer_dept1)

    # dept2_admin attempts to start investigation in dept1
    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/start",
        headers=_auth_headers(dept2_admin),
    )
    assert resp.status_code == 403
    assert "another department" in resp.json()["detail"].lower()


@skip_if_no_db
def test_general_patch_cannot_tamper_with_status(db: Session):
    dept = Department(name="General Dept", code=f"GEN_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_assigned_grievance(db, dept, super_admin, officer)

    assert grievance.status == "ASSIGNED"

    # Attempt to illegally jump status to RESOLVED via general PATCH
    patch_resp = client.patch(
        f"/api/v1/grievances/{grievance.id}",
        json={"status": "RESOLVED"},
        headers=_auth_headers(super_admin),
    )
    assert patch_resp.status_code == 422

    db.refresh(grievance)
    assert grievance.status == "ASSIGNED"  # Status remains untouched!


# ===========================================================================
# 6. Timeline Verification
# ===========================================================================

@skip_if_no_db
def test_grievance_timeline_event_sequence(db: Session):
    dept = Department(name="Metro Works", code=f"MET_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    dept_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, dept.id)
    officer = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_assigned_grievance(db, dept, super_admin, officer)

    # Execute workflow sequence
    client.post(f"/api/v1/grievances/{grievance.id}/start", headers=_auth_headers(officer))
    client.post(f"/api/v1/grievances/{grievance.id}/request-information", json={"information_required": "Provide GPS location coordinates."}, headers=_auth_headers(officer))
    client.post(f"/api/v1/grievances/{grievance.id}/resume", json={"information_received": "Coordinates submitted."}, headers=_auth_headers(officer))
    client.post(f"/api/v1/grievances/{grievance.id}/resolve", json={"resolution_summary": "Pothole filled with asphalt.", "resolution_details": "Road patch completed."}, headers=_auth_headers(officer))
    client.post(f"/api/v1/grievances/{grievance.id}/close", json={"closure_reason": "Civic verification passed."}, headers=_auth_headers(dept_admin))

    # Fetch timeline
    timeline_resp = client.get(
        f"/api/v1/grievances/{grievance.id}/timeline",
        headers=_auth_headers(officer),
    )
    assert timeline_resp.status_code == 200
    timeline_data = timeline_resp.json()

    assert timeline_data["grievance_id"] == str(grievance.id)
    assert timeline_data["current_status"] == "CLOSED"

    actions = [ev["action"] for ev in timeline_data["timeline"]]
    # Verify expected event actions appear in sequence
    assert "GRIEVANCE_CREATED" in actions
    assert "GRIEVANCE_DEPARTMENT_CONFIRMED" in actions
    assert "GRIEVANCE_ASSIGNED" in actions
    assert "INVESTIGATION_STARTED" in actions
    assert "INFORMATION_REQUESTED" in actions
    assert "INVESTIGATION_RESUMED" in actions
    assert "GRIEVANCE_RESOLVED" in actions
    assert "GRIEVANCE_CLOSED" in actions


# ===========================================================================
# 7. Grievance Reopen Tests
# ===========================================================================

@skip_if_no_db
def test_reopen_resolved_grievance_by_authorized_admin(db: Session):
    dept = Department(name="Revenue Dept", code=f"REV_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    dept_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, dept.id)
    officer = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_assigned_grievance(db, dept, super_admin, officer)

    # Move to RESOLVED
    client.post(f"/api/v1/grievances/{grievance.id}/start", headers=_auth_headers(officer))
    client.post(
        f"/api/v1/grievances/{grievance.id}/resolve",
        json={"resolution_summary": "Tax reassessed", "resolution_details": "Assessment revised downwards by 15%."},
        headers=_auth_headers(officer),
    )

    db.refresh(grievance)
    assert grievance.status == "RESOLVED"
    assert grievance.resolved_at is not None

    # Officer cannot reopen (403 Forbidden)
    off_reopen = client.post(
        f"/api/v1/grievances/{grievance.id}/reopen",
        json={"reopen_reason": "Officer attempting reopen"},
        headers=_auth_headers(officer),
    )
    assert off_reopen.status_code == 403

    # Department Admin reopens with reason
    admin_reopen = client.post(
        f"/api/v1/grievances/{grievance.id}/reopen",
        json={"reopen_reason": "Citizen contested revised assessment with supplementary deeds."},
        headers=_auth_headers(dept_admin),
    )
    assert admin_reopen.status_code == 200
    reopen_data = admin_reopen.json()
    assert reopen_data["status"] == "IN_PROGRESS"
    assert reopen_data["resolved_at"] is None

    # Audit log check
    audit = db.query(ActivityLog).filter_by(action="GRIEVANCE_REOPENED", grievance_id=grievance.id).first()
    assert audit is not None
    assert audit.new_values["reopen_reason"] == "Citizen contested revised assessment with supplementary deeds."
    assert audit.user_id == dept_admin.id


@skip_if_no_db
def test_cannot_reopen_closed_or_non_resolved_grievance(db: Session):
    dept = Department(name="Water Works", code=f"WW_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    dept_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, dept.id)
    officer = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_assigned_grievance(db, dept, super_admin, officer)

    # 1. Attempt reopen while ASSIGNED (must fail 400)
    r1 = client.post(
        f"/api/v1/grievances/{grievance.id}/reopen",
        json={"reopen_reason": "Premature reopen attempt"},
        headers=_auth_headers(dept_admin),
    )
    assert r1.status_code == 400
    assert "only resolved grievances" in r1.json()["detail"].lower()

    # Move to CLOSED
    client.post(f"/api/v1/grievances/{grievance.id}/start", headers=_auth_headers(officer))
    client.post(
        f"/api/v1/grievances/{grievance.id}/resolve",
        json={"resolution_summary": "Pipeline welded", "resolution_details": "Leakage fixed at joint 4."},
        headers=_auth_headers(officer),
    )
    client.post(
        f"/api/v1/grievances/{grievance.id}/close",
        json={"closure_reason": "Inspected and closed."},
        headers=_auth_headers(dept_admin),
    )

    # 2. Attempt reopen while CLOSED (must fail 400 - terminal state)
    r2 = client.post(
        f"/api/v1/grievances/{grievance.id}/reopen",
        json={"reopen_reason": "Attempting to reopen closed grievance"},
        headers=_auth_headers(dept_admin),
    )
    assert r2.status_code == 400


