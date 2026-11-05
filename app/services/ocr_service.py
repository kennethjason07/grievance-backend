"""
OCR Service for JanSetu Grievance System.
Implements Stage 1 Document Text Extraction:
- Digital/scanned/mixed PDF extraction via PyMuPDF + Tesseract
- Image preprocessing (grayscale, noise reduction, thresholding, deskewing) via OpenCV + Tesseract
- DOCX text extraction via python-docx
- Plain text ingestion
- Strict resource limits, error tracking, and separation of raw vs reviewed text
"""
import io
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import pymupdf
import pytesseract
from docx import Document as DocxDocument
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.document import Document
from app.models.grievance import Grievance
from app.models.user import User
from app.schemas.document import DocumentOcrReviewRequest
from app.services.audit import log_activity
from app.services.grievance_service import _check_grievance_department_access

logger = logging.getLogger(__name__)
settings = get_settings()


def configure_pytesseract() -> None:
    """Apply environment settings to pytesseract runtime."""
    if settings.TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD
    if settings.TESSDATA_PREFIX:
        os.environ["TESSDATA_PREFIX"] = settings.TESSDATA_PREFIX


def get_tesseract_status() -> Dict[str, Any]:
    """Check Tesseract installation and available language traineddata."""
    configure_pytesseract()
    installed = False
    version = None
    languages: List[str] = []

    try:
        ver = pytesseract.get_tesseract_version()
        if ver is not None:
            installed = True
            version = str(ver)
            languages = pytesseract.get_languages()
    except Exception as e:
        logger.debug(f"Tesseract is not installed or accessible: {e}")

    return {
        "installed": installed,
        "version": version,
        "languages": languages,
        "tesseract_cmd": settings.TESSERACT_CMD or pytesseract.pytesseract.tesseract_cmd,
        "supports_english": "eng" in languages,
        "supports_kannada": "kan" in languages,
    }


