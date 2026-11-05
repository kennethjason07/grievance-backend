"""
Comprehensive security and reliability tests for Grievance Document Upload:
- Valid extensions, MIME validation, and signature verification (magic bytes)
- Rejection of executable, script, and dangerous extensions
- Path traversal mitigation and server-generated storage filenames
- Atomic failure handling: No orphaned files on disk upon DB failure
- Department-level authorization on upload, listing, and download
- Safe retries and metadata retention
- Verification that internal storage paths are not leaked
"""
import io
import uuid
import zipfile
from pathlib import Path
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
from app.models.document import Document
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
def db(test_engine) -> pytest.FixtureRequest:
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


def _create_grievance(db: Session, dept: Department, user: User) -> Grievance:
    grievance = Grievance(
        grievance_number=f"GRV-2026-{uuid.uuid4().hex[:5].upper()}",
        title="Land Encroachment Complaint",
        description="Public land encroachment complaint requiring police inspection.",
        source="IN_PERSON",
        original_language="kn",
        suggested_department_id=dept.id,
        confirmed_department_id=dept.id,
        status="NEW",
        priority="HIGH",
        created_by=user.id,
    )
    db.add(grievance)
    db.flush()
    return grievance


# ===========================================================================
# 1. Successful Upload, Listing, Download, and Storage Path Protection
# ===========================================================================

