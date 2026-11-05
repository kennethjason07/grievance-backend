"""
OCR Stage 1 Test Suite — JanSetu Grievance System

Covers all 19 required categories:
1.  Text-based PDF extraction
2.  Scanned PDF OCR (mocked Tesseract)
3.  PNG OCR (mocked Tesseract)
4.  JPEG OCR (mocked Tesseract)
5.  DOCX text extraction
6.  TXT extraction
7.  Kannada OCR (mocked language)
8.  English OCR (mocked Tesseract)
9.  Mixed-language document (eng+kan mocked)
10. Corrupted PDF → FAILED
11. Unsupported DOC format → FAILED
12. Missing Tesseract → FAILED
13. Missing language traineddata → FAILED
14. OCR failure handling (pytesseract raises RuntimeError)
15. Cross-department access denial → 403
16. OCR retry after FAILED
17. Original file unchanged after OCR
18. DB transaction consistency: extracted_text ≠ reviewed_text; audit log recorded
19. PDF page count exceeds MAX_OCR_PAGES → FAILED

All tests run against grievance_test_db only.
Tesseract is NOT installed on this machine; tests that need it use monkeypatching.
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


# ---------------------------------------------------------------------------
# Shared session-scoped fixtures
# ---------------------------------------------------------------------------

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


def _create_dept(db: Session, prefix: str = "OCR") -> Department:
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
        title="OCR Test Grievance",
        description="Grievance for OCR test suite",
        source="IN_PERSON",
        original_language="en",
        suggested_department_id=dept.id,
        confirmed_department_id=dept.id,
        status="NEW",
        priority="MEDIUM",
        created_by=user.id,
    )
    db.add(g)
    db.flush()
    return g


def _upload_file(grievance_id, headers, filename: str, content: bytes, mime: str, doc_type="EVIDENCE") -> dict:
    """Helper: upload a document and return the JSON response dict."""
    res = client.post(
        f"/api/v1/grievances/{grievance_id}/documents",
        headers=headers,
        files={"file": (filename, io.BytesIO(content), mime)},
        data={"document_type": doc_type, "language": "en"},
    )
    assert res.status_code == 201, f"Upload failed: {res.text}"
    return res.json()


# ---------------------------------------------------------------------------
# Synthetic file builders
# ---------------------------------------------------------------------------

def _make_text_pdf(text_content: str = "Hello OCR World") -> bytes:
    """Create a minimal valid PDF with embedded text using PyMuPDF."""
    import pymupdf
    buf = io.BytesIO()
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text_content, fontsize=14)
    buf.write(doc.tobytes())
    buf.seek(0)
    return buf.read()


def _make_blank_pdf(page_count: int = 1) -> bytes:
    """Create a PDF with blank (no text) pages to simulate scanned pages."""
    import pymupdf
    buf = io.BytesIO()
    doc = pymupdf.open()
    for _ in range(page_count):
        doc.new_page()  # blank page — no text
    buf.write(doc.tobytes())
    buf.seek(0)
    return buf.read()


def _make_png_image() -> bytes:
    """Create a simple 100×50 white PNG with Pillow."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (100, 50), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((5, 10), "OCR", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def _make_jpeg_image() -> bytes:
    """Create a simple 100×50 white JPEG with Pillow."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (100, 50), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((5, 10), "JAN", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    return buf.read()


def _make_docx(text_content: str = "Hello from DOCX") -> bytes:
    """Create an in-memory DOCX with python-docx."""
    from docx import Document as DocxDocument
    doc = DocxDocument()
    doc.add_paragraph(text_content)
    doc.add_paragraph("Second paragraph for testing.")
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


def _make_oversized_pdf() -> bytes:
    """Create a PDF with more pages than MAX_OCR_PAGES (default 20)."""
    import pymupdf
    max_pages = settings.MAX_OCR_PAGES
    buf = io.BytesIO()
    doc = pymupdf.open()
    for _ in range(max_pages + 1):  # one over the limit
        doc.new_page()
    buf.write(doc.tobytes())
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Monkeypatch helpers for Tesseract simulation
# ---------------------------------------------------------------------------

def _mock_tesseract_present(monkeypatch, ocr_text="Mocked OCR Text", languages=None):
    """Patch pytesseract functions to simulate Tesseract being installed."""
    if languages is None:
        languages = ["eng", "kan", "osd"]

    monkeypatch.setattr(
        "app.services.ocr_service.pytesseract.get_tesseract_version",
        lambda: "5.3.0",
    )
    monkeypatch.setattr(
        "app.services.ocr_service.pytesseract.get_languages",
        lambda: languages,
    )
    monkeypatch.setattr(
        "app.services.ocr_service.pytesseract.image_to_string",
        lambda *a, **kw: ocr_text,
    )
    monkeypatch.setattr(
        "app.services.ocr_service.pytesseract.image_to_data",
        lambda *a, **kw: {"conf": ["85.0", "90.0", "92.0", "-1"]},
    )


def _mock_tesseract_absent(monkeypatch):
    """Patch pytesseract.get_tesseract_version to simulate Tesseract not installed."""
    from pytesseract import TesseractNotFoundError

    def _raise():
        raise TesseractNotFoundError()

    monkeypatch.setattr(
        "app.services.ocr_service.pytesseract.get_tesseract_version",
        lambda: _raise(),
    )


# ===========================================================================
# TEST 1: Text-based PDF extraction (embedded text via PyMuPDF)
# ===========================================================================

@skip_if_no_db
def test_ocr_text_pdf_extraction(db: Session):
    """PDF with embedded text: OCR must succeed via PyMuPDF without Tesseract."""
    dept = _create_dept(db, "TPDF")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    pdf_bytes = _make_text_pdf("Embedded OCR Test Content 1234567890")
    upload = _upload_file(grievance.id, headers, "text_doc.pdf", pdf_bytes, "application/pdf")
    doc_id = upload["id"]

    res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ocr_status"] == "COMPLETED"
    assert "Embedded OCR Test Content" in (data.get("extracted_text") or "")
    assert data.get("ocr_error") is None


# ===========================================================================
# TEST 2: Scanned PDF OCR (blank PDF pages → Tesseract path, mocked)
# ===========================================================================

@skip_if_no_db
def test_ocr_scanned_pdf(db: Session, monkeypatch):
    """Blank PDF pages trigger OCR path; with mocked Tesseract → COMPLETED/NEEDS_REVIEW."""
    _mock_tesseract_present(monkeypatch, ocr_text="Scanned Page Text")

    dept = _create_dept(db, "SPDF")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    pdf_bytes = _make_blank_pdf(page_count=1)
    upload = _upload_file(grievance.id, headers, "scanned.pdf", pdf_bytes, "application/pdf")
    doc_id = upload["id"]

    res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    # Mocked Tesseract present → should not be FAILED; OCR ran
    assert data["ocr_status"] in ("COMPLETED", "NEEDS_REVIEW")
    assert data.get("extracted_text") is not None


# ===========================================================================
# TEST 3: PNG OCR (mocked Tesseract)
# ===========================================================================

@skip_if_no_db
def test_ocr_png_image(db: Session, monkeypatch):
    """PNG image file triggers image OCR path with mocked Tesseract."""
    _mock_tesseract_present(monkeypatch, ocr_text="PNG OCR Result Text")

    dept = _create_dept(db, "TPNG")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    png_bytes = _make_png_image()
    upload = _upload_file(grievance.id, headers, "evidence.png", png_bytes, "image/png")
    doc_id = upload["id"]

    res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ocr_status"] in ("COMPLETED", "NEEDS_REVIEW")
    assert "PNG OCR Result Text" in (data.get("extracted_text") or "")


# ===========================================================================
# TEST 4: JPEG OCR (mocked Tesseract)
# ===========================================================================

@skip_if_no_db
def test_ocr_jpeg_image(db: Session, monkeypatch):
    """JPEG image file triggers image OCR path with mocked Tesseract."""
    _mock_tesseract_present(monkeypatch, ocr_text="JPEG OCR Result Text")

    dept = _create_dept(db, "TJPG")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    jpg_bytes = _make_jpeg_image()
    upload = _upload_file(grievance.id, headers, "photo.jpg", jpg_bytes, "image/jpeg")
    doc_id = upload["id"]

    res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ocr_status"] in ("COMPLETED", "NEEDS_REVIEW")
    assert "JPEG OCR Result Text" in (data.get("extracted_text") or "")


# ===========================================================================
# TEST 5: DOCX text extraction (no Tesseract needed)
# ===========================================================================

@skip_if_no_db
def test_ocr_docx_extraction(db: Session):
    """DOCX file is extracted via python-docx without any Tesseract dependency."""
    dept = _create_dept(db, "TDOX")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    docx_bytes = _make_docx("Official DOCX complaint text with aadhaar reference")
    upload = _upload_file(
        grievance.id, headers, "complaint.docx", docx_bytes,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    doc_id = upload["id"]

    res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ocr_status"] == "COMPLETED"
    extracted = data.get("extracted_text") or ""
    assert "Official DOCX complaint text" in extracted
    assert "Second paragraph" in extracted


# ===========================================================================
# TEST 6: TXT extraction (no Tesseract needed)
# ===========================================================================

@skip_if_no_db
def test_ocr_txt_extraction(db: Session):
    """Plain .txt files are read directly without OCR."""
    dept = _create_dept(db, "TTXT")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    txt_content = b"This is a plain text grievance statement. Reference ID: TXT-9876."
    upload = _upload_file(grievance.id, headers, "statement.txt", txt_content, "text/plain")
    doc_id = upload["id"]

    res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ocr_status"] == "COMPLETED"
    assert "plain text grievance statement" in (data.get("extracted_text") or "")


# ===========================================================================
# TEST 7: Kannada OCR (mocked Tesseract with 'kan' language)
# ===========================================================================

@skip_if_no_db
def test_ocr_kannada_language(db: Session, monkeypatch):
    """Kannada language ('kan') is passed correctly to Tesseract and accepted."""
    mock_kannada_text = "ಕನ್ನಡ ಪರೀಕ್ಷೆ ಪಠ್ಯ"
    _mock_tesseract_present(monkeypatch, ocr_text=mock_kannada_text, languages=["eng", "kan"])

    captured_langs = []

    original_image_to_string = __import__(
        "pytesseract", fromlist=["image_to_string"]
    ).image_to_string

    def capture_lang_image_to_string(*args, lang=None, **kwargs):
        captured_langs.append(lang)
        return mock_kannada_text

    monkeypatch.setattr(
        "app.services.ocr_service.pytesseract.image_to_string",
        capture_lang_image_to_string,
    )

    dept = _create_dept(db, "TKAN")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    png_bytes = _make_png_image()
    upload = _upload_file(grievance.id, headers, "kannada_doc.png", png_bytes, "image/png")
    doc_id = upload["id"]

    res = client.post(
        f"/api/v1/documents/{doc_id}/ocr",
        headers=headers,
        json={"languages": "kan"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ocr_status"] in ("COMPLETED", "NEEDS_REVIEW")
    assert data.get("ocr_language") == "kan"
    # Verify 'kan' was actually passed to pytesseract
    assert any("kan" in (l or "") for l in captured_langs)


# ===========================================================================
# TEST 8: English OCR (mocked Tesseract with 'eng')
# ===========================================================================

@skip_if_no_db
def test_ocr_english_language(db: Session, monkeypatch):
    """English language ('eng') OCR processes correctly with high confidence."""
    _mock_tesseract_present(monkeypatch, ocr_text="English test document content", languages=["eng"])

    dept = _create_dept(db, "TENG")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    png_bytes = _make_png_image()
    upload = _upload_file(grievance.id, headers, "english_doc.png", png_bytes, "image/png")
    doc_id = upload["id"]

    res = client.post(
        f"/api/v1/documents/{doc_id}/ocr",
        headers=headers,
        json={"languages": "eng"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ocr_status"] in ("COMPLETED", "NEEDS_REVIEW")
    assert "English test document" in (data.get("extracted_text") or "")
    assert data.get("ocr_language") == "eng"


# ===========================================================================
# TEST 9: Mixed-language document (eng+kan)
# ===========================================================================

@skip_if_no_db
def test_ocr_mixed_language(db: Session, monkeypatch):
    """eng+kan multi-language OCR request is processed correctly."""
    mixed_text = "English content ಕನ್ನಡ ಪಠ್ಯ"
    _mock_tesseract_present(monkeypatch, ocr_text=mixed_text, languages=["eng", "kan"])

    dept = _create_dept(db, "TMIX")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    png_bytes = _make_png_image()
    upload = _upload_file(grievance.id, headers, "mixed_lang.png", png_bytes, "image/png")
    doc_id = upload["id"]

    res = client.post(
        f"/api/v1/documents/{doc_id}/ocr",
        headers=headers,
        json={"languages": "eng+kan"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ocr_language"] == "eng+kan"
    assert data["ocr_status"] in ("COMPLETED", "NEEDS_REVIEW")


# ===========================================================================
# TEST 10: Corrupted PDF → FAILED
# ===========================================================================

@skip_if_no_db
def test_ocr_corrupted_pdf(db: Session):
    """File with .pdf extension but invalid bytes must result in FAILED status."""
    dept = _create_dept(db, "TCPDF")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    # Valid PDF magic bytes (%PDF) but completely garbled body
    corrupt_bytes = b"%PDF-1.4 corrupted invalid content \x00\x01\x02\x03" + b"X" * 50
    upload = _upload_file(grievance.id, headers, "corrupt.pdf", corrupt_bytes, "application/pdf")
    doc_id = upload["id"]

    res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ocr_status"] == "FAILED"
    assert data.get("ocr_error") is not None


# ===========================================================================
# TEST 11: Unsupported DOC format → FAILED
# ===========================================================================

@skip_if_no_db
def test_ocr_unsupported_doc_format(db: Session):
    """Legacy .doc format must be rejected with a clear unsupported error message."""
    dept = _create_dept(db, "TDOC")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    # Craft minimal valid DOC magic bytes (OLE compound document)
    doc_magic = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 500
    upload = _upload_file(grievance.id, headers, "legacy.doc", doc_magic, "application/msword")
    doc_id = upload["id"]

    res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ocr_status"] == "FAILED"
    error = data.get("ocr_error") or ""
    assert "doc" in error.lower() or "unsupported" in error.lower() or "external" in error.lower()


# ===========================================================================
# TEST 12: Missing Tesseract → FAILED for image files
# ===========================================================================

@skip_if_no_db
def test_ocr_missing_tesseract_fails_image(db: Session, monkeypatch):
    """When Tesseract is not installed, image OCR must FAIL with clear message."""
    _mock_tesseract_absent(monkeypatch)

    dept = _create_dept(db, "TNOT")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    png_bytes = _make_png_image()
    upload = _upload_file(grievance.id, headers, "needs_tess.png", png_bytes, "image/png")
    doc_id = upload["id"]

    res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ocr_status"] == "FAILED"
    error = data.get("ocr_error") or ""
    assert "tesseract" in error.lower() or "not installed" in error.lower()


# ===========================================================================
# TEST 13: Missing language traineddata → FAILED
# ===========================================================================

@skip_if_no_db
def test_ocr_missing_traineddata_fails(db: Session, monkeypatch):
    """When Tesseract is installed but 'kan' traineddata is missing, OCR must FAIL."""
    # Only 'eng' available, 'kan' is NOT installed
    _mock_tesseract_present(monkeypatch, ocr_text="English only", languages=["eng", "osd"])

    dept = _create_dept(db, "TNTD")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    png_bytes = _make_png_image()
    upload = _upload_file(grievance.id, headers, "kannada_missing.png", png_bytes, "image/png")
    doc_id = upload["id"]

    # Request Kannada language that isn't in the installed list
    res = client.post(
        f"/api/v1/documents/{doc_id}/ocr",
        headers=headers,
        json={"languages": "kan"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ocr_status"] == "FAILED"
    error = data.get("ocr_error") or ""
    assert "kan" in error.lower() or "traineddata" in error.lower() or "missing" in error.lower()


# ===========================================================================
# TEST 14: OCR failure handling (pytesseract.image_to_string raises RuntimeError)
# ===========================================================================

@skip_if_no_db
def test_ocr_runtime_failure_handled(db: Session, monkeypatch):
    """When pytesseract raises a RuntimeError, OCR must catch it and store FAILED status."""
    # Tesseract appears installed...
    monkeypatch.setattr(
        "app.services.ocr_service.pytesseract.get_tesseract_version",
        lambda: "5.3.0",
    )
    monkeypatch.setattr(
        "app.services.ocr_service.pytesseract.get_languages",
        lambda: ["eng", "kan"],
    )
    # ...but image_to_string crashes
    def _crash(*args, **kwargs):
        raise RuntimeError("Simulated Tesseract crash during processing")

    monkeypatch.setattr(
        "app.services.ocr_service.pytesseract.image_to_string",
        _crash,
    )

    dept = _create_dept(db, "TCRSH")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    png_bytes = _make_png_image()
    upload = _upload_file(grievance.id, headers, "crash_doc.png", png_bytes, "image/png")
    doc_id = upload["id"]

    res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ocr_status"] == "FAILED"
    error = data.get("ocr_error") or ""
    assert "crash" in error.lower() or "simulated" in error.lower() or len(error) > 0


# ===========================================================================
# TEST 15: Cross-department access denial
# ===========================================================================

@skip_if_no_db
def test_ocr_cross_department_denied(db: Session):
    """Department B user must not trigger OCR on Department A's document."""
    dept_a = _create_dept(db, "TDPA")
    dept_b = _create_dept(db, "TDPB")
    user_a = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept_a.id)
    user_b = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept_b.id)

    grievance_a = _create_grievance(db, dept_a, user_a)
    headers_a = _auth_headers(user_a)
    headers_b = _auth_headers(user_b)

    txt_content = b"Department A sensitive document content."
    upload = _upload_file(grievance_a.id, headers_a, "dept_a_doc.txt", txt_content, "text/plain")
    doc_id = upload["id"]

    # Department B user tries to trigger OCR on Dept A's document
    res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers_b)
    assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"


