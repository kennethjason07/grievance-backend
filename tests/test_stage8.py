"""
Stage 8 Comprehensive Tests:
- Role seeding and repeated execution (idempotency)
- Password hashing & verification
- Authentication: login, JWT access & refresh tokens
- Invalid credentials rejection
- Inactive & unverified account rejection
- Role-based authorization & permission enforcement
- Concurrency-safe grievance number generation (format GRV-YYYY-00001)
- Concurrent grievance number generation (multi-threaded safety)
- Yearly sequence reset
- Notification idempotency key construction & uniqueness constraint
"""
import concurrent.futures
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.roles import Permission, RoleName, has_permission
from app.core.security import hash_password, verify_password
from app.main import app
from app.models import (
    Base,
    Department,
    Grievance,
    Notification,
    Role,
    User,
)
from app.services.auth import (
    AccountInactiveError,
    AccountUnverifiedError,
    AuthenticationError,
    login,
    refresh_access_token,
)
from app.services.seeder import seed_roles
from app.utils.grievance_number import generate_grievance_number
from app.utils.notification_idempotency import build_idempotency_key

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
    yield session
    session.close()
    transaction.rollback()
    connection.close()


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _get_or_create_dept(db: Session, name="Police Department", code="POL") -> Department:
    dept = db.query(Department).filter_by(code=code).first()
    if not dept:
        dept = Department(name=name, code=code, is_active=True)
        db.add(dept)
        db.flush()
    return dept


def _get_or_create_role(db: Session, name: str) -> Role:
    role = db.query(Role).filter_by(name=name).first()
    if not role:
        role = Role(name=name, description=f"Role: {name}")
        db.add(role)
        db.flush()
    return role


# ===========================================================================
# 1. Role Seeder & Idempotency Tests
# ===========================================================================

@skip_if_no_db
def test_role_seeding_and_idempotency(db: Session):
    """Verify seed_roles inserts all system roles and subsequent calls are no-ops."""
    res1 = seed_roles(db, commit=False)
    assert res1["inserted"] >= 6
    assert len(res1["roles"]) == 6

    # Verify roles exist in DB
    roles_in_db = {r.name for r in db.query(Role).all()}
    for expected_role in RoleName.ALL:
        assert expected_role in roles_in_db

    # Second run should insert 0 and report all already_existed
    res2 = seed_roles(db, commit=False)
    assert res2["inserted"] == 0
    assert res2["already_existed"] == 6


# ===========================================================================
# 2. Password Hashing & Security Tests
# ===========================================================================

def test_password_hashing():
    """Verify bcrypt hash generation and constant-time verification."""
    pwd = "SecurePolicePassword123!"
    p_hash = hash_password(pwd)
    assert p_hash != pwd
    assert p_hash.startswith("$2b$")
    assert verify_password(pwd, p_hash) is True
    assert verify_password("WrongPassword!", p_hash) is False
    assert verify_password("", p_hash) is False
    assert verify_password(pwd, "invalid_hash_string") is False


# ===========================================================================
# 3. Authentication & Token Tests
# ===========================================================================

