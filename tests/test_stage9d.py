"""
Stage 9D Tests: Grievance Assignment and Officer Workflows.
Covers:
1. Successful initial assignment
2. Grievance status transition to ASSIGNED
3. assigned_to_user_id updated on Grievance
4. Rejection of unconfirmed department grievance
5. Rejection of inactive officers
6. Rejection of cross-department officers
7. Rejection of ineligible user roles (e.g. VIEWER, DATA_ENTRY_OPERATOR)
8. Prevention of duplicate active assignments via /assign
9. Successful reassignment and deactivation of previous active assignment
10. Preservation of chronological assignment history
11. Rejection of reassigning to the same officer
12. Authorization guards (SUPER_ADMIN, DEPARTMENT_ADMIN, SUPERVISOR vs unauthorized roles)
13. Department isolation for assignment and workload views
14. Officer /officers/me/assignments query
15. Real-time workload calculation (/officers/me/workload and /officers/{id}/workload)
16. Comprehensive audit logging for assignments and reassignments
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
from app.models import ActivityLog, Base, Department, GrievanceAssignment, Role, User
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


def _create_confirmed_grievance(db: Session, dept: Department, super_admin: User) -> Grievance:
    cr = client.post(
        "/api/v1/grievances",
        json={
            "title": f"Grievance in {dept.name}",
            "description": "Standard citizen complaint for testing assignment workflows.",
            "suggested_department_id": str(dept.id),
        },
        headers=_auth_headers(super_admin),
    )
    grv_id = cr.json()["id"]
    client.post(
        f"/api/v1/grievances/{grv_id}/confirm-department",
        json={"department_id": str(dept.id), "confirmation_notes": "Confirmed for assignment test."},
        headers=_auth_headers(super_admin),
    )
    return db.get(Grievance, grv_id)


# ===========================================================================
# 1. Initial Assignment Tests
# ===========================================================================

@skip_if_no_db
def test_successful_initial_assignment(db: Session):
    dept = Department(name="Revenue Dept", code=f"REV_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    supervisor = _create_user(db, RoleName.SUPERVISOR, dept.id)
    officer = _create_user(db, RoleName.OFFICER, dept.id)

    grievance = _create_confirmed_grievance(db, dept, super_admin)

    # Supervisor assigns grievance to officer
    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/assign",
        json={"officer_id": str(officer.id), "remarks": "Investigate revenue discrepancy immediately."},
        headers=_auth_headers(supervisor),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["assigned_to_user_id"] == str(officer.id)
    assert data["assigned_by_user_id"] == str(supervisor.id)
    assert data["is_active"] is True
    assert data["remarks"] == "Investigate revenue discrepancy immediately."

    # Grievance record verification
    db.refresh(grievance)
    assert grievance.assigned_to_user_id == officer.id
    assert grievance.status == "ASSIGNED"
    assert grievance.last_action_at is not None

    # Audit log verification
    audit = db.query(ActivityLog).filter_by(action="GRIEVANCE_ASSIGNED", grievance_id=grievance.id).first()
    assert audit is not None
    assert audit.new_values["assigned_to_user_id"] == str(officer.id)
    assert audit.new_values["status"] == "ASSIGNED"


@skip_if_no_db
def test_assignment_rejects_unconfirmed_grievance(db: Session):
    dept = Department(name="Transport Dept", code=f"TR_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer = _create_user(db, RoleName.OFFICER, dept.id)

    # Register grievance without confirming department
    cr = client.post(
        "/api/v1/grievances",
        json={"title": "Bus route issue", "description": "Buses skipping stop.", "suggested_department_id": str(dept.id)},
        headers=_auth_headers(super_admin),
    )
    grv_id = cr.json()["id"]

    resp = client.post(
        f"/api/v1/grievances/{grv_id}/assign",
        json={"officer_id": str(officer.id)},
        headers=_auth_headers(super_admin),
    )
    assert resp.status_code == 400
    assert "confirmed" in resp.json()["detail"].lower()


@skip_if_no_db
def test_assignment_rejects_inactive_officer(db: Session):
    dept = Department(name="Health Dept", code=f"HL_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    inactive_officer = _create_user(db, RoleName.OFFICER, dept.id, is_active=False)
    grievance = _create_confirmed_grievance(db, dept, super_admin)

    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/assign",
        json={"officer_id": str(inactive_officer.id)},
        headers=_auth_headers(super_admin),
    )
    assert resp.status_code == 400
    assert "inactive" in resp.json()["detail"].lower()


@skip_if_no_db
def test_assignment_rejects_officer_from_another_department(db: Session):
    dept1 = Department(name="Dept A", code=f"DA_{uuid.uuid4().hex[:4].upper()}")
    dept2 = Department(name="Dept B", code=f"DB_{uuid.uuid4().hex[:4].upper()}")
    db.add_all([dept1, dept2])
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer_dept2 = _create_user(db, RoleName.OFFICER, dept2.id)
    grievance = _create_confirmed_grievance(db, dept1, super_admin)

    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/assign",
        json={"officer_id": str(officer_dept2.id)},
        headers=_auth_headers(super_admin),
    )
    assert resp.status_code == 400
    assert "does not belong" in resp.json()["detail"].lower()


@skip_if_no_db
def test_assignment_rejects_ineligible_roles(db: Session):
    dept = Department(name="Police Dept", code=f"POL_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    viewer = _create_user(db, RoleName.VIEWER, dept.id)
    grievance = _create_confirmed_grievance(db, dept, super_admin)

    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/assign",
        json={"officer_id": str(viewer.id)},
        headers=_auth_headers(super_admin),
    )
    assert resp.status_code == 400
    assert "not eligible" in resp.json()["detail"].lower()


@skip_if_no_db
def test_duplicate_active_assignment_prevented(db: Session):
    dept = Department(name="PWD Dept", code=f"PWD_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer1 = _create_user(db, RoleName.OFFICER, dept.id)
    officer2 = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_confirmed_grievance(db, dept, super_admin)

    # First assignment succeeds
    r1 = client.post(
        f"/api/v1/grievances/{grievance.id}/assign",
        json={"officer_id": str(officer1.id)},
        headers=_auth_headers(super_admin),
    )
    assert r1.status_code == 200

    # Second assignment via /assign must fail (should use /reassign)
    r2 = client.post(
        f"/api/v1/grievances/{grievance.id}/assign",
        json={"officer_id": str(officer2.id)},
        headers=_auth_headers(super_admin),
    )
    assert r2.status_code == 400
    assert "already actively assigned" in r2.json()["detail"].lower()


# ===========================================================================
# 2. Reassignment Tests
# ===========================================================================

@skip_if_no_db
def test_successful_reassignment_and_history(db: Session):
    dept = Department(name="Water Dept", code=f"WAT_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    dept_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, dept.id)
    officer1 = _create_user(db, RoleName.OFFICER, dept.id)
    officer2 = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_confirmed_grievance(db, dept, super_admin)

    # Initial assignment to officer1
    client.post(
        f"/api/v1/grievances/{grievance.id}/assign",
        json={"officer_id": str(officer1.id), "remarks": "Initial assignment."},
        headers=_auth_headers(dept_admin),
    )

    # Reassign to officer2
    reassign_resp = client.post(
        f"/api/v1/grievances/{grievance.id}/reassign",
        json={
            "officer_id": str(officer2.id),
            "reassignment_reason": "Officer 1 is on extended medical leave.",
        },
        headers=_auth_headers(dept_admin),
    )
    assert reassign_resp.status_code == 200, reassign_resp.text
    reassign_data = reassign_resp.json()

    assert reassign_data["assigned_to_user_id"] == str(officer2.id)
    assert reassign_data["is_active"] is True
    assert reassign_data["remarks"] == "Officer 1 is on extended medical leave."

    # Verify database state
    db.refresh(grievance)
    assert grievance.assigned_to_user_id == officer2.id

    # Check assignment history
    hist_resp = client.get(
        f"/api/v1/grievances/{grievance.id}/assignments",
        headers=_auth_headers(dept_admin),
    )
    assert hist_resp.status_code == 200
    hist = hist_resp.json()
    assert hist["total_assignments"] == 2
    assert hist["history"][0]["assigned_to_user_id"] == str(officer2.id)
    assert hist["history"][0]["is_active"] is True
    assert hist["history"][1]["assigned_to_user_id"] == str(officer1.id)
    assert hist["history"][1]["is_active"] is False
    assert hist["history"][1]["unassigned_at"] is not None

    # Check audit log for reassignment
    audit = db.query(ActivityLog).filter_by(action="GRIEVANCE_REASSIGNED", grievance_id=grievance.id).first()
    assert audit is not None
    assert audit.old_values["assigned_to_user_id"] == str(officer1.id)
    assert audit.new_values["assigned_to_user_id"] == str(officer2.id)


@skip_if_no_db
def test_reassign_same_officer_rejected(db: Session):
    dept = Department(name="Forestry Dept", code=f"FOR_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_confirmed_grievance(db, dept, super_admin)

    client.post(
        f"/api/v1/grievances/{grievance.id}/assign",
        json={"officer_id": str(officer.id)},
        headers=_auth_headers(super_admin),
    )

    # Reassigning to the same officer
    resp = client.post(
        f"/api/v1/grievances/{grievance.id}/reassign",
        json={"officer_id": str(officer.id), "reassignment_reason": "Redundant assignment test."},
        headers=_auth_headers(super_admin),
    )
    assert resp.status_code == 400
    assert "already assigned to this officer" in resp.json()["detail"].lower()


# ===========================================================================
# 3. Authorization & Department Isolation Tests
# ===========================================================================

@skip_if_no_db
def test_unauthorized_users_cannot_assign(db: Session):
    dept = Department(name="Finance Dept", code=f"FIN_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer = _create_user(db, RoleName.OFFICER, dept.id)
    deo = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_confirmed_grievance(db, dept, super_admin)

    # Officer cannot assign grievances
    r_off = client.post(
        f"/api/v1/grievances/{grievance.id}/assign",
        json={"officer_id": str(officer.id)},
        headers=_auth_headers(officer),
    )
    assert r_off.status_code == 403

    # DEO cannot assign grievances
    r_deo = client.post(
        f"/api/v1/grievances/{grievance.id}/assign",
        json={"officer_id": str(officer.id)},
        headers=_auth_headers(deo),
    )
    assert r_deo.status_code == 403


@skip_if_no_db
def test_department_isolation_on_assignment(db: Session):
    dept1 = Department(name="Dept 1", code=f"D1_{uuid.uuid4().hex[:4].upper()}")
    dept2 = Department(name="Dept 2", code=f"D2_{uuid.uuid4().hex[:4].upper()}")
    db.add_all([dept1, dept2])
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    dept2_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, dept2.id)
    officer_dept1 = _create_user(db, RoleName.OFFICER, dept1.id)
    grievance_dept1 = _create_confirmed_grievance(db, dept1, super_admin)

    # dept2_admin attempts to assign grievance belonging to dept1
    resp = client.post(
        f"/api/v1/grievances/{grievance_dept1.id}/assign",
        json={"officer_id": str(officer_dept1.id)},
        headers=_auth_headers(dept2_admin),
    )
    assert resp.status_code == 403
    assert "cannot assign grievances belonging to another department" in resp.json()["detail"].lower()


# ===========================================================================
# 4. Officer Workflow & Workload Tests
# ===========================================================================

@skip_if_no_db
def test_officer_me_assignments_and_workload(db: Session):
    dept = Department(name="Energy Dept", code=f"NRG_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer1 = _create_user(db, RoleName.OFFICER, dept.id)
    officer2 = _create_user(db, RoleName.OFFICER, dept.id)

    g1 = _create_confirmed_grievance(db, dept, super_admin)
    g2 = _create_confirmed_grievance(db, dept, super_admin)
    g3 = _create_confirmed_grievance(db, dept, super_admin)

    # Assign g1 & g2 to officer1; g3 to officer2
    client.post(f"/api/v1/grievances/{g1.id}/assign", json={"officer_id": str(officer1.id)}, headers=_auth_headers(super_admin))
    client.post(f"/api/v1/grievances/{g2.id}/assign", json={"officer_id": str(officer1.id)}, headers=_auth_headers(super_admin))
    client.post(f"/api/v1/grievances/{g3.id}/assign", json={"officer_id": str(officer2.id)}, headers=_auth_headers(super_admin))

    # Officer 1 checks /officers/me/assignments
    me_resp = client.get("/api/v1/officers/me/assignments", headers=_auth_headers(officer1))
    assert me_resp.status_code == 200
    items = me_resp.json()["items"]
    assert len(items) == 2
    assert {it["id"] for it in items} == {str(g1.id), str(g2.id)}

    # Officer 1 checks /officers/me/workload
    wl_resp = client.get("/api/v1/officers/me/workload", headers=_auth_headers(officer1))
    assert wl_resp.status_code == 200
    wl = wl_resp.json()
    assert wl["officer_id"] == str(officer1.id)
    assert wl["assigned_count"] == 2
    assert wl["total_active_workload"] == 2


@skip_if_no_db
def test_supervisor_workload_department_isolation(db: Session):
    dept1 = Department(name="Agri Dept", code=f"AG_{uuid.uuid4().hex[:4].upper()}")
    dept2 = Department(name="Seri Dept", code=f"SE_{uuid.uuid4().hex[:4].upper()}")
    db.add_all([dept1, dept2])
    db.flush()

    supervisor_dept1 = _create_user(db, RoleName.SUPERVISOR, dept1.id)
    officer_dept1 = _create_user(db, RoleName.OFFICER, dept1.id)
    officer_dept2 = _create_user(db, RoleName.OFFICER, dept2.id)

    # Supervisor can view officer in their department
    r1 = client.get(f"/api/v1/officers/{officer_dept1.id}/workload", headers=_auth_headers(supervisor_dept1))
    assert r1.status_code == 200

    # Supervisor cannot view officer in another department
    r2 = client.get(f"/api/v1/officers/{officer_dept2.id}/workload", headers=_auth_headers(supervisor_dept1))
    assert r2.status_code == 403
    assert "belongs to another department" in r2.json()["detail"].lower()

