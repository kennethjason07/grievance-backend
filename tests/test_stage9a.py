"""
Stage 9A Tests:
- Department CRUD & permissions
- Duplicate code and name prevention
- Department listing scoping & cross-department access isolation
- Office CRUD & department validation
- Cross-department office creation & access rejection
- Office activation/deactivation
- Audit log verification for Department and Office operations
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
# Department Tests
# ===========================================================================

@skip_if_no_db
def test_super_admin_create_department(db: Session):
    admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(admin)

    resp = client.post(
        "/api/v1/departments",
        json={
            "name": "Transport Department",
            "code": f"TRN_{uuid.uuid4().hex[:4].upper()}",
            "description": "Public transportation management",
        },
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Transport Department"
    assert data["is_active"] is True

    # Verify audit log
    audit = db.query(ActivityLog).filter_by(
        action="DEPARTMENT_CREATED", entity_id=uuid.UUID(data["id"])
    ).first()
    assert audit is not None
    assert audit.user_id == admin.id


@skip_if_no_db
def test_non_super_admin_cannot_create_department(db: Session):
    # Department admin cannot create new government departments
    dept = Department(name=f"Dept_{uuid.uuid4().hex[:4]}", code=f"CD_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    dept_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, department_id=dept.id)
    headers = _auth_headers(dept_admin)

    resp = client.post(
        "/api/v1/departments",
        json={"name": "New Dept", "code": "NEW_DEPT"},
        headers=headers,
    )
    assert resp.status_code == 403


@skip_if_no_db
def test_duplicate_department_code_and_name_rejected(db: Session):
    admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(admin)
    code = f"DUP_{uuid.uuid4().hex[:4].upper()}"

    resp1 = client.post(
        "/api/v1/departments",
        json={"name": f"Dept {code}", "code": code},
        headers=headers,
    )
    assert resp1.status_code == 201

    # Duplicate code
    resp2 = client.post(
        "/api/v1/departments",
        json={"name": f"Different Name {code}", "code": code},
        headers=headers,
    )
    assert resp2.status_code == 409

    # Duplicate name
    resp3 = client.post(
        "/api/v1/departments",
        json={"name": f"Dept {code}", "code": f"OTHER_{uuid.uuid4().hex[:4].upper()}"},
        headers=headers,
    )
    assert resp3.status_code == 409


@skip_if_no_db
def test_department_listing_and_isolation(db: Session):
    admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers_admin = _auth_headers(admin)

    d1 = Department(name=f"Dept 1 {uuid.uuid4().hex[:4]}", code=f"D1_{uuid.uuid4().hex[:4].upper()}")
    d2 = Department(name=f"Dept 2 {uuid.uuid4().hex[:4]}", code=f"D2_{uuid.uuid4().hex[:4].upper()}")
    db.add_all([d1, d2])
    db.flush()

    dept1_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, department_id=d1.id)
    headers_dept1 = _auth_headers(dept1_admin)

    # Super admin sees all
    resp_all = client.get("/api/v1/departments", headers=headers_admin)
    assert resp_all.status_code == 200
    ids_seen = [d["id"] for d in resp_all.json()]
    assert str(d1.id) in ids_seen
    assert str(d2.id) in ids_seen

    # Dept1 admin only sees Dept 1
    resp_scoped = client.get("/api/v1/departments", headers=headers_dept1)
    assert resp_scoped.status_code == 200
    scoped_ids = [d["id"] for d in resp_scoped.json()]
    assert str(d1.id) in scoped_ids
    assert str(d2.id) not in scoped_ids


@skip_if_no_db
def test_cross_department_access_rejected(db: Session):
    d1 = Department(name=f"Dept A {uuid.uuid4().hex[:4]}", code=f"DA_{uuid.uuid4().hex[:4].upper()}")
    d2 = Department(name=f"Dept B {uuid.uuid4().hex[:4]}", code=f"DB_{uuid.uuid4().hex[:4].upper()}")
    db.add_all([d1, d2])
    db.flush()

    dept1_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, department_id=d1.id)
    headers_dept1 = _auth_headers(dept1_admin)

    # Dept 1 admin attempts to GET Dept 2 -> 403 Forbidden
    resp = client.get(f"/api/v1/departments/{d2.id}", headers=headers_dept1)
    assert resp.status_code == 403
    assert "Cross-department" in resp.json()["detail"]

    # Dept 1 admin attempts to PATCH Dept 2 -> 403 Forbidden
    resp_patch = client.patch(
        f"/api/v1/departments/{d2.id}",
        json={"name": "Hacked Dept"},
        headers=headers_dept1,
    )
    assert resp_patch.status_code == 403


@skip_if_no_db
def test_department_status_toggle_by_super_admin(db: Session):
    admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(admin)

    dept = Department(name=f"Dept Toggle {uuid.uuid4().hex[:4]}", code=f"DT_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    # Deactivate
    resp = client.patch(
        f"/api/v1/departments/{dept.id}/status",
        json={"is_active": False},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Check audit log
    audit = db.query(ActivityLog).filter_by(
        action="DEPARTMENT_STATUS_CHANGED", entity_id=dept.id
    ).first()
    assert audit is not None
    assert audit.new_values["is_active"] is False


# ===========================================================================
# Office Tests
# ===========================================================================

@skip_if_no_db
def test_create_and_get_office(db: Session):
    dept = Department(name=f"Police {uuid.uuid4().hex[:4]}", code=f"POL_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(admin)

    code = f"OFF_{uuid.uuid4().hex[:4].upper()}"
    resp = client.post(
        "/api/v1/offices",
        json={
            "department_id": str(dept.id),
            "name": "Central Police Station",
            "office_code": code,
            "district": "Bengaluru Central",
            "state": "Karnataka",
        },
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["office_code"] == code
    assert data["district"] == "Bengaluru Central"

    # Get office
    get_resp = client.get(f"/api/v1/offices/{data['id']}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Central Police Station"

    # Audit log verification
    audit = db.query(ActivityLog).filter_by(
        action="OFFICE_CREATED", entity_id=uuid.UUID(data["id"])
    ).first()
    assert audit is not None


@skip_if_no_db
def test_cross_department_office_creation_prevented(db: Session):
    d1 = Department(name=f"Dept Alpha {uuid.uuid4().hex[:4]}", code=f"DA_{uuid.uuid4().hex[:4].upper()}")
    d2 = Department(name=f"Dept Beta {uuid.uuid4().hex[:4]}", code=f"DB_{uuid.uuid4().hex[:4].upper()}")
    db.add_all([d1, d2])
    db.flush()

    dept1_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, department_id=d1.id)
    headers_dept1 = _auth_headers(dept1_admin)

    # Dept 1 admin attempts to create office in Dept 2 -> 403 Forbidden
    resp = client.post(
        "/api/v1/offices",
        json={
            "department_id": str(d2.id),
            "name": "Unauthorized Office",
            "office_code": f"BAD_{uuid.uuid4().hex[:4].upper()}",
            "district": "Mysuru",
        },
        headers=headers_dept1,
    )
    assert resp.status_code == 403


@skip_if_no_db
def test_office_update_and_status(db: Session):
    dept = Department(name=f"Revenue {uuid.uuid4().hex[:4]}", code=f"REV_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(admin)

    office = Office(
        department_id=dept.id,
        name="Taluk Office",
        office_code=f"TLK_{uuid.uuid4().hex[:4].upper()}",
        district="Mandya",
        state="Karnataka",
    )
    db.add(office)
    db.flush()

    # Update office address and district
    resp = client.patch(
        f"/api/v1/offices/{office.id}",
        json={"district": "Mandya Urban", "address": "Court Road"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["district"] == "Mandya Urban"
    assert resp.json()["address"] == "Court Road"

    # Deactivate office
    status_resp = client.patch(
        f"/api/v1/offices/{office.id}/status",
        json={"is_active": False},
        headers=headers,
    )
    assert status_resp.status_code == 200
    assert status_resp.json()["is_active"] is False

    # Check audit log
    audit = db.query(ActivityLog).filter_by(
        action="OFFICE_STATUS_CHANGED", entity_id=office.id
    ).first()
    assert audit is not None
    assert audit.new_values["is_active"] is False