def preprocess_image_for_ocr(image_bytes: bytes) -> np.ndarray:
    """
    OpenCV preprocessing pipeline for scanned documents and images:
    1. Decode image bytes
    2. Convert to grayscale
    3. Gaussian blur for noise suppression
    4. Otsu binarization / thresholding
    5. Deskewing when practical
    """
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Corrupted image data could not be decoded by OpenCV.")

    # 1. Grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 2. Gaussian noise reduction
    denoised = cv2.GaussianBlur(gray, (3, 3), 0)

    # 3. Thresholding (Otsu's binarization)
    _, thresh = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 4. Deskewing
    try:
        coords = np.column_stack(np.where(thresh == 0))
        if len(coords) > 50:
            angle = cv2.minAreaRect(coords)[-1]
            if angle < -45:
                angle = -(90 + angle)
            elif angle > 45:
                angle = 90 - angle
            if 0.5 < abs(angle) < 45:
                (h, w) = thresh.shape[:2]
                center = (w // 2, h // 2)
                M = cv2.getRotationMatrix2D(center, angle, 1.0)
                thresh = cv2.warpAffine(
                    thresh, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
                )
    except Exception as e:
        logger.debug(f"Deskew skipped: {e}")

    return thresh


def run_ocr_on_image(image_bytes: bytes, languages: str) -> Tuple[str, Optional[float]]:
    """Execute OpenCV preprocessing and Tesseract OCR on raw image bytes."""
    processed_img = preprocess_image_for_ocr(image_bytes)
    configure_pytesseract()

    # Extract text with language configuration
    text_output = pytesseract.image_to_string(processed_img, lang=languages)

    # Attempt to calculate mean confidence from OCR data
    confidence = None
    try:
        data = pytesseract.image_to_data(processed_img, lang=languages, output_type=pytesseract.Output.DICT)
        confs = [float(c) for c in data.get("conf", []) if float(c) > 0]
        if confs:
            confidence = round(sum(confs) / len(confs), 2)
    except Exception:
        pass

    return text_output.strip(), confidence


def extract_text_from_pdf(
    file_path: Path,
    languages: str,
) -> Tuple[str, List[Dict[str, Any]], bool]:
    """
    PyMuPDF-first extraction with fallback to rendered page OCR:
    - Attempt embedded text extraction.
    - If a page has < 30 non-whitespace chars, treat as scanned and run OCR.
    - Handles mixed PDFs containing both digital and scanned pages.
    """
    pages_data: List[Dict[str, Any]] = []
    combined_paragraphs: List[str] = []
    has_scanned_pages = False

    tess_status = get_tesseract_status()

    try:
        doc = pymupdf.open(str(file_path))
    except Exception as exc:
        raise ValueError(f"Corrupted or invalid PDF file: {str(exc)}")

    try:
        page_count = len(doc)
        if page_count > settings.MAX_OCR_PAGES:
            raise ValueError(
                f"PDF page count ({page_count}) exceeds maximum processing limit of {settings.MAX_OCR_PAGES} pages."
            )

        for page_idx in range(page_count):
            page = doc[page_idx]
            page_num = page_idx + 1

            # 1. Attempt embedded text extraction
            embedded_text = page.get_text("text").strip()

            # Consider meaningful text if length >= 30 chars
            if len(embedded_text) >= 30:
                pages_data.append({
                    "page_number": page_num,
                    "extracted_text": embedded_text,
                    "method": "embedded",
                    "confidence": 100.0,
                    "char_count": len(embedded_text),
                })
                combined_paragraphs.append(embedded_text)
            else:
                # 2. Page appears scanned; render into pixmap and run OCR
                has_scanned_pages = True
                dpi = min(settings.MAX_RENDER_DPI, 200)
                pix = page.get_pixmap(dpi=dpi)
                img_bytes = pix.tobytes("png")

                if not tess_status["installed"]:
                    # Tesseract is not installed on the system
                    error_msg = "[Scanned page detected, but Tesseract OCR is not installed or configured on server]"
                    pages_data.append({
                        "page_number": page_num,
                        "extracted_text": error_msg,
                        "method": "scanned_ocr_unavailable",
                        "confidence": 0.0,
                        "char_count": 0,
                    })
                    combined_paragraphs.append(error_msg)
                else:
                    ocr_text, conf = run_ocr_on_image(img_bytes, languages)
                    pages_data.append({
                        "page_number": page_num,
                        "extracted_text": ocr_text,
                        "method": "ocr",
                        "confidence": conf,
                        "char_count": len(ocr_text),
                    })
                    combined_paragraphs.append(ocr_text)

    finally:
        doc.close()

    full_text = "\n\n".join(p for p in combined_paragraphs if p)
    return full_text, pages_data, has_scanned_pages


def extract_text_from_docx(file_path: Path) -> Tuple[str, List[Dict[str, Any]]]:
    """Extract structured text directly from DOCX via python-docx."""
    try:
        doc = DocxDocument(str(file_path))
    except Exception as exc:
        raise ValueError(f"Corrupted or invalid DOCX document: {str(exc)}")

    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]

    # Extract tables
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
            if row_text:
                paragraphs.append(row_text)

    full_text = "\n\n".join(paragraphs)
    pages_data = [{
        "page_number": 1,
        "extracted_text": full_text,
        "method": "docx",
        "confidence": 100.0,
        "char_count": len(full_text),
    }]
    return full_text, pages_data


def extract_text_from_txt(file_path: Path) -> Tuple[str, List[Dict[str, Any]]]:
    """Read plain text directly from validated UTF-8 TXT files."""
    try:
        content = file_path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError:
        content = file_path.read_text(encoding="latin-1", errors="replace").strip()

    pages_data = [{
        "page_number": 1,
        "extracted_text": content,
        "method": "txt",
        "confidence": 100.0,
        "char_count": len(content),
    }]
    return content, pages_data


