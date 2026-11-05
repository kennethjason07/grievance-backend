"""
JanSetu Stage 2 Translation Test Suite: Bidirectional Kannada-English Translation.

Covers all 18 required categories:
1.  Kannada -> English translation
2.  English -> Kannada translation
3.  Empty OCR text rejection
4.  Missing OCR results rejection
5.  Identical source & target language rejection
6.  Unsupported language rejection
7.  Translation provider unavailable / failure handling
8.  Translation failure and retry
9.  Long document chunking
10. Paragraph preservation
11. Number and identifier preservation
12. Translation history retention
13. Human correction & reviewed text separation
14. Approval workflow
15. Original OCR text immutability
16. Department isolation (cross-department denial)
17. Unauthorized approval rejection
18. Audit event creation

All tests run against grievance_test_db only.
"""

import io
import uuid
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
from app.models.translation import DocumentTranslation
from app.services.translation import MockTranslationProvider, set_translation_provider_override

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


@pytest.fixture(autouse=True)
def reset_translation_provider():
    """Ensure mock provider is set by default for all translation tests."""
    set_translation_provider_override(MockTranslationProvider())
    yield
    set_translation_provider_override(None)


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _get_or_create_role(db: Session, name: str) -> Role:
    role = db.query(Role).filter_by(name=name).first()
    if not role:
        role = Role(name=name, description=f"Role: {name}")
        db.add(role)
        db.flush()
    return role


def _create_user(db: Session, role_name: str, department_id=None) -> User:
    role = _get_or_create_role(db, role_name)
    user = User(
        full_name=f"User {role_name}",
        email=f"trans_{uuid.uuid4().hex[:8]}@gov.in",
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


def _create_dept(db: Session, prefix: str = "TRN") -> Department:
    dept = Department(
        name=f"{prefix} Dept {uuid.uuid4().hex[:4].upper()}",
        code=f"{prefix}_{uuid.uuid4().hex[:4].upper()}",
    )
    db.add(dept)
    db.flush()
    return dept


def _create_grievance(db: Session, dept: Department, user: User) -> Grievance:
    g = Grievance(
        grievance_number=f"GRV-2026-{uuid.uuid4().hex[:5].upper()}",
        title="Translation Test Grievance",
        description="Grievance for translation test suite",
        source="IN_PERSON",
        original_language="kn",
        suggested_department_id=dept.id,
        confirmed_department_id=dept.id,
        status="NEW",
        priority="HIGH",
        created_by=user.id,
    )
    db.add(g)
    db.flush()
    return g


def _create_document_with_ocr(
    db: Session,
    grievance: Grievance,
    user: User,
    ocr_status: str = "COMPLETED",
    extracted_text: str = "ಮಾದರಿ ಕನ್ನಡ ದೂರು ಪಠ್ಯ",
    reviewed_text: str = None,
) -> Document:
    doc = Document(
        grievance_id=grievance.id,
        document_name="Citizen Petition",
        original_filename="petition.pdf",
        storage_path="uploads/test_petition.pdf",
        mime_type="application/pdf",
        file_size=1024,
        document_type="PETITION",
        language="kn",
        uploaded_by=user.id,
        ocr_status=ocr_status,
        extracted_text=extracted_text,
        reviewed_text=reviewed_text or extracted_text,
        ocr_language="kn",
    )
    db.add(doc)
    db.flush()
    return doc


# ===========================================================================
# 1. Kannada -> English Translation
# ===========================================================================

@skip_if_no_db
def test_translation_kannada_to_english(db: Session):
    dept = _create_dept(db, "TKE")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    doc = _create_document_with_ocr(
        db, grievance, user,
        ocr_status="COMPLETED",
        extracted_text="ದೂರು: ಬೆಂಗಳೂರು ದಕ್ಷಿಣ ತಾಲ್ಲೂಕಿನಲ್ಲಿ ಭೂಮಿ ಒತ್ತುವರಿ ಬಗ್ಗೆ ಜಿಲ್ಲಾಧಿಕಾರಿಗಳು ತನಿಖೆ ನಡೆಸಬೇಕು.",
    )
    headers = _auth_headers(user)

    res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "kn", "target_language": "en", "text_source": "REVIEWED_OCR"},
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["source_language"] == "kn"
    assert data["target_language"] == "en"
    assert data["status"] == "COMPLETED"
    assert data["translated_text"] is not None
    assert "Complaint" in data["translated_text"] or "Deputy Commissioner" in data["translated_text"]
    assert data["document_id"] == str(doc.id)