@skip_if_no_db
def test_upload_document_success(db: Session):
    dept = Department(name="Home Dept", code=f"POL_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    file_content = b"%PDF-1.4 Simulated petition content for legal complaint"
    files = {
        "file": ("complaint_letter.pdf", io.BytesIO(file_content), "application/pdf")
    }
    data = {
        "document_type": "PETITION",
        "language": "kn",
        "document_name": "Official Citizen Petition",
    }

    res = client.post(
        f"/api/v1/grievances/{grievance.id}/documents",
        headers=headers,
        files=files,
        data=data,
    )

    assert res.status_code == 201, res.text
    payload = res.json()
    assert payload["grievance_id"] == str(grievance.id)
    assert payload["document_name"] == "Official Citizen Petition"
    assert payload["original_filename"] == "complaint_letter.pdf"
    assert payload["file_size"] == len(file_content)
    assert payload["mime_type"] == "application/pdf"
    assert payload["document_type"] == "PETITION"
    assert payload["language"] == "kn"

    # CRITICAL: Verify internal storage path is NOT leaked in response
    assert "storage_path" not in payload
    assert payload.get("download_url") == f"/api/v1/documents/{payload['id']}/download"

    # Verify document listed in DB
    doc_id = payload["id"]
    doc_record = db.get(Document, uuid.UUID(doc_id))
    assert doc_record is not None
    assert doc_record.file_size == len(file_content)

    # Verify audit log
    log = db.query(ActivityLog).filter_by(
        action="DOCUMENT_UPLOADED",
        entity_id=uuid.UUID(doc_id),
    ).first()
    assert log is not None
    assert log.grievance_id == grievance.id


@skip_if_no_db
def test_list_and_download_document(db: Session):
    dept = Department(name="Revenue Dept", code=f"REV_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    user = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    file_content = b"Binary sample report data for case resolution"
    files = {
        "file": ("spot_report.txt", io.BytesIO(file_content), "text/plain")
    }
    upload_res = client.post(
        f"/api/v1/grievances/{grievance.id}/documents",
        headers=headers,
        files=files,
        data={"document_type": "REPORT", "language": "en"},
    )
    assert upload_res.status_code == 201
    doc_id = upload_res.json()["id"]

    # 1. Test listing documents
    list_res = client.get(
        f"/api/v1/grievances/{grievance.id}/documents",
        headers=headers,
    )
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) >= 1
    assert any(d["id"] == doc_id for d in items)
    assert all("storage_path" not in d for d in items)

    # 2. Test download (attachment disposition)
    dl_res = client.get(
        f"/api/v1/documents/{doc_id}/download",
        headers=headers,
    )
    assert dl_res.status_code == 200
    assert dl_res.content == file_content
    assert "attachment" in dl_res.headers.get("content-disposition", "")

    # 3. Test inline preview
    preview_res = client.get(
        f"/api/v1/documents/{doc_id}/download?preview=true",
        headers=headers,
    )
    assert preview_res.status_code == 200
    assert preview_res.content == file_content
    assert "inline" in preview_res.headers.get("content-disposition", "")


# ===========================================================================
# 2. Security Validation: File Extensions, MIME Types, and Signatures
# ===========================================================================

@skip_if_no_db
def test_invalid_file_extension(db: Session):
    dept = Department(name="IT Dept", code=f"IT_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    dangerous_names = [
        "payload.exe",
        "script.sh",
        "malware.bat",
        "hack.py",
        "exploit.vbs",
        "app.js",
    ]

    for filename in dangerous_names:
        files = {
            "file": (filename, io.BytesIO(b"echo malicious"), "application/octet-stream")
        }
        res = client.post(
            f"/api/v1/grievances/{grievance.id}/documents",
            headers=headers,
            files=files,
        )
        assert res.status_code == 400, f"Expected 400 for {filename}, got {res.status_code}"
        assert "unsupported or dangerous file extension" in res.json()["detail"].lower()


@skip_if_no_db
def test_mime_mismatch(db: Session):
    dept = Department(name="Agri Dept", code=f"AGR_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    # .pdf with declared image/png MIME type
    files = {
        "file": ("petition.pdf", io.BytesIO(b"%PDF-1.4 valid header"), "image/png")
    }
    res = client.post(
        f"/api/v1/grievances/{grievance.id}/documents",
        headers=headers,
        files=files,
    )
    assert res.status_code == 400
    assert "mime type mismatch" in res.json()["detail"].lower()


@skip_if_no_db
def test_invalid_file_signature(db: Session):
    dept = Department(name="Civil Dept", code=f"CIV_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    # 1. Fake PDF (does not start with %PDF-)
    fake_pdf = {
        "file": ("fake.pdf", io.BytesIO(b"This is a text file masquerading as PDF"), "application/pdf")
    }
    res = client.post(f"/api/v1/grievances/{grievance.id}/documents", headers=headers, files=fake_pdf)
    assert res.status_code == 400
    assert "invalid file signature" in res.json()["detail"].lower()

    # 2. Fake PNG (does not have PNG magic bytes)
    fake_png = {
        "file": ("fake.png", io.BytesIO(b"Fake PNG content"), "image/png")
    }
    res = client.post(f"/api/v1/grievances/{grievance.id}/documents", headers=headers, files=fake_png)
    assert res.status_code == 400
    assert "invalid file signature" in res.json()["detail"].lower()

    # 3. Text file with executable / binary null bytes
    binary_txt = {
        "file": ("binary.txt", io.BytesIO(b"MZ\x90\x00\x03\x00\x00\x00"), "text/plain")
    }
    res = client.post(f"/api/v1/grievances/{grievance.id}/documents", headers=headers, files=binary_txt)
    assert res.status_code == 400
    assert "executable or binary content detected" in res.json()["detail"].lower()


# ===========================================================================
# 3. Path Traversal Mitigation & Server-Generated Filenames
# ===========================================================================

@skip_if_no_db
def test_path_traversal_attempt(db: Session):
    dept = Department(name="Security Dept", code=f"SEC_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    # Attempt path traversal via filename
    traversal_name = "../../../etc/passwd.pdf"
    file_content = b"%PDF-1.4 Content safe"
    files = {
        "file": (traversal_name, io.BytesIO(file_content), "application/pdf")
    }

    res = client.post(
        f"/api/v1/grievances/{grievance.id}/documents",
        headers=headers,
        files=files,
    )
    assert res.status_code == 201
    payload = res.json()

    # Verify that the filename was stripped and sanitized
    assert "/" not in payload["original_filename"]
    assert ".." not in payload["original_filename"]

    # Verify file is strictly stored inside settings.UPLOAD_DIR
    doc_record = db.get(Document, uuid.UUID(payload["id"]))
    base_upload_dir = Path(settings.UPLOAD_DIR).resolve()
    stored_path = Path(doc_record.storage_path).resolve()
    assert stored_path.is_relative_to(base_upload_dir)
    assert stored_path.is_file()


# ===========================================================================
# 4. Atomic Cleanup: No Orphaned Files on Database Failure
# ===========================================================================

@skip_if_no_db
def test_database_failure_after_file_write_cleans_up(db: Session, monkeypatch):
    dept = Department(name="Test Dept", code=f"TST_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    file_content = b"%PDF-1.4 Atomicity test content"
    files = {
        "file": ("atomicity_test.pdf", io.BytesIO(file_content), "application/pdf")
    }

    # Simulate database failure during commit
    original_commit = db.commit

    def failing_commit():
        raise RuntimeError("Simulated Database Outage during document commit")

    monkeypatch.setattr(db, "commit", failing_commit)

    res = client.post(
        f"/api/v1/grievances/{grievance.id}/documents",
        headers=headers,
        files=files,
    )
    assert res.status_code == 500
    assert "database transaction failed" in res.json()["detail"].lower()

    # Restore commit and verify NO orphaned files remain in grievance target directory
    monkeypatch.setattr(db, "commit", original_commit)
    target_dir = Path(settings.UPLOAD_DIR).resolve() / str(grievance.id)
    if target_dir.exists():
        existing_files = list(target_dir.glob("*.pdf"))
        assert len(existing_files) == 0, f"Found orphaned files on disk: {existing_files}"


# ===========================================================================
# 5. Authorization: Cross-Department and Unauthenticated Rejection
# ===========================================================================

@skip_if_no_db
def test_cross_department_access_denied(db: Session):
    dept_a = Department(name="Dept A", code=f"DA_{uuid.uuid4().hex[:4].upper()}")
    dept_b = Department(name="Dept B", code=f"DB_{uuid.uuid4().hex[:4].upper()}")
    db.add_all([dept_a, dept_b])
    db.flush()

    user_a = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept_a.id)
    user_b = _create_user(db, RoleName.OFFICER, dept_b.id)

    grievance_a = _create_grievance(db, dept_a, user_a)
    headers_b = _auth_headers(user_b)

    files = {
        "file": ("cross_dept.pdf", io.BytesIO(b"%PDF-1.4 test"), "application/pdf")
    }
    # 1. User B uploading to Grievance A -> 403
    upload_res = client.post(
        f"/api/v1/grievances/{grievance_a.id}/documents",
        headers=headers_b,
        files=files,
    )
    assert upload_res.status_code == 403

    # Upload legitimate document with User A
    headers_a = _auth_headers(user_a)
    legit_upload = client.post(
        f"/api/v1/grievances/{grievance_a.id}/documents",
        headers=headers_a,
        files=files,
    )
    assert legit_upload.status_code == 201
    doc_id = legit_upload.json()["id"]

    # 2. User B listing Grievance A documents -> 403
    list_res = client.get(
        f"/api/v1/grievances/{grievance_a.id}/documents",
        headers=headers_b,
    )
    assert list_res.status_code == 403

    # 3. User B downloading Grievance A document -> 403
    download_res = client.get(
        f"/api/v1/documents/{doc_id}/download",
        headers=headers_b,
    )
    assert download_res.status_code == 403

    # 4. Unauthenticated download -> 401
    unauth_res = client.get(f"/api/v1/documents/{doc_id}/download")
    assert unauth_res.status_code == 401


@skip_if_no_db
def test_empty_file_rejected(db: Session):
    dept = Department(name="Health Dept", code=f"HLT_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    files = {
        "file": ("empty.pdf", io.BytesIO(b""), "application/pdf")
    }
    res = client.post(
        f"/api/v1/grievances/{grievance.id}/documents",
        headers=headers,
        files=files,
    )
    assert res.status_code == 400
    assert "empty file" in res.json()["detail"].lower()


@skip_if_no_db
def test_oversized_file_rejected(db: Session, monkeypatch):
    dept = Department(name="Transport Dept", code=f"TRN_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    # Monkeypatch MAX_UPLOAD_SIZE_BYTES to 100 bytes for fast test
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_BYTES", 100)

    large_content = b"%PDF-1.4 " + (b"X" * 150)
    files = {
        "file": ("oversized.pdf", io.BytesIO(large_content), "application/pdf")
    }
    res = client.post(
        f"/api/v1/grievances/{grievance.id}/documents",
        headers=headers,
        files=files,
    )
    assert res.status_code == 413
    assert "exceeds maximum limit" in res.json()["detail"]


@skip_if_no_db
def test_retry_after_failed_upload_preserves_single_grievance(db: Session):
    """
    Demonstrate that a failed upload can be retried safely against the same grievance
    without duplicating the grievance.
    """
    dept = Department(name="Social Welfare", code=f"SW_{uuid.uuid4().hex[:4].upper()}")
    db.add(dept)
    db.flush()

    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    # Step 1: Failed upload due to invalid signature
    bad_files = {
        "file": ("corrupt.pdf", io.BytesIO(b"Not a PDF"), "application/pdf")
    }
    fail_res = client.post(
        f"/api/v1/grievances/{grievance.id}/documents",
        headers=headers,
        files=bad_files,
    )
    assert fail_res.status_code == 400

    # Verify grievance is still intact and no document was added
    docs_before = db.query(Document).filter_by(grievance_id=grievance.id).all()
    assert len(docs_before) == 0

    # Step 2: Retry upload with valid file
    good_files = {
        "file": ("valid.pdf", io.BytesIO(b"%PDF-1.4 Valid Petition"), "application/pdf")
    }
    retry_res = client.post(
        f"/api/v1/grievances/{grievance.id}/documents",
        headers=headers,
        files=good_files,
    )
    assert retry_res.status_code == 201

    # Verify single grievance now has exactly one document attached
    docs_after = db.query(Document).filter_by(grievance_id=grievance.id).all()
    assert len(docs_after) == 1
    assert docs_after[0].original_filename == "valid.pdf"