def extract_text_from_file_path(
    file_path: Path,
    languages: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Core extraction dispatcher for a physical file:
    - PDF: PyMuPDF embedded text first, with scanned page detection and OCR fallback.
    - Images (.png, .jpg, .jpeg): OpenCV preprocessing + Tesseract OCR.
    - DOCX: python-docx paragraph and table extraction.
    - TXT: UTF-8 plain text extraction.
    """
    selected_langs = languages.strip() if languages and languages.strip() else settings.OCR_DEFAULT_LANGUAGES
    ext = file_path.suffix.lower()

    if ext == ".doc":
        return {
            "extracted_text": "",
            "pages_data": [],
            "ocr_status": "FAILED",
            "ocr_error": "Legacy .doc format requires external conversion. Please upload a .docx or .pdf version.",
            "languages": selected_langs,
        }

    extracted_text = ""
    pages_data: List[Dict[str, Any]] = []
    final_status = "COMPLETED"
    error_message: Optional[str] = None

    try:
        if ext == ".pdf":
            extracted_text, pages_data, has_scanned = extract_text_from_pdf(
                file_path=file_path,
                languages=selected_langs,
            )
            if any(p.get("method") == "scanned_ocr_unavailable" for p in pages_data):
                if all(p.get("method") == "scanned_ocr_unavailable" for p in pages_data):
                    final_status = "FAILED"
                    error_message = (
                        "Tesseract OCR engine is not installed or configured on the server. "
                        "Please install Tesseract-OCR and configure TESSERACT_CMD."
                    )
                else:
                    final_status = "NEEDS_REVIEW"
                    error_message = "Partial text extracted from digital pages. Scanned pages require Tesseract OCR installation."
            elif has_scanned:
                final_status = "NEEDS_REVIEW"

        elif ext in {".png", ".jpg", ".jpeg"}:
            tess_status = get_tesseract_status()
            if not tess_status["installed"]:
                final_status = "FAILED"
                error_message = (
                    "Tesseract OCR engine is not installed or configured on the server. "
                    "Please install Tesseract-OCR and configure TESSERACT_CMD."
                )
            else:
                req_langs = [l.strip() for l in selected_langs.split("+") if l.strip()]
                missing_langs = [l for l in req_langs if l not in tess_status["languages"]]
                if missing_langs:
                    final_status = "FAILED"
                    error_message = (
                        f"Tesseract language traineddata for {missing_langs} is missing. "
                        f"Please install {', '.join(missing_langs)}.traineddata."
                    )
                else:
                    img_bytes = file_path.read_bytes()
                    ocr_text, conf = run_ocr_on_image(img_bytes, selected_langs)
                    extracted_text = ocr_text
                    pages_data = [{
                        "page_number": 1,
                        "extracted_text": ocr_text,
                        "method": "ocr",
                        "confidence": conf,
                        "char_count": len(ocr_text),
                    }]
                    final_status = "NEEDS_REVIEW" if (conf is not None and conf < 75.0) else "COMPLETED"

        elif ext == ".docx":
            extracted_text, pages_data = extract_text_from_docx(file_path)
            final_status = "COMPLETED"

        elif ext == ".txt":
            extracted_text, pages_data = extract_text_from_txt(file_path)
            final_status = "COMPLETED"

        else:
            final_status = "FAILED"
            error_message = f"Unsupported file extension '{ext}' for text extraction."

    except Exception as exc:
        logger.exception(f"File text extraction failed for {file_path}: {exc}")
        final_status = "FAILED"
        error_message = str(exc)

    return {
        "extracted_text": extracted_text,
        "pages_data": pages_data,
        "ocr_status": final_status,
        "ocr_error": error_message,
        "languages": selected_langs,
    }


def process_document_ocr(
    db: Session,
    document_id: uuid.UUID,
    languages: Optional[str] = None,
    current_user: Optional[User] = None,
    ip_address: Optional[str] = None,
) -> Document:
    """
    Main OCR Processing pipeline:
    1. Verify document existence & department access
    2. Resolve and validate secure file path
    3. Route by file type (PDF, Images, DOCX, TXT, DOC)
    4. Execute text extraction / OCR
    5. Update document model with separated raw vs reviewed text
    6. Record audit log
    """
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with ID {document_id} not found.",
        )

    grievance = db.get(Grievance, document.grievance_id)
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated grievance not found.",
        )

    if current_user:
        _check_grievance_department_access(current_user, grievance)

    # Resolve file path securely
    base_upload_dir = Path(settings.UPLOAD_DIR).resolve()
    file_path = Path(document.storage_path).resolve()

    if not file_path.is_relative_to(base_upload_dir) or not file_path.is_file():
        document.ocr_status = "FAILED"
        document.ocr_error = "Document binary file not found on storage disk."
        db.commit()
        db.refresh(document)
        return document

    # Determine languages to use
    selected_langs = languages.strip() if languages and languages.strip() else settings.OCR_DEFAULT_LANGUAGES

    # Reject legacy .doc format per requirements
    ext = file_path.suffix.lower()
    if ext == ".doc":
        document.ocr_status = "FAILED"
        document.ocr_error = "Legacy .doc format requires external conversion. Please upload a .docx or .pdf version."
        document.ocr_processed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(document)
        return document

    # Mark as PROCESSING
    document.ocr_status = "PROCESSING"
    document.ocr_error = None
    db.commit()
    db.refresh(document)

    extraction_res = extract_text_from_file_path(file_path, selected_langs)
    extracted_text = extraction_res["extracted_text"]
    pages_data = extraction_res["pages_data"]
    final_status = extraction_res["ocr_status"]
    error_message = extraction_res["ocr_error"]

    # Persist extraction results (never overwriting original binary file!)
    document.ocr_status = final_status
    document.extracted_text = extracted_text if final_status != "FAILED" else None
    # Initialize reviewed_text equal to extracted_text initially so user can edit it
    document.reviewed_text = extracted_text if final_status != "FAILED" else None
    document.ocr_language = selected_langs
    document.ocr_pages_data = pages_data if pages_data else None
    document.ocr_processed_at = datetime.now(timezone.utc)
    document.ocr_error = error_message

    db.commit()
    db.refresh(document)

    if current_user:
        try:
            log_activity(
                db=db,
                action="DOCUMENT_OCR_PROCESSED",
                entity_type="document",
                entity_id=document.id,
                user_id=current_user.id,
                grievance_id=document.grievance_id,
                new_values={
                    "document_id": str(document.id),
                    "ocr_status": document.ocr_status,
                    "ocr_language": document.ocr_language,
                    "char_count": len(extracted_text) if extracted_text else 0,
                    "error": document.ocr_error,
                },
                ip_address=ip_address,
            )
        except Exception:
            logger.warning("Failed to record OCR processing audit log; results were committed.")

    return document


def get_document_ocr(
    db: Session,
    document_id: uuid.UUID,
    current_user: User,
) -> Document:
    """Retrieve document OCR metadata and extracted text, enforcing department isolation."""
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with ID {document_id} not found.",
        )

    grievance = db.get(Grievance, document.grievance_id)
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated grievance not found.",
        )

    _check_grievance_department_access(current_user, grievance)
    return document


def review_document_ocr(
    db: Session,
    document_id: uuid.UUID,
    review_in: DocumentOcrReviewRequest,
    current_user: User,
    ip_address: Optional[str] = None,
) -> Document:
    """
    Save manually corrected/reviewed text.
    The original extracted_text is preserved intact and distinguishable.
    """
    document = get_document_ocr(db=db, document_id=document_id, current_user=current_user)

    if document.ocr_status not in ["COMPLETED", "NEEDS_REVIEW"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot review document in '{document.ocr_status}' status. OCR must complete first.",
        )

    old_reviewed_text = document.reviewed_text
    document.reviewed_text = review_in.reviewed_text
    document.ocr_reviewed_at = datetime.now(timezone.utc)
    document.ocr_reviewed_by = current_user.id

    if review_in.mark_status in ["COMPLETED", "NEEDS_REVIEW"]:
        document.ocr_status = review_in.mark_status

    db.commit()
    db.refresh(document)

    log_activity(
        db=db,
        action="DOCUMENT_OCR_REVIEWED",
        entity_type="document",
        entity_id=document.id,
        user_id=current_user.id,
        grievance_id=document.grievance_id,
        old_values={"reviewed_text_preview": (old_reviewed_text[:100] if old_reviewed_text else None)},
        new_values={
            "reviewed_text_preview": review_in.reviewed_text[:100],
            "reviewed_by": str(current_user.id),
            "status": document.ocr_status,
        },
        ip_address=ip_address,
    )

    return document

def extract_form_entities_from_text(text: str) -> Dict[str, Any]:
    """
    Extract structured grievance form fields from OCR text:
    - Citizen Name, Phone, Email, Address
    - District, Taluk, Locality
    - Complaint Title, Category, Incident Date, Incident Location
    - Demanded Relief / Requested Action
    - Detected language and confidence scores
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not text or not text.strip():
        return {
            "citizen_name": "",
            "phone": "",
            "email": "",
            "address": "",
            "district": "Bengaluru Urban",
            "taluk": "Bengaluru South",
            "village_locality": "",
            "title": "",
            "category": "General Grievance",
            "incident_date": today_str,
            "incident_location": "",
            "description": "",
            "requested_action": "",
            "language": "english",
            "confidence_scores": {
                "citizen": "MISSING",
                "category": "NEEDS_REVIEW",
                "location": "MISSING",
                "date": "NEEDS_REVIEW",
            },
        }

    clean_text = text.strip()

    # 1. Detect language
    kannada_chars = len(re.findall(r"[\u0C80-\u0CFF]", clean_text))
    english_chars = len(re.findall(r"[A-Za-z]", clean_text))
    if kannada_chars > 30 and english_chars > 30:
        detected_lang = "bilingual"
    elif kannada_chars > 30:
        detected_lang = "kannada"
    else:
        detected_lang = "english"

    # 2. Extract Phone
    phone = ""
    phone_match = re.search(
        r"(?:(?:Mob(?:ile)?|Phone|Tel|ದೂರವಾಣಿ|ಮೊಬೈಲ್|Cell)\s*[:\-]?\s*)?(?:\+91[\s\-]?)?([6-9]\d{4}[\s\-]?\d{5}|[6-9]\d{9})\b",
        clean_text,
    )
    if phone_match:
        raw_digits = re.sub(r"[\s\-]", "", phone_match.group(1))
        if len(raw_digits) == 10:
            phone = f"+91 {raw_digits[:5]} {raw_digits[5:]}"
        else:
            phone = phone_match.group(0).strip()

    # 3. Extract Email
    email = ""
    email_match = re.search(r"\b([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,})\b", clean_text)
    if email_match:
        email = email_match.group(1).lower()

    # 4. Extract Citizen Name
    citizen_name = ""
    name_patterns = [
        r"(?:From|Applicant|Petitioner|Complainant|Name)\s*[:\-]\s*([A-Za-z\s.]{2,40})(?=\n|,|Address|Mobile|Phone|Email|No\.)",
        r"(?:ಅರ್ಜಿದಾರರು|ಹೆಸರು)\s*[:\-]\s*([^\n,]{2,40})",
        r"From\s*\n\s*([A-Za-z\s.]{3,40})\n",
    ]
    for pat in name_patterns:
        m = re.search(pat, clean_text, re.IGNORECASE)
        if m and m.group(1):
            cand = m.group(1).strip()
            cand = re.sub(r"^(Sri|Smt|Mr|Mrs|Dr|Shri)\.?\s+", "", cand, flags=re.IGNORECASE).strip()
            if cand and not re.search(r"^(Deputy|Commissioner|Police|Inspector|Department|District|Superintendent)", cand, re.IGNORECASE):
                citizen_name = cand
                break

    # 5. Extract Date
    incident_date = today_str
    date_found = False
    date_match = re.search(
        r"(?:Date|Dated|ದಿನಾಂಕ|on)\s*[:\-]?\s*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2})",
        clean_text,
        re.IGNORECASE,
    )
    if date_match:
        raw_d = date_match.group(1).strip().replace("-", "/").replace(".", "/")
        parts = raw_d.split("/")
        if len(parts) == 3:
            try:
                if len(parts[0]) == 4:
                    y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                else:
                    d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
                    if y < 100:
                        y += 2000
                incident_date = datetime(y, m, d).strftime("%Y-%m-%d")
                date_found = True
            except Exception:
                pass

    # 6. Extract Subject / Title
    title = ""
    sub_match = re.search(r"(?:Subject|Sub|ವಿಷಯ|ಕುರಿತು)\s*[:\-]\s*([^\n]+)", clean_text, re.IGNORECASE)
    if sub_match:
        title = sub_match.group(1).strip()
        title = re.sub(r"^[“\"\'\s]+|[”\"\'\s\.]+$", "", title)

    # 7. Extract Category
    lower_text = clean_text.lower()
    category = "General Grievance"
    if any(k in lower_text for k in ["noise", "loudspeaker", "music", "sound", "decibel", "ಧ್ವನಿ"]):
        category = "Public Nuisance & Noise Pollution"
    elif any(k in lower_text for k in ["cyber", "telegram", "fraud", "trading", "crypto", "upi", "online investment", "ಖಾತೆ"]):
        category = "Cyber Financial Fraud"
    elif any(k in lower_text for k in ["encroachment", "land", "fencing", "gomal", "survey no", "survey", "ಕಬಳಿಕೆ", "ಒತ್ತುವರಿ", "ಜಮೀನು"]):
        category = "Land Encroachment & Illegal Occupation"
    elif any(k in lower_text for k in ["theft", "stolen", "robbery", "burglary", "snatching", "ಕಳ್ಳತನ"]):
        category = "Theft & Burglary"
    elif any(k in lower_text for k in ["threat", "intimidation", "harassment", "extortion", "abusive", "ಬೆದರಿಕೆ"]):
        category = "Threat & Harassment"
    elif any(k in lower_text for k in ["traffic", "parking", "footpath", "pedestrian", "obstruction", "ರಸ್ತೆ"]):
        category = "Traffic & Road Obstruction"
    elif any(k in lower_text for k in ["drainage", "sewage", "garbage", "sanitation", "ನೀರು", "ತ್ಯಾಜ್ಯ"]):
        category = "Civic Amenities & Sanitation"

    if not title:
        title = f"Petition regarding {category.lower()}"

    # 8. Taluk & Locality
    taluks = ["Bengaluru South", "Bengaluru North", "Bengaluru East", "Anekal", "Yelahanka"]
    taluk = "Bengaluru South"
    for t in taluks:
        if re.search(rf"\b{re.escape(t)}\b", clean_text, re.IGNORECASE):
            taluk = t
            break

    localities = [
        "Koramangala", "Indiranagar", "Agara", "Whitefield", "HSR Layout", "Jayanagar",
        "BTM Layout", "Electronic City", "Bellandur", "Marathahalli", "Hebbal", "Yelahanka",
        "Malleshwaram", "Rajajinagar", "Basavanagudi", "Banashankari", "Shivajinagar",
    ]
    locality = ""
    for loc in localities:
        if re.search(rf"\b{re.escape(loc)}\b", clean_text, re.IGNORECASE):
            locality = loc
            break

    # 9. Address
    address = ""
    addr_match = re.search(
        r"(?:Address|Residing at|ವಿಳಾಸ)\s*[:\-]\s*([^\n]+(?:\n[^\n]+)?)(?=\n|Mobile|Phone|Email|Cell|$)",
        clean_text,
        re.IGNORECASE,
    )
    if addr_match and addr_match.group(1):
        address = " ".join(addr_match.group(1).split())
    elif locality:
        address = f"{locality}, {taluk}, Bengaluru Urban"

    # 10. Incident Location
    incident_location = ""
    loc_match = re.search(
        r"(?:at|near|location|ಸ್ಥಳ)\s*[:\-]?\s*([A-Za-z0-9\s,\-]{3,45})(?=\.|\n|, on|, dated)",
        clean_text,
        re.IGNORECASE,
    )
    if loc_match and loc_match.group(1) and not re.search(r"^(the|deputy|respected|sir|madam)", loc_match.group(1).strip(), re.IGNORECASE):
        incident_location = loc_match.group(1).strip()
    elif locality:
        incident_location = f"{locality}, {taluk}"

    # 11. Requested Action
    requested_action = ""
    action_match = re.search(r"(?:Prayer|Relief|Request|ಮನವಿ|ಕೋರಿಕೆ)\s*[:\-]\s*([^\n]+(?:\n[^\n]+)?)", clean_text, re.IGNORECASE)
    if action_match and action_match.group(1):
        requested_action = " ".join(action_match.group(1).split())
    else:
        req_line = re.search(r"(?:request(?: you| the authorities)? to [^.\n]+|take necessary action[^.\n]+)", clean_text, re.IGNORECASE)
        if req_line:
            requested_action = req_line.group(0).strip().capitalize()
        else:
            requested_action = f"Immediate inquiry and statutory action regarding {category.lower()}."

    # 12. Confidence Scores
    citizen_conf = "HIGH" if (citizen_name and phone) else ("NEEDS_REVIEW" if (citizen_name or phone) else "MISSING")
    cat_conf = "HIGH" if category != "General Grievance" else "NEEDS_REVIEW"
    loc_conf = "HIGH" if (incident_location or locality) else "MISSING"
    date_conf = "HIGH" if date_found else "NEEDS_REVIEW"

    return {
        "citizen_name": citizen_name,
        "phone": phone,
        "email": email,
        "address": address,
        "district": "Bengaluru Urban",
        "taluk": taluk,
        "village_locality": locality,
        "title": title,
        "category": category,
        "incident_date": incident_date,
        "incident_location": incident_location,
        "description": clean_text[:1000].strip(),
        "requested_action": requested_action,
        "language": detected_lang,
        "confidence_scores": {
            "citizen": citizen_conf,
            "category": cat_conf,
            "location": loc_conf,
            "date": date_conf,
        },
    }