# ===========================================================================
# 2. English -> Kannada Translation
# ===========================================================================

@skip_if_no_db
def test_translation_english_to_kannada(db: Session):
    dept = _create_dept(db, "TEK")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    doc = _create_document_with_ocr(
        db, grievance, user,
        ocr_status="COMPLETED",
        extracted_text="Complaint: Encroachment in Bengaluru South. Deputy Commissioner must order inspection.",
    )
    headers = _auth_headers(user)

    res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "en", "target_language": "kn", "text_source": "REVIEWED_OCR"},
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["source_language"] == "en"
    assert data["target_language"] == "kn"
    assert data["status"] == "COMPLETED"
    assert "ದೂರು" in data["translated_text"] or "ಜಿಲ್ಲಾಧಿಕಾರಿಗಳು" in data["translated_text"]


# ===========================================================================
# 3. Empty OCR Text Rejection
# ===========================================================================

@skip_if_no_db
def test_translation_empty_ocr_text_rejected(db: Session):
    dept = _create_dept(db, "TEMT")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    doc = _create_document_with_ocr(
        db, grievance, user,
        ocr_status="COMPLETED",
        extracted_text="",
        reviewed_text="",
    )
    headers = _auth_headers(user)

    res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "kn", "target_language": "en", "text_source": "RAW_OCR"},
    )
    assert res.status_code == 400
    assert "empty" in res.json()["detail"].lower()


# ===========================================================================
# 4. Missing OCR Results Rejection
# ===========================================================================

@skip_if_no_db
def test_translation_missing_ocr_results_rejected(db: Session):
    dept = _create_dept(db, "TMIS")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    doc = _create_document_with_ocr(
        db, grievance, user,
        ocr_status="NOT_STARTED",
        extracted_text=None,
    )
    headers = _auth_headers(user)

    res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "kn", "target_language": "en"},
    )
    assert res.status_code == 400
    assert "ocr" in res.json()["detail"].lower()


# ===========================================================================
# 5. Identical Source and Target Languages Rejection
# ===========================================================================

@skip_if_no_db
def test_translation_identical_languages_rejected(db: Session):
    dept = _create_dept(db, "TIDN")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    doc = _create_document_with_ocr(db, grievance, user, ocr_status="COMPLETED", extracted_text="Some text")
    headers = _auth_headers(user)

    res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "kn", "target_language": "kn"},
    )
    assert res.status_code == 400
    assert "identical" in res.json()["detail"].lower()


# ===========================================================================
# 6. Unsupported Language Rejection
# ===========================================================================

@skip_if_no_db
def test_translation_unsupported_language_rejected(db: Session):
    dept = _create_dept(db, "TUNS")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    doc = _create_document_with_ocr(db, grievance, user, ocr_status="COMPLETED", extracted_text="Some text")
    headers = _auth_headers(user)

    res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "fr", "target_language": "en"},
    )
    # Schema validation or service validation rejects unsupported language code
    assert res.status_code in (400, 422)


# ===========================================================================
# 7. Translation Provider Unavailable / Failure Handling
# ===========================================================================

@skip_if_no_db
def test_translation_provider_failure_handled(db: Session):
    dept = _create_dept(db, "TFAIL")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    doc = _create_document_with_ocr(db, grievance, user, ocr_status="COMPLETED", extracted_text="Text to translate")
    headers = _auth_headers(user)

    # Inject failure into provider
    failing_provider = MockTranslationProvider(fail_next=True)
    set_translation_provider_override(failing_provider)

    res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "kn", "target_language": "en"},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "FAILED"
    assert data["error_message"] is not None
    assert "failure" in data["error_message"].lower() or "outage" in data["error_message"].lower()