# ===========================================================================
# TEST 16: OCR retry after FAILED
# ===========================================================================

@skip_if_no_db
def test_ocr_retry_after_failure(db: Session):
    """After FAILED status, re-triggering OCR on a text PDF must transition to COMPLETED."""
    dept = _create_dept(db, "TRTY")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    # Create a document and manually set it to FAILED in DB
    pdf_bytes = _make_text_pdf("Retry OCR Test Text Content with sufficient embedded characters for testing")
    upload = _upload_file(grievance.id, headers, "retry_doc.pdf", pdf_bytes, "application/pdf")
    doc_id = upload["id"]

    # Manually inject a FAILED status directly into the DB
    doc_record = db.get(Document, uuid.UUID(doc_id))
    doc_record.ocr_status = "FAILED"
    doc_record.ocr_error = "Simulated previous failure"
    db.flush()

    # Retry: trigger OCR again
    res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    # Text-based PDF should complete without Tesseract
    assert data["ocr_status"] in ("COMPLETED", "NEEDS_REVIEW")
    # Error should be cleared on successful re-run
    assert data.get("ocr_error") is None


# ===========================================================================
# TEST 17: Original file unchanged after OCR
# ===========================================================================

@skip_if_no_db
def test_ocr_original_file_unchanged(db: Session):
    """The original uploaded binary must not be modified by OCR processing."""
    dept = _create_dept(db, "TORG")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    original_text = "ORIGINAL CONTENT THAT MUST REMAIN INTACT AFTER OCR PROCESSING"
    txt_bytes = original_text.encode("utf-8")
    upload = _upload_file(grievance.id, headers, "original.txt", txt_bytes, "text/plain")
    doc_id = upload["id"]

    # Read the storage path from DB (not from API — internal path)
    doc_record = db.get(Document, uuid.UUID(doc_id))
    storage_path = Path(doc_record.storage_path)

    # Capture file hash before OCR
    before_bytes = storage_path.read_bytes()

    # Run OCR
    res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers)
    assert res.status_code == 200, res.text

    # Verify file is byte-for-byte identical
    after_bytes = storage_path.read_bytes()
    assert before_bytes == after_bytes, "Original file was modified by OCR processing!"
    assert after_bytes == txt_bytes