@skip_if_no_db
def test_auth_login_and_token_flow(db: Session):
    """Verify login generates valid access and refresh tokens, and refresh works."""
    dept = _get_or_create_dept(db)
    role = _get_or_create_role(db, RoleName.OFFICER)
    pwd = "OfficerPassword2026!"
    email = f"officer_{uuid.uuid4().hex[:6]}@police.gov.in"

    user = User(
        full_name="Constable Sharma",
        email=email,
        password_hash=hash_password(pwd),
        department_id=dept.id,
        role_id=role.id,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()

    tokens = login(db, email, pwd)
    assert "access_token" in tokens
    assert "refresh_token" in tokens
    assert tokens["token_type"] == "bearer"

    # Verify user last_login_at was updated
    db.refresh(user)
    assert user.last_login_at is not None

    # Test refresh token endpoint service
    new_access = refresh_access_token(db, tokens["refresh_token"])
    assert "access_token" in new_access
    assert new_access["token_type"] == "bearer"


@skip_if_no_db
def test_auth_invalid_credentials(db: Session):
    """Verify wrong password or non-existent user raises AuthenticationError."""
    dept = _get_or_create_dept(db)
    role = _get_or_create_role(db, RoleName.OFFICER)
    email = f"user_{uuid.uuid4().hex[:6]}@police.gov.in"

    user = User(
        full_name="Officer Rao",
        email=email,
        password_hash=hash_password("RealPassword123!"),
        department_id=dept.id,
        role_id=role.id,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()

    # Wrong password
    with pytest.raises(AuthenticationError, match="Invalid email or password"):
        login(db, email, "IncorrectPassword!")

    # Non-existent email
    with pytest.raises(AuthenticationError, match="Invalid email or password"):
        login(db, "nobody@police.gov.in", "RealPassword123!")


@skip_if_no_db
def test_auth_inactive_and_unverified_users(db: Session):
    """Verify inactive and unverified accounts cannot log in."""
    dept = _get_or_create_dept(db)
    role = _get_or_create_role(db, RoleName.OFFICER)
    pwd = "StandardPassword123!"

    # Inactive user
    email_inactive = f"inactive_{uuid.uuid4().hex[:6]}@police.gov.in"
    u_inactive = User(
        full_name="Inactive Officer",
        email=email_inactive,
        password_hash=hash_password(pwd),
        department_id=dept.id,
        role_id=role.id,
        is_active=False,
        is_verified=True,
    )
    db.add(u_inactive)

    # Unverified user
    email_unverified = f"unverified_{uuid.uuid4().hex[:6]}@police.gov.in"
    u_unverified = User(
        full_name="Unverified Officer",
        email=email_unverified,
        password_hash=hash_password(pwd),
        department_id=dept.id,
        role_id=role.id,
        is_active=True,
        is_verified=False,
    )
    db.add(u_unverified)
    db.flush()

    with pytest.raises(AccountInactiveError, match="deactivated"):
        login(db, email_inactive, pwd)

    with pytest.raises(AccountUnverifiedError, match="not been verified"):
        login(db, email_unverified, pwd)


# ===========================================================================
# 4. Role Authorization & Permission Registry Tests
# ===========================================================================

def test_role_permissions_matrix():
    """Verify role permissions match defined security specifications."""
    # SUPER_ADMIN has full permissions
    assert has_permission(RoleName.SUPER_ADMIN, Permission.MANAGE_DEPARTMENTS) is True
    assert has_permission(RoleName.SUPER_ADMIN, Permission.CLOSE_GRIEVANCE) is True
    assert has_permission(RoleName.SUPER_ADMIN, Permission.VIEW_AUDIT_LOGS) is True

    # OFFICER has case action permissions but not user/dept management
    assert has_permission(RoleName.OFFICER, Permission.LOG_FOLLOW_UP_ACTION) is True
    assert has_permission(RoleName.OFFICER, Permission.MANAGE_DEPARTMENTS) is False
    assert has_permission(RoleName.OFFICER, Permission.MANAGE_USERS) is False

    # VIEWER has read-only access
    assert has_permission(RoleName.VIEWER, Permission.VIEW_GRIEVANCE) is True
    assert has_permission(RoleName.VIEWER, Permission.CREATE_GRIEVANCE) is False
    assert has_permission(RoleName.VIEWER, Permission.RESOLVE_GRIEVANCE) is False


# ===========================================================================
# 5. Grievance Number Generation Tests
# ===========================================================================

@skip_if_no_db
def test_grievance_number_format_and_sequence(db: Session):
    """Verify grievance numbers follow GRV-YYYY-NNNNN and increment correctly."""
    dept = _get_or_create_dept(db)
    role = _get_or_create_role(db, RoleName.DATA_ENTRY_OPERATOR)
    user = User(
        full_name="Clerk",
        email=f"clerk_{uuid.uuid4().hex[:6]}@gov.in",
        password_hash=hash_password("pwd"),
        department_id=dept.id,
        role_id=role.id,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()

    curr_year = datetime.now(tz=timezone.utc).year
    prefix = f"GRV-{curr_year}-"

    num1 = generate_grievance_number(db)
    assert num1.startswith(prefix)
    assert len(num1.split("-")[-1]) == 5

    # Insert grievance with num1
    g1 = Grievance(
        grievance_number=num1,
        title="Issue 1",
        description="Desc 1",
        created_by=user.id,
        status="NEW",
        priority="LOW",
    )
    db.add(g1)
    db.flush()

    # Generate next number
    num2 = generate_grievance_number(db)
    seq1 = int(num1.split("-")[-1])
    seq2 = int(num2.split("-")[-1])
    assert seq2 == seq1 + 1


@skip_if_no_db
def test_grievance_number_yearly_reset(db: Session):
    """Verify sequence calculation isolates previous years and starts at 00001 for a new year."""
    dept = _get_or_create_dept(db)
    role = _get_or_create_role(db, RoleName.DATA_ENTRY_OPERATOR)
    user = User(
        full_name="Clerk 2",
        email=f"clerk2_{uuid.uuid4().hex[:6]}@gov.in",
        password_hash=hash_password("pwd"),
        department_id=dept.id,
        role_id=role.id,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()

    # Insert an old grievance from year 2025 with a high sequence number
    old_g = Grievance(
        grievance_number="GRV-2025-00999",
        title="Old Grievance 2025",
        description="Old 2025 issue",
        created_by=user.id,
        status="CLOSED",
        priority="LOW",
    )
    db.add(old_g)
    db.flush()

    # Current year generation should ignore 2025 sequence
    curr_year = datetime.now(tz=timezone.utc).year
    num = generate_grievance_number(db)
    assert num.startswith(f"GRV-{curr_year}-")
    if curr_year != 2025:
        # Should NOT be 1000
        assert int(num.split("-")[-1]) < 1000


@skip_if_no_db
def test_concurrent_grievance_number_generation(test_engine):
    """Verify advisory lock prevents duplicate numbers across concurrent database connections."""
    generated_numbers = []
    num_threads = 5

    def worker():
        with Session(bind=test_engine) as session:
            with session.begin():
                number = generate_grievance_number(session)
                # simulate work while holding lock
                dept = session.query(Department).first()
                if not dept:
                    dept = Department(name="Temp Dept", code=f"TMP_{uuid.uuid4().hex[:4]}")
                    session.add(dept)
                    session.flush()
                role = session.query(Role).first()
                if not role:
                    role = Role(name=f"Role_{uuid.uuid4().hex[:4]}")
                    session.add(role)
                    session.flush()
                u = User(
                    full_name="Worker",
                    email=f"w_{uuid.uuid4().hex[:8]}@gov.in",
                    password_hash="x",
                    department_id=dept.id,
                    role_id=role.id,
                    is_active=True,
                    is_verified=True,
                )
                session.add(u)
                session.flush()
                g = Grievance(
                    grievance_number=number,
                    title="Concurrent Test",
                    description="Concurrent generation test",
                    created_by=u.id,
                    status="NEW",
                    priority="LOW",
                )
                session.add(g)
            return number

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker) for _ in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            generated_numbers.append(f.result())

    # Ensure all generated numbers are unique
    assert len(generated_numbers) == num_threads
    assert len(set(generated_numbers)) == num_threads


# ===========================================================================
# 6. Notification Idempotency Tests
# ===========================================================================

def test_notification_idempotency_key_construction():
    """Verify build_idempotency_key creates deterministic keys."""
    grv_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    task_id = uuid.UUID("66666666-7777-8888-9999-000000000000")
    test_date = date(2026, 11, 4)

    # With follow_up_task
    key1 = build_idempotency_key(
        grievance_id=grv_id,
        channel="SMS",
        notification_type="REMINDER",
        scheduled_date=test_date,
        follow_up_task_id=task_id,
    )
    assert key1 == f"{grv_id.hex}:{task_id.hex}:SMS:REMINDER:2026-11-04"

    # Without follow_up_task -> "none"
    key2 = build_idempotency_key(
        grievance_id=grv_id,
        channel="EMAIL",
        notification_type="ACKNOWLEDGMENT",
        scheduled_date=test_date,
        follow_up_task_id=None,
    )
    assert key2 == f"{grv_id.hex}:none:EMAIL:ACKNOWLEDGMENT:2026-11-04"

    # Determinism: exact same inputs produce exact same key
    key3 = build_idempotency_key(
        grievance_id=grv_id,
        channel="SMS",
        notification_type="REMINDER",
        scheduled_date=test_date,
        follow_up_task_id=task_id,
    )
    assert key1 == key3


@skip_if_no_db
def test_notification_idempotency_duplicate_prevention(db: Session):
    """Verify database unique constraint rejects duplicate notifications with identical idempotency key."""
    dept = _get_or_create_dept(db)
    role = _get_or_create_role(db, RoleName.OFFICER)
    user = User(
        full_name="Notif User",
        email=f"notif_{uuid.uuid4().hex[:6]}@gov.in",
        password_hash="pwd",
        department_id=dept.id,
        role_id=role.id,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()

    grv = Grievance(
        grievance_number=f"GRV-IDEM-{uuid.uuid4().hex[:6].upper()}",
        title="Idem Test",
        description="Desc",
        created_by=user.id,
        status="NEW",
        priority="MEDIUM",
    )
    db.add(grv)
    db.flush()

    idem_key = build_idempotency_key(
        grievance_id=grv.id,
        channel="SMS",
        notification_type="REMINDER",
        scheduled_date=date(2026, 11, 4),
    )

    n1 = Notification(
        grievance_id=grv.id,
        recipient_name="Citizen One",
        recipient_contact="9876543210",
        notification_type="REMINDER",
        channel="SMS",
        message="Your reminder",
        status="GENERATED",
        idempotency_key=idem_key,
    )
    db.add(n1)
    db.flush()

    # Second notification with the SAME idempotency key must raise IntegrityError
    n2 = Notification(
        grievance_id=grv.id,
        recipient_name="Citizen One",
        recipient_contact="9876543210",
        notification_type="REMINDER",
        channel="SMS",
        message="Duplicate reminder attempt",
        status="GENERATED",
        idempotency_key=idem_key,
    )
    db.add(n2)
    with pytest.raises(IntegrityError):
        db.flush()


# ===========================================================================
# 7. FastAPI HTTP Auth Endpoints Integration Tests
# ===========================================================================

@skip_if_no_db
def test_api_auth_http_endpoints(test_engine):
    """Verify HTTP API v1 endpoints for login, /me, refresh, and logout."""
    email = f"api_user_{uuid.uuid4().hex[:6]}@police.gov.in"
    password = "StrongPassword2026!"

    def override_get_db():
        with Session(bind=test_engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    try:
        with Session(bind=test_engine) as session:
            dept = session.query(Department).first()
            if not dept:
                dept = Department(name="Police Dept API", code=f"POL_{uuid.uuid4().hex[:4]}")
                session.add(dept)
                session.flush()
            role = session.query(Role).first()
            if not role:
                role = Role(name=f"Role_{uuid.uuid4().hex[:4]}")
                session.add(role)
                session.flush()
            u = User(
                full_name="API User",
                email=email,
                password_hash=hash_password(password),
                department_id=dept.id,
                role_id=role.id,
                is_active=True,
                is_verified=True,
            )
            session.add(u)
            session.commit()

        # 1. Login
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert login_resp.status_code == 200
        token_data = login_resp.json()
        assert "access_token" in token_data
        assert "refresh_token" in token_data

        access_token = token_data["access_token"]
        refresh_token = token_data["refresh_token"]

        # 2. GET /api/v1/auth/me with Bearer token
        me_resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["email"] == email
        assert me_data["is_active"] is True

        # 3. GET /api/v1/auth/me without token -> 401
        unauth_resp = client.get("/api/v1/auth/me")
        assert unauth_resp.status_code == 401

        # 4. POST /api/v1/auth/refresh
        refresh_resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert refresh_resp.status_code == 200
        assert "access_token" in refresh_resp.json()

        # 5. POST /api/v1/auth/logout
        logout_resp = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert logout_resp.status_code == 200
        assert logout_resp.json()["message"] == "Successfully logged out."
    finally:
        app.dependency_overrides.clear()