# ===========================================================================
# 8. Translation Failure and Safe Retry
# ===========================================================================

@skip_if_no_db
def test_translation_failure_and_retry(db: Session):
    dept = _create_dept(db, "TRTY")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    doc = _create_document_with_ocr(db, grievance, user, ocr_status="COMPLETED", extracted_text="Text to translate")
    headers = _auth_headers(user)

    # 1. Trigger failure
    failing_provider = MockTranslationProvider(fail_next=True)
    set_translation_provider_override(failing_provider)
    fail_res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "kn", "target_language": "en"},
    )
    assert fail_res.json()["status"] == "FAILED"

    # 2. Re-trigger with operational provider
    set_translation_provider_override(MockTranslationProvider())
    retry_res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "kn", "target_language": "en"},
    )
    assert retry_res.status_code == 201
    assert retry_res.json()["status"] == "COMPLETED"
    assert retry_res.json()["error_message"] is None


# ===========================================================================
# 9. Long Document Chunking
# ===========================================================================

@skip_if_no_db
def test_translation_long_document_chunking(db: Session, monkeypatch):
    dept = _create_dept(db, "TCHK")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)

    # Set chunk size small to force multiple chunks
    monkeypatch.setattr(settings, "TRANSLATION_MAX_CHUNK_CHARS", 100)

    p1 = "Paragraph 1: Grievance regarding public road maintenance."
    p2 = "Paragraph 2: Multiple potholes reported by residents."
    p3 = "Paragraph 3: Urgent repair requested by panchayat."
    long_text = f"{p1}\n\n{p2}\n\n{p3}"

    doc = _create_document_with_ocr(db, grievance, user, ocr_status="COMPLETED", extracted_text=long_text)
    headers = _auth_headers(user)

    res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "en", "target_language": "kn"},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "COMPLETED"
    # All 3 paragraphs should be represented in translation
    translated = data["translated_text"]
    assert "Paragraph 1" in translated or "ದೂರು" in translated
    assert "Paragraph 2" in translated
    assert "Paragraph 3" in translated


# ===========================================================================
# 10. Paragraph Preservation
# ===========================================================================

@skip_if_no_db
def test_translation_paragraph_preservation(db: Session):
    dept = _create_dept(db, "TPAR")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)

    text_with_paras = "First paragraph content.\n\nSecond paragraph content.\n\nThird paragraph content."
    doc = _create_document_with_ocr(db, grievance, user, ocr_status="COMPLETED", extracted_text=text_with_paras)
    headers = _auth_headers(user)

    res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "en", "target_language": "kn"},
    )
    assert res.status_code == 201
    translated = res.json()["translated_text"]
    out_paras = translated.split("\n\n")
    assert len(out_paras) == 3
    assert "First" in out_paras[0]
    assert "Second" in out_paras[1]
    assert "Third" in out_paras[2]


# ===========================================================================
# 11. Number and Identifier Preservation
# ===========================================================================

@skip_if_no_db
def test_translation_preserves_numbers_and_identifiers(db: Session):
    dept = _create_dept(db, "TNUM")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)

    text_with_ids = (
        "Complainant Sunita Mehra (Aadhaar: 4892-1092-3841, Phone: +91 98860 44556). "
        "Case ID: GRV-2026-99214. Amount defrauded: Rs 4,80,000 on 28-10-2026."
    )
    doc = _create_document_with_ocr(db, grievance, user, ocr_status="COMPLETED", extracted_text=text_with_ids)
    headers = _auth_headers(user)

    res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "en", "target_language": "kn"},
    )
    assert res.status_code == 201
    translated = res.json()["translated_text"]
    # Identifiers must not be scrambled or dropped
    assert "4892-1092-3841" in translated
    assert "98860 44556" in translated
    assert "GRV-2026-99214" in translated
    assert "4,80,000" in translated


# ===========================================================================
# 12. Translation History Retention
# ===========================================================================

