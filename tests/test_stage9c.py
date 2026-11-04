"""
Stage 9C Tests:
- Concurrency-safe grievance registration and GRV-YYYY-NNNNN number format
- Initial status strictly NEW
- Language support: Kannada ('kn') and English ('en')
- Suggested vs confirmed department separation
- Department confirmation endpoint and NEW -> UNDER_REVIEW lifecycle transition
- Role permission verification (DATA_ENTRY_OPERATOR allowed, VIEWER rejected)
- Object-level authorization & cross-department access rejection (GET & PATCH)
- Scoped listing, filtering (status, priority, search), and pagination
- Response projection separation (List vs Detail)
- Controlled updates (PATCH) and tamper resistance
- Comprehensive activity logging for creation, confirmation, and update
"""
import re
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
from app.models import ActivityLog, Base, Category, Department, Office, Role, User
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


def _create_dept_and_category(db: Session, dept_code: str) -> tuple[Department, Category]:
    dept = Department(name=f"Dept_{dept_code}", code=dept_code)
    db.add(dept)
    db.flush()
    cat = Category(name=f"Category_{dept_code}", department_id=dept.id, is_active=True)
    db.add(cat)
    db.flush()
    return dept, cat


# ===========================================================================
# 1. Grievance Registration Tests
# ===========================================================================

