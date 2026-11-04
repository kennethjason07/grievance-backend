"""
Stage 9B Tests:
- Administrator-controlled user provisioning (no self-registration)
- SUPER_ADMIN cross-department provisioning
- DEPARTMENT_ADMIN department-scoped provisioning
- Privilege escalation guard: DEPARTMENT_ADMIN cannot create/assign SUPER_ADMIN
- Self-modification guards: Cannot change own role or deactivate own account
- Office-to-department consistency validation
- Unique email enforcement (409 Conflict)
- Password hashing verification (no password_hash in response)
- Department-scoped listing, filtering, pagination
- Object-level authorization & cross-department access rejection
- Audit logging for user creation, updates, status toggle, and role change
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
from app.models import ActivityLog, Base, Department, Office, Role, User

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
# 1. User Provisioning & Permissions
# ===========================================================================

@skip_if_no_db
def test_super_admin_provision_user(db: Session):
    dept = Department(name=f"Dept_{uuid.uuid4().hex[:4]}", code=f"D_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer_role = _get_or_create_role(db, RoleName.OFFICER)
    headers = _auth_headers(super_admin)

    email = f"officer_{uuid.uuid4().hex[:6]}@police.gov.in"
    resp = client.post(
        "/api/v1/users",
        json={
            "full_name": "Rajesh Kumar",
            "email": email,
            "password": "SecurePassword123!",
            "phone": "9876543210",
            "department_id": str(dept.id),
            "role_id": str(officer_role.id),
        },
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == email
    assert data["full_name"] == "Rajesh Kumar"
    assert data["role_name"] == RoleName.OFFICER
    assert "password" not in data
    assert "password_hash" not in data

    # Audit log check
    audit = db.query(ActivityLog).filter_by(
        action="USER_CREATED", entity_id=uuid.UUID(data["id"])
    ).first()
    assert audit is not None
    assert audit.user_id == super_admin.id


@skip_if_no_db
def test_dept_admin_cannot_escalate_to_super_admin(db: Session):
    dept = Department(name=f"Dept_{uuid.uuid4().hex[:4]}", code=f"D_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    dept_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, department_id=dept.id)
    super_admin_role = _get_or_create_role(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(dept_admin)

    resp = client.post(
        "/api/v1/users",
        json={
            "full_name": "Attempted SuperAdmin",
            "email": f"bad_admin_{uuid.uuid4().hex[:6]}@gov.in",
            "password": "SecurePassword123!",
            "department_id": str(dept.id),
            "role_id": str(super_admin_role.id),
        },
        headers=headers,
    )
    assert resp.status_code == 403
    assert "Only a SUPER_ADMIN can assign the SUPER_ADMIN role" in resp.json()["detail"]


@skip_if_no_db
def test_dept_admin_cross_department_provisioning_prevented(db: Session):
    d1 = Department(name=f"Dept 1 {uuid.uuid4().hex[:4]}", code=f"D1_{uuid.uuid4().hex[:4].upper()}")
    d2 = Department(name=f"Dept 2 {uuid.uuid4().hex[:4]}", code=f"D2_{uuid.uuid4().hex[:4].upper()}")
    db.add_all([d1, d2])
    db.flush()

    dept1_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, department_id=d1.id)
    officer_role = _get_or_create_role(db, RoleName.OFFICER)
    headers = _auth_headers(dept1_admin)

    # Attempt to create user in Dept 2
    resp = client.post(
        "/api/v1/users",
        json={
            "full_name": "Cross Dept User",
            "email": f"cross_{uuid.uuid4().hex[:6]}@gov.in",
            "password": "SecurePassword123!",
            "department_id": str(d2.id),
            "role_id": str(officer_role.id),
        },
        headers=headers,
    )
    assert resp.status_code == 403
    assert "outside your authorized department" in resp.json()["detail"]


@skip_if_no_db
def test_office_department_consistency_enforced(db: Session):
    d1 = Department(name=f"Dept Alpha {uuid.uuid4().hex[:4]}", code=f"DA_{uuid.uuid4().hex[:4].upper()}")
    d2 = Department(name=f"Dept Beta {uuid.uuid4().hex[:4]}", code=f"DB_{uuid.uuid4().hex[:4].upper()}")
    db.add_all([d1, d2])
    db.flush()

    # Office belongs to Dept 2
    off2 = Office(
        department_id=d2.id,
        name="Office in Dept 2",
        office_code=f"OFF2_{uuid.uuid4().hex[:4].upper()}",
        district="Bengaluru",
        state="Karnataka",
    )
    db.add(off2)
    db.flush()

    admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer_role = _get_or_create_role(db, RoleName.OFFICER)
    headers = _auth_headers(admin)

    # Attempt to assign office of Dept 2 to a user of Dept 1
    resp = client.post(
        "/api/v1/users",
        json={
            "full_name": "Mismatched User",
            "email": f"mismatch_{uuid.uuid4().hex[:6]}@gov.in",
            "password": "SecurePassword123!",
            "department_id": str(d1.id),
            "office_id": str(off2.id),
            "role_id": str(officer_role.id),
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert "Selected office does not belong to the user's department" in resp.json()["detail"]


@skip_if_no_db
def test_duplicate_email_rejected(db: Session):
    dept = Department(name=f"Dept_{uuid.uuid4().hex[:4]}", code=f"D_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer_role = _get_or_create_role(db, RoleName.OFFICER)
    headers = _auth_headers(admin)
    shared_email = f"shared_{uuid.uuid4().hex[:6]}@police.gov.in"

    resp1 = client.post(
        "/api/v1/users",
        json={
            "full_name": "User One",
            "email": shared_email,
            "password": "SecurePassword123!",
            "department_id": str(dept.id),
            "role_id": str(officer_role.id),
        },
        headers=headers,
    )
    assert resp1.status_code == 201

    # Second user with same email (even with different case)
    resp2 = client.post(
        "/api/v1/users",
        json={
            "full_name": "User Two",
            "email": shared_email.upper(),
            "password": "SecurePassword123!",
            "department_id": str(dept.id),
            "role_id": str(officer_role.id),
        },
        headers=headers,
    )
    assert resp2.status_code == 409
    assert "already in use" in resp2.json()["detail"]


# ===========================================================================
# 2. User Listing, Scoping & Get Details
# ===========================================================================

@skip_if_no_db
def test_user_listing_department_isolation(db: Session):
    d1 = Department(name=f"Dept X {uuid.uuid4().hex[:4]}", code=f"DX_{uuid.uuid4().hex[:4].upper()}")
    d2 = Department(name=f"Dept Y {uuid.uuid4().hex[:4]}", code=f"DY_{uuid.uuid4().hex[:4].upper()}")
    db.add_all([d1, d2])
    db.flush()

    u1 = _create_user(db, RoleName.OFFICER, department_id=d1.id)
    u2 = _create_user(db, RoleName.OFFICER, department_id=d2.id)

    dept1_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, department_id=d1.id)
    super_admin = _create_user(db, RoleName.SUPER_ADMIN)

    # Super admin lists all or filters
    resp_super = client.get("/api/v1/users", headers=_auth_headers(super_admin))
    assert resp_super.status_code == 200
    ids_super = [u["id"] for u in resp_super.json()]
    assert str(u1.id) in ids_super
    assert str(u2.id) in ids_super

    # Dept 1 admin only sees users in Dept 1
    resp_dept1 = client.get("/api/v1/users", headers=_auth_headers(dept1_admin))
    assert resp_dept1.status_code == 200
    ids_dept1 = [u["id"] for u in resp_dept1.json()]
    assert str(u1.id) in ids_dept1
    assert str(u2.id) not in ids_dept1

    # Cross-department GET user/{id} rejected
    resp_cross = client.get(f"/api/v1/users/{u2.id}", headers=_auth_headers(dept1_admin))
    assert resp_cross.status_code == 403


# ===========================================================================
# 3. User Updates, Self-Modification Guards & Role Changes
# ===========================================================================

@skip_if_no_db
def test_admin_cannot_deactivate_self(db: Session):
    admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(admin)

    resp = client.patch(
        f"/api/v1/users/{admin.id}/status",
        json={"is_active": False},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "cannot deactivate their own account" in resp.json()["detail"]


@skip_if_no_db
def test_admin_cannot_change_own_role(db: Session):
    admin = _create_user(db, RoleName.SUPER_ADMIN)
    viewer_role = _get_or_create_role(db, RoleName.VIEWER)
    headers = _auth_headers(admin)

    resp = client.patch(
        f"/api/v1/users/{admin.id}/role",
        json={"role_id": str(viewer_role.id)},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "cannot modify their own role" in resp.json()["detail"]


@skip_if_no_db
def test_user_update_and_role_change_with_audit(db: Session):
    dept = Department(name=f"Police {uuid.uuid4().hex[:4]}", code=f"POL_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    admin = _create_user(db, RoleName.SUPER_ADMIN)
    officer = _create_user(db, RoleName.OFFICER, department_id=dept.id)
    supervisor_role = _get_or_create_role(db, RoleName.SUPERVISOR)
    headers = _auth_headers(admin)

    # 1. Update profile
    resp_update = client.patch(
        f"/api/v1/users/{officer.id}",
        json={"full_name": "Inspector Sharma", "phone": "9999888877"},
        headers=headers,
    )
    assert resp_update.status_code == 200
    assert resp_update.json()["full_name"] == "Inspector Sharma"

    # 2. Promote to Supervisor
    resp_role = client.patch(
        f"/api/v1/users/{officer.id}/role",
        json={"role_id": str(supervisor_role.id)},
        headers=headers,
    )
    assert resp_role.status_code == 200
    assert resp_role.json()["role_name"] == RoleName.SUPERVISOR

    # 3. Deactivate user
    resp_status = client.patch(
        f"/api/v1/users/{officer.id}/status",
        json={"is_active": False},
        headers=headers,
    )
    assert resp_status.status_code == 200
    assert resp_status.json()["is_active"] is False

    # Verify audit logs for role and status changes
    audit_role = db.query(ActivityLog).filter_by(
        action="USER_ROLE_CHANGED", entity_id=officer.id
    ).first()
    assert audit_role is not None
    assert audit_role.new_values["role"] == RoleName.SUPERVISOR

    audit_status = db.query(ActivityLog).filter_by(
        action="USER_STATUS_CHANGED", entity_id=officer.id
    ).first()
    assert audit_status is not None
    assert audit_status.new_values["is_active"] is False