@skip_if_no_db
def test_translation_history_retention(db: Session):
    dept = _create_dept(db, "THST")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    doc = _create_document_with_ocr(db, grievance, user, ocr_status="COMPLETED", extracted_text="Initial text")
    headers = _auth_headers(user)

    # First translation: kn -> en
    res1 = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "kn", "target_language": "en"},
    )
    assert res1.status_code == 201
    id1 = res1.json()["id"]

    # Second translation: en -> kn
    res2 = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "en", "target_language": "kn"},
    )
    assert res2.status_code == 201
    id2 = res2.json()["id"]

    # List translations
    list_res = client.get(f"/api/v1/documents/{doc.id}/translations", headers=headers)
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) >= 2
    item_ids = [item["id"] for item in items]
    assert id1 in item_ids
    assert id2 in item_ids


# ===========================================================================
# 13. Human Correction & Separation from Raw Machine Output
# ===========================================================================

@skip_if_no_db
def test_translation_human_correction(db: Session):
    dept = _create_dept(db, "TCOR")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    doc = _create_document_with_ocr(db, grievance, user, ocr_status="COMPLETED", extracted_text="ದೂರು ವಿವರಗಳು")
    headers = _auth_headers(user)

    # 1. Generate machine translation
    trans_res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "kn", "target_language": "en"},
    )
    t_id = trans_res.json()["id"]
    original_machine = trans_res.json()["translated_text"]

    # 2. Submit human review / correction via PATCH
    corrected = "Human verified: Complete citizen complaint statement in formal English."
    review_res = client.patch(
        f"/api/v1/translations/{t_id}/review",
        headers=headers,
        json={"corrected_text": corrected},
    )
    assert review_res.status_code == 200
    data = review_res.json()
    assert data["status"] == "UNDER_REVIEW"
    assert data["corrected_text"] == corrected
    # Critical: original machine translation must remain distinguishable and unchanged
    assert data["translated_text"] == original_machine
    assert data["reviewed_by"] == str(user.id)


# ===========================================================================
# 14. Approval Workflow
# ===========================================================================

@skip_if_no_db
def test_translation_approval_workflow(db: Session):
    dept = _create_dept(db, "TAPR")
    deo = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    officer = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_grievance(db, dept, deo)
    doc = _create_document_with_ocr(db, grievance, deo, ocr_status="COMPLETED", extracted_text="ದೂರು ಪಠ್ಯ")

    # DEO triggers translation
    trans_res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=_auth_headers(deo),
        json={"source_language": "kn", "target_language": "en"},
    )
    t_id = trans_res.json()["id"]

    # Officer approves translation
    appr_res = client.post(
        f"/api/v1/translations/{t_id}/approve",
        headers=_auth_headers(officer),
    )
    assert appr_res.status_code == 200, appr_res.text
    data = appr_res.json()
    assert data["status"] == "APPROVED"
    assert data["approved_by"] == str(officer.id)
    assert data["approved_at"] is not None


# ===========================================================================
# 15. Original OCR Text Immutability
# ===========================================================================

@skip_if_no_db
def test_translation_original_ocr_text_immutable(db: Session):
    dept = _create_dept(db, "TIMM")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)

    orig_extracted = "ಕನ್ನಡ ಮೂಲ ಪಠ್ಯ — ಈ ಪಠ್ಯ ಬದಲಾಗಬಾರದು."
    orig_reviewed = "Reviewed version of Kannada text."
    doc = _create_document_with_ocr(
        db, grievance, user,
        ocr_status="COMPLETED",
        extracted_text=orig_extracted,
        reviewed_text=orig_reviewed,
    )
    headers = _auth_headers(user)

    # Run translation
    res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "kn", "target_language": "en"},
    )
    assert res.status_code == 201

    # Reload document from DB
    db.refresh(doc)
    assert doc.extracted_text == orig_extracted, "extracted_text was mutated!"
    assert doc.reviewed_text == orig_reviewed, "reviewed_text was mutated!"
    assert doc.ocr_status == "COMPLETED"


# ===========================================================================
# 16. Department Isolation (Cross-Department Denial)
# ===========================================================================