# ===========================================================================
# TEST 18: DB transaction consistency: extracted_text ≠ reviewed_text; audit log
# ===========================================================================

@skip_if_no_db
def test_ocr_db_consistency_and_audit_log(db: Session):
    """
    After OCR completion:
    - extracted_text and reviewed_text are stored separately
    - reviewed_text can be edited without touching extracted_text
    - Audit log entry DOCUMENT_OCR_PROCESSED is created
    - Audit log entry DOCUMENT_OCR_REVIEWED is created on review
    """
    dept = _create_dept(db, "TAUL")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    # Upload and trigger OCR
    txt_bytes = b"Audit log consistency test document content."
    upload = _upload_file(grievance.id, headers, "audit_doc.txt", txt_bytes, "text/plain")
    doc_id = upload["id"]

    ocr_res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers)
    assert ocr_res.status_code == 200
    assert ocr_res.json()["ocr_status"] == "COMPLETED"

    # Verify audit log entry
    audit = db.query(ActivityLog).filter_by(
        action="DOCUMENT_OCR_PROCESSED",
        entity_id=uuid.UUID(doc_id),
    ).first()
    assert audit is not None
    assert audit.grievance_id == grievance.id

    # Verify DB columns: extracted_text exists, reviewed_text initialized from it
    doc_record = db.get(Document, uuid.UUID(doc_id))
    assert doc_record.extracted_text is not None
    assert doc_record.reviewed_text is not None
    original_extracted = doc_record.extracted_text

    # Save a corrected reviewed text via PATCH
    corrected = "MANUALLY CORRECTED TEXT — different from extracted"
    review_res = client.patch(
        f"/api/v1/documents/{doc_id}/ocr/review",
        headers=headers,
        json={"reviewed_text": corrected, "mark_status": "COMPLETED"},
    )
    assert review_res.status_code == 200, review_res.text

    # Re-fetch from DB and verify separation
    db.refresh(doc_record)
    assert doc_record.reviewed_text == corrected
    assert doc_record.extracted_text == original_extracted, "extracted_text must NOT be overwritten by review"
    assert doc_record.extracted_text != doc_record.reviewed_text

    # Verify review audit log
    review_audit = db.query(ActivityLog).filter_by(
        action="DOCUMENT_OCR_REVIEWED",
        entity_id=uuid.UUID(doc_id),
    ).first()
    assert review_audit is not None