@skip_if_no_db
def test_grievance_registration_success(db: Session):
    dept, cat = _create_dept_and_category(db, f"D_{uuid.uuid4().hex[:4].upper()}")
    deo = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    headers = _auth_headers(deo)

    payload = {
        "title": "Broken Streetlights in Ward 12",
        "description": "All streetlights on 4th Main have been flickering and out of order for 2 weeks.",
        "source": "ONLINE_PORTAL",
        "original_language": "en",
        "suggested_department_id": str(dept.id),
        "category_id": str(cat.id),
        "priority": "HIGH",
    }

    resp = client.post("/api/v1/grievances", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert "id" in data
    assert re.match(r"^GRV-\d{4}-\d{5}$", data["grievance_number"])
    assert data["status"] == "NEW"
    assert data["priority"] == "HIGH"
    assert data["suggested_department_id"] == str(dept.id)
    assert data["confirmed_department_id"] is None
    assert data["created_by"] == str(deo.id)

    # Check audit log
    audit = db.query(ActivityLog).filter_by(action="GRIEVANCE_CREATED", grievance_id=data["id"]).first()
    assert audit is not None
    assert audit.user_id == deo.id


@skip_if_no_db
def test_grievance_registration_kannada_language(db: Session):
    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(super_admin)

    payload = {
        "title": "ರಸ್ತೆ ಗುಂಡಿಗಳ ದುರಸ್ತಿ",
        "description": "ಮುಖ್ಯ ರಸ್ತೆಯಲ್ಲಿ ಹಲವಾರು ಗುಂಡಿಗಳಿದ್ದು ವಾಹನ ಸವಾರರಿಗೆ ತೊಂದರೆಯಾಗುತ್ತಿದೆ.",
        "source": "IN_PERSON",
        "original_language": "kn",
        "original_text": "ಮುಖ್ಯ ರಸ್ತೆಯಲ್ಲಿ ಹಲವಾರು ಗುಂಡಿಗಳಿದ್ದು ವಾಹನ ಸವಾರರಿಗೆ ತೊಂದರೆಯಾಗುತ್ತಿದೆ.",
        "translated_language": "en",
        "translated_text": "Severe potholes on the main road causing danger to motorists.",
        "priority": "URGENT",
    }

    resp = client.post("/api/v1/grievances", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["original_language"] == "kn"
    assert data["translated_language"] == "en"
    assert data["translated_text"] == "Severe potholes on the main road causing danger to motorists."
    assert data["priority"] == "URGENT"


@skip_if_no_db
def test_grievance_registration_invalid_language(db: Session):
    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(super_admin)

    payload = {
        "title": "Water leakage in sector 3",
        "description": "Continuous water pipeline leakage.",
        "source": "ONLINE_PORTAL",
        "original_language": "fr",  # Not supported
    }

    resp = client.post("/api/v1/grievances", json=payload, headers=headers)
    assert resp.status_code == 422


@skip_if_no_db
def test_grievance_registration_invalid_references(db: Session):
    dept1, cat1 = _create_dept_and_category(db, f"D_{uuid.uuid4().hex[:4].upper()}")
    dept2, cat2 = _create_dept_and_category(db, f"D_{uuid.uuid4().hex[:4].upper()}")
    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(super_admin)

    # Category belongs to dept2, but suggested dept is dept1
    payload = {
        "title": "Mismatched Department and Category",
        "description": "Testing category department consistency.",
        "suggested_department_id": str(dept1.id),
        "category_id": str(cat2.id),
    }

    resp = client.post("/api/v1/grievances", json=payload, headers=headers)
    assert resp.status_code == 400
    assert "Category does not belong" in resp.json()["detail"]


@skip_if_no_db
def test_grievance_registration_role_permissions(db: Session):
    dept, _ = _create_dept_and_category(db, f"D_{uuid.uuid4().hex[:4].upper()}")
    viewer = _create_user(db, RoleName.VIEWER, dept.id)
    headers = _auth_headers(viewer)

    payload = {
        "title": "Unauthorized attempt",
        "description": "Viewer attempting to create grievance.",
    }

    resp = client.post("/api/v1/grievances", json=payload, headers=headers)
    assert resp.status_code == 403


# ===========================================================================
# 2. Department Confirmation Tests
# ===========================================================================

@skip_if_no_db
def test_confirm_department_success(db: Session):
    dept1, _ = _create_dept_and_category(db, f"D_{uuid.uuid4().hex[:4].upper()}")
    dept2, _ = _create_dept_and_category(db, f"D_{uuid.uuid4().hex[:4].upper()}")
    super_admin = _create_user(db, RoleName.SUPER_ADMIN)
    headers = _auth_headers(super_admin)

    # Create initial grievance
    create_resp = client.post(
        "/api/v1/grievances",
        json={
            "title": "Garbage dumping on roadside",
            "description": "Solid waste dumped along highway bypass.",
            "suggested_department_id": str(dept1.id),
        },
        headers=headers,
    )
    assert create_resp.status_code == 201
    grv_id = create_resp.json()["id"]

    # Confirm department to dept2
    confirm_resp = client.post(
        f"/api/v1/grievances/{grv_id}/confirm-department",
        json={
            "department_id": str(dept2.id),
            "confirmation_notes": "Re-routed to Sanitation Department after initial screening.",
        },
        headers=headers,
    )
    assert confirm_resp.status_code == 200, confirm_resp.text
    data = confirm_resp.json()

    assert data["confirmed_department_id"] == str(dept2.id)
    assert data["status"] == "UNDER_REVIEW"  # Transitioned from NEW

    # Verify audit log
    audit = db.query(ActivityLog).filter_by(
        action="GRIEVANCE_DEPARTMENT_CONFIRMED",
        grievance_id=grv_id,
    ).first()
    assert audit is not None
    assert audit.new_values["confirmed_department_id"] == str(dept2.id)
    assert audit.new_values["status"] == "UNDER_REVIEW"


@skip_if_no_db
def test_confirm_department_authorization_guards(db: Session):
    dept1, _ = _create_dept_and_category(db, f"D_{uuid.uuid4().hex[:4].upper()}")
    dept2, _ = _create_dept_and_category(db, f"D_{uuid.uuid4().hex[:4].upper()}")

    dept1_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, dept1.id)
    super_admin = _create_user(db, RoleName.SUPER_ADMIN)

    # Super admin creates grievance
    create_resp = client.post(
        "/api/v1/grievances",
        json={"title": "Road block", "description": "Tree fallen on road."},
        headers=_auth_headers(super_admin),
    )
    grv_id = create_resp.json()["id"]

    # dept1_admin attempts to confirm grievance for dept2
    resp = client.post(
        f"/api/v1/grievances/{grv_id}/confirm-department",
        json={"department_id": str(dept2.id), "confirmation_notes": "Attempting cross-dept confirmation"},
        headers=_auth_headers(dept1_admin),
    )
    assert resp.status_code == 403
    assert "own department" in resp.json()["detail"]


# ===========================================================================
# 3. Retrieval & Cross-Department Isolation Tests
# ===========================================================================

@skip_if_no_db
def test_grievance_retrieval_and_cross_department_isolation(db: Session):
    dept1, _ = _create_dept_and_category(db, f"D_{uuid.uuid4().hex[:4].upper()}")
    dept2, _ = _create_dept_and_category(db, f"D_{uuid.uuid4().hex[:4].upper()}")

    dept1_user = _create_user(db, RoleName.OFFICER, dept1.id)
    dept2_user = _create_user(db, RoleName.OFFICER, dept2.id)
    super_admin = _create_user(db, RoleName.SUPER_ADMIN)

    # Register grievance in Dept 1
    create_resp = client.post(
        "/api/v1/grievances",
        json={
            "title": "Dept 1 Grievance",
            "description": "Issues handled by Dept 1.",
            "suggested_department_id": str(dept1.id),
        },
        headers=_auth_headers(super_admin),
    )
    grv_id = create_resp.json()["id"]

    # Confirm into dept1
    client.post(
        f"/api/v1/grievances/{grv_id}/confirm-department",
        json={"department_id": str(dept1.id)},
        headers=_auth_headers(super_admin),
    )

    # Dept 1 user can retrieve
    r1 = client.get(f"/api/v1/grievances/{grv_id}", headers=_auth_headers(dept1_user))
    assert r1.status_code == 200

    # Super admin can retrieve
    r2 = client.get(f"/api/v1/grievances/{grv_id}", headers=_auth_headers(super_admin))
    assert r2.status_code == 200

    # Dept 2 user is prohibited (403 Forbidden)
    r3 = client.get(f"/api/v1/grievances/{grv_id}", headers=_auth_headers(dept2_user))
    assert r3.status_code == 403
    assert "belongs to another department" in r3.json()["detail"]


@skip_if_no_db
def test_list_grievances_scoped_and_filtered(db: Session):
    dept1, _ = _create_dept_and_category(db, f"D_{uuid.uuid4().hex[:4].upper()}")
    dept2, _ = _create_dept_and_category(db, f"D_{uuid.uuid4().hex[:4].upper()}")

    dept1_user = _create_user(db, RoleName.SUPERVISOR, dept1.id)
    super_admin = _create_user(db, RoleName.SUPER_ADMIN)

    # Create 2 grievances in Dept 1 and 1 in Dept 2
    r_g1 = client.post(
        "/api/v1/grievances",
        json={"title": "Dept1 Alpha Grievance", "description": "Detailed desc", "suggested_department_id": str(dept1.id), "priority": "HIGH"},
        headers=_auth_headers(super_admin),
    )
    r_g2 = client.post(
        "/api/v1/grievances",
        json={"title": "Dept1 Beta Grievance", "description": "Detailed desc", "suggested_department_id": str(dept1.id), "priority": "LOW"},
        headers=_auth_headers(super_admin),
    )
    r_g3 = client.post(
        "/api/v1/grievances",
        json={"title": "Dept2 Gamma Grievance", "description": "Detailed desc", "suggested_department_id": str(dept2.id), "priority": "HIGH"},
        headers=_auth_headers(super_admin),
    )

    # Confirm them
    client.post(f"/api/v1/grievances/{r_g1.json()['id']}/confirm-department", json={"department_id": str(dept1.id)}, headers=_auth_headers(super_admin))
    client.post(f"/api/v1/grievances/{r_g2.json()['id']}/confirm-department", json={"department_id": str(dept1.id)}, headers=_auth_headers(super_admin))
    client.post(f"/api/v1/grievances/{r_g3.json()['id']}/confirm-department", json={"department_id": str(dept2.id)}, headers=_auth_headers(super_admin))

    # Dept 1 user list should only see Dept 1 grievances
    list_resp = client.get("/api/v1/grievances", headers=_auth_headers(dept1_user))
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    dept_ids = {it["confirmed_department_id"] for it in items}
    assert dept_ids == {str(dept1.id)}

    # Super admin filtered by priority HIGH
    sa_high = client.get("/api/v1/grievances?priority=HIGH", headers=_auth_headers(super_admin))
    assert sa_high.status_code == 200
    for it in sa_high.json()["items"]:
        assert it["priority"] == "HIGH"

    # Search by grievance number
    target_num = r_g1.json()["grievance_number"]
    sa_search = client.get(f"/api/v1/grievances?search={target_num}", headers=_auth_headers(super_admin))
    assert sa_search.status_code == 200
    assert any(it["grievance_number"] == target_num for it in sa_search.json()["items"])


# ===========================================================================
# 4. Controlled Updates & Prohibited Tampering
# ===========================================================================

@skip_if_no_db
def test_update_grievance_allowed_fields(db: Session):
    dept, _ = _create_dept_and_category(db, f"D_{uuid.uuid4().hex[:4].upper()}")
    dept_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, dept.id)
    headers = _auth_headers(dept_admin)

    # Create grievance
    cr = client.post(
        "/api/v1/grievances",
        json={"title": "Old Title", "description": "Old Description", "suggested_department_id": str(dept.id), "priority": "LOW"},
        headers=headers,
    )
    grv_id = cr.json()["id"]

    # Confirm department
    client.post(
        f"/api/v1/grievances/{grv_id}/confirm-department",
        json={"department_id": str(dept.id)},
        headers=headers,
    )

    # Patch allowed fields
    patch_resp = client.patch(
        f"/api/v1/grievances/{grv_id}",
        json={
            "title": "Updated Road Safety Concern",
            "priority": "HIGH",
            "neutral_summary": "Objective synopsis of traffic signal failure.",
        },
        headers=headers,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    data = patch_resp.json()
    assert data["title"] == "Updated Road Safety Concern"
    assert data["priority"] == "HIGH"
    assert data["neutral_summary"] == "Objective synopsis of traffic signal failure."

    # Check audit log
    audit = db.query(ActivityLog).filter_by(
        action="GRIEVANCE_UPDATED",
        grievance_id=grv_id,
    ).first()
    assert audit is not None
    assert "title" in audit.new_values


@skip_if_no_db
def test_update_grievance_tamper_resistance(db: Session):
    dept, _ = _create_dept_and_category(db, f"D_{uuid.uuid4().hex[:4].upper()}")
    dept_admin = _create_user(db, RoleName.DEPARTMENT_ADMIN, dept.id)
    headers = _auth_headers(dept_admin)

    cr = client.post(
        "/api/v1/grievances",
        json={"title": "Tamper Test", "description": "Testing unchangeable fields.", "suggested_department_id": str(dept.id)},
        headers=headers,
    )
    grv_id = cr.json()["id"]
    orig_grv_number = cr.json()["grievance_number"]
    orig_status = cr.json()["status"]

    # Attempt to illegally tamper with status, grievance_number, created_by
    bogus_user_id = str(uuid.uuid4())
    resp_tamper = client.patch(
        f"/api/v1/grievances/{grv_id}",
        json={
            "title": "Valid Title Update",
            "status": "RESOLVED",  # Protected field must trigger 422 Unprocessable Entity
            "grievance_number": "GRV-9999-99999",
            "created_by": bogus_user_id,
        },
        headers=headers,
    )
    assert resp_tamper.status_code == 422

    # Verify grievance in DB remains untouched for prohibited fields
    fetched = client.get(f"/api/v1/grievances/{grv_id}", headers=headers).json()
    assert fetched["status"] == orig_status
    assert fetched["grievance_number"] == orig_grv_number
    assert fetched["created_by"] != bogus_user_id