@skip_if_no_db
def test_translation_cross_department_denied(db: Session):
    dept_a = _create_dept(db, "TDPA")
    dept_b = _create_dept(db, "TDPB")
    user_a = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept_a.id)
    user_b = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept_b.id)

    grievance_a = _create_grievance(db, dept_a, user_a)
    doc_a = _create_document_with_ocr(db, grievance_a, user_a, ocr_status="COMPLETED", extracted_text="Sensitive text")

    # User B (Dept B) tries to translate Dept A's document
    res = client.post(
        f"/api/v1/documents/{doc_a.id}/translate",
        headers=_auth_headers(user_b),
        json={"source_language": "kn", "target_language": "en"},
    )
    assert res.status_code == 403

    # User B tries to list Dept A's translations
    list_res = client.get(
        f"/api/v1/documents/{doc_a.id}/translations",
        headers=_auth_headers(user_b),
    )
    assert list_res.status_code == 403


# ===========================================================================
# 17. Unauthorized Approval Rejection
# ===========================================================================

@skip_if_no_db
def test_translation_unauthorized_approval_rejected(db: Session):
    dept = _create_dept(db, "TUAP")
    deo = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, deo)
    doc = _create_document_with_ocr(db, grievance, deo, ocr_status="COMPLETED", extracted_text="Text to approve")

    # DEO triggers translation
    trans_res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=_auth_headers(deo),
        json={"source_language": "kn", "target_language": "en"},
    )
    t_id = trans_res.json()["id"]

    # DEO attempts to approve the translation -> must be rejected with 403 Forbidden
    appr_res = client.post(
        f"/api/v1/translations/{t_id}/approve",
        headers=_auth_headers(deo),
    )
    assert appr_res.status_code == 403, f"Expected 403, got {appr_res.status_code}"
    assert "not authorized" in appr_res.json()["detail"].lower() or "officer" in appr_res.json()["detail"].lower()


# ===========================================================================
# 18. Audit Event Creation
# ===========================================================================

@skip_if_no_db
def test_translation_audit_event_creation(db: Session):
    dept = _create_dept(db, "TAUD")
    user = _create_user(db, RoleName.OFFICER, dept.id)
    grievance = _create_grievance(db, dept, user)
    doc = _create_document_with_ocr(db, grievance, user, ocr_status="COMPLETED", extracted_text="Audit test text")
    headers = _auth_headers(user)

    # 1. Create translation
    res = client.post(
        f"/api/v1/documents/{doc.id}/translate",
        headers=headers,
        json={"source_language": "kn", "target_language": "en"},
    )
    assert res.status_code == 201
    t_id = uuid.UUID(res.json()["id"])

    # Verify creation audit log
    log1 = db.query(ActivityLog).filter_by(
        action="DOCUMENT_TRANSLATION_CREATED",
        entity_id=t_id,
    ).first()
    assert log1 is not None
    assert log1.grievance_id == grievance.id

    # 2. Review translation
    client.patch(
        f"/api/v1/translations/{t_id}/review",
        headers=headers,
        json={"corrected_text": "Reviewed text for audit."},
    )
    log2 = db.query(ActivityLog).filter_by(
        action="DOCUMENT_TRANSLATION_REVIEWED",
        entity_id=t_id,
    ).first()
    assert log2 is not None

    # 3. Approve translation
    client.post(
        f"/api/v1/translations/{t_id}/approve",
        headers=headers,
    )
    log3 = db.query(ActivityLog).filter_by(
        action="DOCUMENT_TRANSLATION_APPROVED",
        entity_id=t_id,
    ).first()
    assert log3 is not None


# ===========================================================================
# BONUS: Engine Status Endpoint
# ===========================================================================

@skip_if_no_db
def test_translation_engine_status_endpoint(db: Session):
    dept = _create_dept(db, "TENG")
    user = _create_user(db, RoleName.OFFICER, dept.id)
    headers = _auth_headers(user)

    res = client.get("/api/v1/translations/engine/status", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert "provider_name" in data
    assert "supported_pairs" in data
    assert "is_local" in data
    assert data["is_local"] is True