# ===========================================================================
# TEST 19: PDF page count exceeds MAX_OCR_PAGES → FAILED
# ===========================================================================

@skip_if_no_db
def test_ocr_pdf_page_limit_exceeded(db: Session):
    """PDF with more pages than MAX_OCR_PAGES must fail with page limit message."""
    dept = _create_dept(db, "TPGL")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    # Build a PDF with max_pages + 1 pages
    oversized_pdf = _make_oversized_pdf()
    upload = _upload_file(grievance.id, headers, "oversized.pdf", oversized_pdf, "application/pdf")
    doc_id = upload["id"]

    res = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ocr_status"] == "FAILED"
    error = (data.get("ocr_error") or "").lower()
    assert "page" in error or "limit" in error or "maximum" in error or "exceed" in error


# ===========================================================================
# BONUS: OCR engine status endpoint
# ===========================================================================

@skip_if_no_db
def test_ocr_engine_status_endpoint(db: Session):
    """GET /api/v1/documents/ocr/status returns Tesseract status for authenticated users."""
    dept = _create_dept(db, "TSTA")
    user = _create_user(db, RoleName.OFFICER, dept.id)
    headers = _auth_headers(user)

    res = client.get("/api/v1/documents/ocr/status", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert "installed" in data
    assert isinstance(data["installed"], bool)
    assert "languages" in data


# ===========================================================================
# BONUS: GET OCR result authorization (dept isolation on read)
# ===========================================================================

@skip_if_no_db
def test_ocr_get_result_cross_dept_denied(db: Session):
    """GET OCR result for a document in another department must return 403."""
    dept_a = _create_dept(db, "TDRA")
    dept_b = _create_dept(db, "TDRB")
    user_a = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept_a.id)
    user_b = _create_user(db, RoleName.OFFICER, dept_b.id)

    grievance_a = _create_grievance(db, dept_a, user_a)
    headers_a = _auth_headers(user_a)
    headers_b = _auth_headers(user_b)

    txt_content = b"Sensitive dept A document."
    upload = _upload_file(grievance_a.id, headers_a, "secret.txt", txt_content, "text/plain")
    doc_id = upload["id"]

    # Dept B user tries to read OCR result
    res = client.get(f"/api/v1/documents/{doc_id}/ocr", headers=headers_b)
    assert res.status_code == 403


# ===========================================================================
# BONUS: Review rejected when OCR not completed
# ===========================================================================

@skip_if_no_db
def test_ocr_review_rejected_when_not_completed(db: Session):
    """PATCH /ocr/review must return 400 when document is in NOT_STARTED status."""
    dept = _create_dept(db, "TRVJ")
    user = _create_user(db, RoleName.DATA_ENTRY_OPERATOR, dept.id)
    grievance = _create_grievance(db, dept, user)
    headers = _auth_headers(user)

    txt_bytes = b"Review rejection test content."
    upload = _upload_file(grievance.id, headers, "not_ocrd.txt", txt_bytes, "text/plain")
    doc_id = upload["id"]

    # Don't trigger OCR — document is in NOT_STARTED state
    res = client.patch(
        f"/api/v1/documents/{doc_id}/ocr/review",
        headers=headers,
        json={"reviewed_text": "Some correction", "mark_status": "COMPLETED"},
    )
    assert res.status_code == 400
    assert "review" in res.json().get("detail", "").lower() or "status" in res.json().get("detail", "").lower()

