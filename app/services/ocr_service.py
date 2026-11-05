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

TALUK_DETECTION_RULES = [
    {
        "taluk": "Bengaluru South",
        "patterns": [
            r"ಬೆಂಗಳೂರು\s*ದಕ್ಷಿಣ",
            r"ದಕ್ಷಿಣ\s*(?:ತಾಲೂಕು|ತಾಲ್ಲೂಕು|ತಾಲೂಕಿನ|ತಾಲ್ಲೂಕಿನ|ತಾಲೂಕಿಗೆ)",
            r"Bengaluru\s*South",
            r"Bangalore\s*South",
            r"\bSouth\s*(?:Taluk|Tq|Tahsil|Tehsil)\b",
            r"ಬೆಂಗಳೂರು\s*ಸೌತ್",
        ],
    },
    {
        "taluk": "Bengaluru North",
        "patterns": [
            r"ಬೆಂಗಳೂರು\s*ಉತ್ತರ",
            r"ಉತ್ತರ\s*(?:ತಾಲೂಕು|ತಾಲ್ಲೂಕು|ತಾಲೂಕಿನ|ತಾಲ್ಲೂಕಿನ|ತಾಲೂಕಿಗೆ)",
            r"Bengaluru\s*North",
            r"Bangalore\s*North",
            r"\bNorth\s*(?:Taluk|Tq|Tahsil|Tehsil)\b",
            r"ಬೆಂಗಳೂರು\s*ನಾರ್ತ್",
        ],
    },
    {
        "taluk": "Bengaluru East",
        "patterns": [
            r"ಬೆಂಗಳೂರು\s*ಪೂರ್ವ",
            r"ಪೂರ್ವ\s*(?:ತಾಲೂಕು|ತಾಲ್ಲೂಕು|ತಾಲೂಕಿನ|ತಾಲ್ಲೂಕಿನ|ತಾಲೂಕಿಗೆ)",
            r"Bengaluru\s*East",
            r"Bangalore\s*East",
            r"\bEast\s*(?:Taluk|Tq|Tahsil|Tehsil)\b",
            r"ಬೆಂಗಳೂರು\s*ಈಸ್ಟ್",
        ],
    },
    {
        "taluk": "Anekal",
        "patterns": [
            r"ಆನೇಕಲ್",
            r"\bAnekal\b",
            r"\bAnekal\s*(?:Taluk|Tq|Tahsil|Tehsil)\b",
        ],
    },
    {
        "taluk": "Yelahanka",
        "patterns": [
            r"ಯಲಹಂಕ",
            r"\bYelahanka\b",
            r"\bYelahanka\s*(?:Taluk|Tq|Tahsil|Tehsil)\b",
        ],
    },
]

LOCALITY_MAP = {
    "Koramangala": ["koramangala", "ಕೋರಮಂಗಲ", "80ft road", "st bed", "80 ಅಡಿ ರಸ್ತೆ"],
    "Indiranagar": ["indiranagar", "ಇಂದಿರಾನಗರ", "100ft road", "cmh road", "100 ಅಡಿ ರಸ್ತೆ"],
    "Agara": ["agara", "ಅಗರ", "agara village", "agara lake", "ಅಗರ ಕೆರೆ"],
    "Whitefield": ["whitefield", "ವೈಟ್‌ಫೀಲ್ಡ್", "itpb", "kadugodi", "ಕಾಡುಗೋಡಿ"],
    "HSR Layout": ["hsr layout", "hsr", "ಎಚ್‌ಎಸ್‌ಆರ್", "ಎಚ್ ಎಸ್ ಆರ್", "ಹೊಸೂರು ಸರ್ಜಾಪುರ ರಸ್ತೆ"],
    "Jayanagar": ["jayanagar", "ಜಯನಗರ"],
    "BTM Layout": ["btm layout", "btm", "ಬಿಟಿಎಂ", "ಬಿ ಟಿ ಎಂ"],
    "Electronic City": ["electronic city", "ecity", "ಎಲೆಕ್ಟ್ರಾನಿಕ್ ಸಿಟಿ", "ಎಲೆಕ್ಟ್ರಾನಿಕ್ಸ್ ಸಿಟಿ"],
    "Bellandur": ["bellandur", "ಬೆಳ್ಳಂದೂರು", "bellandur lake", "ಬೆಳ್ಳಂದೂರು ಕೆರೆ", "outer ring road"],
    "Marathahalli": ["marathahalli", "ಮಾರತ್ತಹಳ್ಳಿ", "ಮಾರತಹಳ್ಳಿ"],
    "Hebbal": ["hebbal", "ಹೆಬ್ಬಾಳ", "hebbal flyover"],
    "Yelahanka": ["yelahanka", "ಯಲಹಂಕ", "nes office"],
    "Malleshwaram": ["malleshwaram", "ಮಲ್ಲೇಶ್ವರಂ", "sampige road"],
    "Rajajinagar": ["rajajinagar", "ರಾಜಾಜಿನಗರ"],
    "Basavanagudi": ["basavanagudi", "ಬಸವನಗುಡಿ", "gandhi bazaar", "ಗಾಂಧಿ ಬಜಾರ್"],
    "Banashankari": ["banashankari", "ಬನಶಂಕರಿ", "bsk"],
    "Shivajinagar": ["shivajinagar", "ಶಿವಾಜಿನಗರ", "russell market"],
    "Sarjapur Road": ["sarjapur", "ಸರ್ಜಾಪುರ", "sarjapur road"],
    "MG Road": ["mg road", "ಎಂ ಜಿ ರಸ್ತೆ", "brigade road", "residency road"],
    "HAL": ["hal", "old airport road", "ಹಳೆ ವಿಮಾನ ನಿಲ್ದಾಣ ರಸ್ತೆ"],
    "JP Nagar": ["jp nagar", "ಜೆ ಪಿ ನಗರ", "ಜೆ.ಪಿ.ನಗರ"],
    "KR Puram": ["kr puram", "ಕೆ ಆರ್ ಪುರಂ", "krishnarajapuram"],
    "Peenya": ["peenya", "ಪೀಣ್ಯ"],
}

LOCALITY_TALUK_MAP = {
    "Koramangala": "Bengaluru South",
    "HSR Layout": "Bengaluru South",
    "BTM Layout": "Bengaluru South",
    "Jayanagar": "Bengaluru South",
    "Basavanagudi": "Bengaluru South",
    "Banashankari": "Bengaluru South",
    "JP Nagar": "Bengaluru South",
    "Bellandur": "Bengaluru East",
    "Whitefield": "Bengaluru East",
    "Marathahalli": "Bengaluru East",
    "Indiranagar": "Bengaluru East",
    "HAL": "Bengaluru East",
    "Sarjapur Road": "Bengaluru East",
    "Agara": "Bengaluru East",
    "KR Puram": "Bengaluru East",
    "Malleshwaram": "Bengaluru North",
    "Rajajinagar": "Bengaluru North",
    "Hebbal": "Bengaluru North",
    "Shivajinagar": "Bengaluru North",
    "MG Road": "Bengaluru North",
    "Peenya": "Bengaluru North",
    "Yelahanka": "Yelahanka",
    "Electronic City": "Anekal",
    "Anekal": "Anekal",
}

def convert_kannada_digits(text: str) -> str:
    if not text:
        return ""
    mapping = {
        '೦': '0', '೧': '1', '೨': '2', '೩': '3', '೪': '4',
        '೫': '5', '೬': '6', '೭': '7', '೮': '8', '೯': '9'
    }
    return "".join(mapping.get(c, c) for c in text)

def extract_form_entities_from_text(text: str) -> Dict[str, Any]:
    """
    Extract structured grievance form fields from OCR / document text:
    - Citizen Name, Phone, Email, Residential Address from natural narrative sentences
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
    norm_text = convert_kannada_digits(clean_text)
    lower_norm = norm_text.lower()

    # 1. Detect language
    kannada_chars = len(re.findall(r"[\u0C80-\u0CFF]", clean_text))
    english_chars = len(re.findall(r"[A-Za-z]", clean_text))
    if kannada_chars > 25 and english_chars > 25:
        detected_lang = "bilingual"
    elif kannada_chars > 25:
        detected_lang = "kannada"
    else:
        detected_lang = "english"

    # 2. Extract Phone
    phone = ""
    phone_match = re.search(
        r"(?:(?:Mob(?:ile)?|Phone|Tel|Cell|Contact|ಸಂಪರ್ಕ|ದೂರವಾಣಿ|ಮೊಬೈಲ್|Ph)\s*[:\-.]?\s*)?(?:\+91[\s\-]?)?([6-9]\d{4}[\s\-]?\d{5}|[6-9]\d{9})\b",
        norm_text,
        re.IGNORECASE,
    )
    if phone_match:
        raw_digits = re.sub(r"[\s\-]", "", phone_match.group(1))
        if len(raw_digits) == 10:
            phone = f"+91 {raw_digits[:5]} {raw_digits[5:]}"
        else:
            phone = phone_match.group(0).strip()
    else:
        standalone = re.search(r"\b([6-9]\d{9})\b", norm_text)
        if standalone:
            d = standalone.group(1)
            phone = f"+91 {d[:5]} {d[5:]}"

    # 3. Extract Email
    email = ""
    email_match = re.search(r"\b([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,})\b", norm_text)
    if email_match:
        email = email_match.group(1).lower()

    # 4. Extract Citizen Name from narrative prose sentences
    citizen_name = ""
    name_patterns = [
        # Kannada prose: ನಾನು ರಮೇಶ್ ಕುಮಾರ್, ತಂದೆ/ಗಂಡ/ಕೋಂ/ತಾಯಿ...
        r"ನಾನು\s+([A-Za-z\u0C80-\u0CFF\s.]{2,35}?)(?=\s*,\s*(?:ತಂದೆ|ಗಂಡ|ಕೋಂ|ತಾಯಿ|ರವರ|ಇವರು|ವಾಸ|ನಿವಾಸಿ|ಕಳೆದ|\d|ರಲ್ಲಿ|ನಲ್ಲಿ))",
        # Kannada police/formal: ದೂರುದಾರರಾದ ಶ್ರೀಮತಿ ಸುಮಿತ್ರಾಬಾಯಿ, ಕೋಂ...
        r"(?:ದೂರುದಾರರಾದ|ಅರ್ಜಿದಾರರಾದ|ದೂರುದಾರನಾದ|ಅರ್ಜಿದಾರನಾದ)\s+(?:ಶ್ರೀಮತಿ|ಶ್ರೀ|ಕುಮಾರಿ|ಡಾ[\.॥])?\s*([A-Za-z\u0C80-\u0CFF\s.]{2,35}?)(?=\s*,\s*(?:ಕೋಂ|ತಂದೆ|ಗಂಡ|ತಾಯಿ|ಬಿನ್|ವಾಸ|ನಿವಾಸಿ|ಮೊಬೈಲ್|ವಿಳಾಸ|\d))",
        # English prose: I, Chandrashekar Murthy, aged... or S/o...
        r"\bI,?\s+(?:am\s+)?(?:Mr\.|Mrs\.|Ms\.|Dr\.|Shri|Smt\.)?\s*([A-Za-z\s.]{3,35}?)(?=\s*,\s*(?:aged|s\/o|w\/o|d\/o|son of|wife of|daughter of|residing|resident|living))",
        # English: My name is John Doe, ...
        r"\bMy\s+name\s+is\s+(?:Mr\.|Mrs\.|Ms\.|Dr\.)?\s*([A-Za-z\s.]{3,35}?)(?=[,\.\n])",
        # Explicit labels: Name: John Doe or Complainant: John Doe
        r"(?:Applicant|Petitioner|Complainant|Citizen)\s+Name\s*[:\-]?\s*([^\n,]{2,40})",
        r"(?:From|Applicant|Petitioner|Complainant|Name|ಅರ್ಜಿದಾರರು|ದೂರುದಾರರು|ಹೆಸರು)\s*[:\-]\s*([^\n,]{2,40})",
        r"(?:From|ಇಂದ)\s*[:\-]?\s*\n\s*([^\n,]{3,40})",
        # End signature block
        r"(?:ಇಂತಿ\s*ತಮ್ಮ\s*ವಿಶ್ವಾಸಿ|ತಮ್ಮ\s*ವಿಶ್ವಾಸಿ|ಇಂತಿ|Yours\s+faithfully|Yours\s+sincerely)\s*[,:\-]?\s*\n\s*([A-Za-z\u0C80-\u0CFF\s.]{2,35})",
    ]
    for pat in name_patterns:
        m = re.search(pat, norm_text, re.IGNORECASE)
        if m and m.group(1):
            cand = m.group(1).strip()
            cand = re.sub(r"^(?:Shri|Smt|Sri|Mrs|Mr|Dr|Kumari|Miss|ಶ್ರೀಮತಿ|ಶ್ರೀ|ಕುಮಾರಿ|ಡಾ[\.॥])\.?\s+", "", cand, flags=re.IGNORECASE).strip()
            cand = re.sub(r"\s+", " ", cand)
            if (
                cand
                and len(cand) >= 3
                and not re.search(
                    r"^(Deputy|Commissioner|Police|Inspector|Department|District|Superintendent|Officer|Station|The\s+Class|ಅಧಿಕಾರಿ|ಠಾಣಾಧಿಕಾರಿ|To\b|Hon|Respected|ಮಾನ್ಯ)",
                    cand,
                    re.IGNORECASE,
                )
            ):
                citizen_name = cand
                break

    # 5. Extract Taluk & Locality
    taluk = ""
    for rule in TALUK_DETECTION_RULES:
        for p in rule["patterns"]:
            if re.search(p, norm_text, re.IGNORECASE):
                taluk = rule["taluk"]
                break
        if taluk:
            break

    locality = ""
    for canon, aliases in LOCALITY_MAP.items():
        for alias in aliases:
            if alias.lower() in lower_norm:
                locality = canon
                break
        if locality:
            break

    if not taluk and locality and locality in LOCALITY_TALUK_MAP:
        taluk = LOCALITY_TALUK_MAP[locality]
    if not taluk:
        taluk = "Bengaluru South"

    # 6. Extract Residential Address from continuous sentences
    address = ""

    # Pattern A: Kannada structured sentence: ನಾನು [ಹೆಸರು], [ಸಂಬಂಧ], [ವಿಳಾಸ] ನಲ್ಲಿ/ರಲ್ಲಿ ವಾಸವಾಗಿದ್ದು
    m_kn_intro = re.search(
        r"ನಾನು\s+[^,]+,\s*(?:(?:ತಂದೆ|ಗಂಡ|ಕೋಂ|ತಾಯಿ|ರವರ|ಇವರು)[^,]+,\s*)?([A-Za-z0-9\u0C80-\u0CFF\s,/#\.\-]+?)\s*(?:ದಲ್ಲಿ|ರಲ್ಲಿ|ನಲ್ಲಿ)\s*(?:ಕಳೆದ\s*[\d]+\s*ವರ್ಷಗಳಿಂದ\s*)?(?:ವಾಸವಾಗಿದ್ದು|ವಾಸವಿರುವ|ನಿವಾಸಿಯಾದ|ವಾಸ)",
        norm_text,
        re.IGNORECASE,
    )
    if m_kn_intro and m_kn_intro.group(1):
        cand = m_kn_intro.group(1).strip()
        cand = re.sub(r"^[,\s\-:]+|[,\s\-:]+$", "", cand)
        if len(cand) >= 10:
            address = cand

    # Pattern B: English narrative sentence: residing at [Address] (Phone: ...)
    if not address:
        m_en_intro = re.search(
            r"\b(?:residing\s+at|resident\s+of|living\s+at)\s+([A-Za-z0-9\s,/#\.\-]+?)(?=\s*(?:\(Phone|\(Mobile|\(Contact|,\s*(?:contact|phone|mobile|email)|\bPhone\b|\bMobile\b|\bEmail\b|,\s*wish\s+to|,\s*hereby|,\s*do\s+hereby|,\s*for\s+the\s+past|,\s*since|\.(?:\s+[A-Z]|\s*$)|;|\n|$))",
            norm_text,
            re.IGNORECASE,
        )
        if m_en_intro and m_en_intro.group(1):
            cand = m_en_intro.group(1).strip()
            cand = re.sub(r"^[,\s\-:]+|[,\s\-:]+$", "", cand)
            if len(cand) >= 10:
                address = cand

    # Pattern C: Police / complaint header: ವಾಸ [ವಿಳಾಸ], ಮೊಬೈಲ್: ...
    if not address:
        m_short_kn = re.search(
            r"(?:ವಾಸವಾಗಿರುವ|ವಾಸವಿರುವ|ನಿವಾಸಿಯಾದ|ವಾಸ|ವಿಳಾಸ)\s*[:\-]?\s*([A-Za-z0-9\u0C80-\u0CFF\s,/#\.\-]+?)(?=\s*(?:,\s*(?:ಮೊಬೈಲ್|ದೂರವಾಣಿ|ಸಂಪರ್ಕ|ದಿನಾಂಕ|ಇಮೇಲ್)|\bಮೊಬೈಲ್\b|\bದೂರವಾಣಿ\b|\bಸಂಪರ್ಕ\b|\.(?:\s+[A-Z\u0C80-\u0CFF]|\s*$)|;|\n|$))",
            norm_text,
            re.IGNORECASE,
        )
        if m_short_kn and m_short_kn.group(1):
            cand = m_short_kn.group(1).strip()
            cand = re.sub(r"^[,\s\-:]+|[,\s\-:]+$", "", cand)
            if len(cand) >= 8:
                address = cand

    # Pattern D: Explicit key-value labels
    if not address:
        addr_match = re.search(
            r"(?:Residential\s*Address|Permanent\s*Address|Address|Postal\s*Address|ವಿಳಾಸ|ವಾಸವಿರುವ\s*ವಿಳಾಸ|ವಾಸಸ್ಥಳ|ಮನೆ\s*ವಿಳಾಸ)\s*[:\-]\s*([^\n]+(?:\n\s*[^\n]+)?)",
            norm_text,
            re.IGNORECASE,
        )
        if addr_match and addr_match.group(1):
            cand = addr_match.group(1).strip()
            cand = re.sub(r"[\n\r]+", ", ", cand)
            cand = re.sub(r"\s+", " ", cand)
            cand = re.sub(r"^[,\s\-:]+|[,\s\-:]+$", "", cand)
            cand = re.sub(r"(?:Phone|Mobile|Email|Mob|ಸಂಪರ್ಕ|ದೂರವಾಣಿ)[:\-]?\s*[\d\w@.]+", "", cand, flags=re.IGNORECASE).strip()
            if len(cand) >= 8 and not re.search(r"^(To|ಇವರಿಗೆ|Deputy|Commissioner|Sir|ಮಾನ್ಯರೆ)", cand, re.IGNORECASE):
                address = cand

    # Pattern E: House / Flat / Site / Door No pattern anywhere in text
    if not address:
        hf_match = re.search(
            r"((?:(?:House|Flat|Door|Site|Plot|Khata|ಮನೆ|ಫ್ಲ್ಯಾಟ್|ಡೋರ್|ಖಾತಾ)\s*(?:No\.?|Number|ನಂ\.?)|#\s*\d+)[A-Za-z0-9\u0C80-\u0CFF\s,/#\.\-]+?(?:Bengaluru|Bangalore|Karnataka|\d{6}|Taluk|ತಾಲೂಕು|Layout|ಲೇಔಟ್|Road|ರಸ್ತೆ|Cross|ಕ್ರಾಸ್|Main|ಮುಖ್ಯರಸ್ತೆ|Block|ಬ್ಲಾಕ್|Nagar|ನಗರ|Colony|ಬಡಾವಣೆ))",
            norm_text,
            re.IGNORECASE,
        )
        if hf_match and hf_match.group(1):
            cand = hf_match.group(1).strip()
            if len(cand) >= 10:
                address = cand

    # Clean address
    if address:
        address = re.sub(r"\s+", " ", address)
        address = re.sub(r"^[,\s\-:]+|[,\s\-:]+$", "", address)
        address = re.sub(r"(?:Phone|Mobile|Email|Mob|ಸಂಪರ್ಕ|ದೂರವಾಣಿ)[:\-]?\s*[\d\w@.]+", "", address, flags=re.IGNORECASE).strip()
        address = re.sub(r"^[,\s\-:]+|[,\s\-:]+$", "", address)
    elif locality:
        address = f"{locality}, {taluk}, Bengaluru Urban"

    # 7. Extract Date
    incident_date = today_str
    date_found = False
    date_patterns = [
        # Explicit incident date: ದಿನಾಂಕ 12/04/2026 ರಂದು or on 14-04-2026
        r"(?:ದಿನಾಂಕ|on|dated|occurred on|happened on)\s*[:\-]?\s*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2})",
        r"(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})\s*(?:ರಂದು|on)",
        r"(?:Date|Dated)\s*[:\-]\s*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})",
    ]
    for pat in date_patterns:
        m = re.search(pat, norm_text, re.IGNORECASE)
        if m and m.group(1):
            raw_d = m.group(1).replace("-", "/").replace(".", "/")
            parts = raw_d.split("/")
            if len(parts) == 3:
                try:
                    if len(parts[0]) == 4:
                        y, mon, d = int(parts[0]), int(parts[1]), int(parts[2])
                    else:
                        d, mon, y = int(parts[0]), int(parts[1]), int(parts[2])
                        if y < 100:
                            y += 2000
                    incident_date = f"{y:04d}-{mon:02d}-{d:02d}"
                    date_found = True
                    break
                except Exception:
                    pass

    # 8. Extract Category
    category = "General Grievance"
    if re.search(r"noise|loudspeaker|amplified|music|sound|decibel|pub|party|ಧ್ವನಿ|ಶಬ್ದ|ಅಡಚಣೆ|ಧ್ವನಿವರ್ಧಕ", lower_norm):
        category = "Public Nuisance & Noise Pollution"
    elif re.search(r"cyber|telegram|fraud|trading|crypto|upi|online investment|scam|financial fraud|phishing|frozen|ಖಾತೆ|ವಂಚನೆ|ಸೈಬರ್", lower_norm):
        category = "Cyber Financial Fraud"
    elif re.search(r"encroachment|gomal|survey\s*no|fencing|land dispute|mutation|rtc|khata|rajakaluve|lake buffer|ಕಬಳಿಕೆ|ಒತ್ತುವರಿ|ಜಮೀನು|ಖಾತಾ|ಪಹಣಿ|ರಾಜಕಾಲುವೆ|ಗೋಮಾಳ", lower_norm):
        category = "Land Encroachment & Illegal Occupation"
    elif re.search(r"theft|stolen|robbery|burglary|snatching|vehicle stolen|break-in|chain snatching|ಕಳ್ಳತನ|ದರೋಡೆ|ಸರಗಳ್ಳತನ", lower_norm):
        category = "Theft & Burglary"
    elif re.search(r"threat|intimidation|harassment|extortion|abusive|assault|blackmail|rowdyism|ಬೆದರಿಕೆ|ಕಿರುಕುಳ|ಹಲ್ಲೆ|ಪ್ರಾಣ ಬೆದರಿಕೆ|ಸುಲಿಗೆ", lower_norm):
        category = "Threat & Harassment"
    elif re.search(r"traffic|parking|footpath|pedestrian|obstruction|pothole|signal|ವಾಹನ ಸಂಚಾರ|ಪಾರ್ಕಿಂಗ್|ರಸ್ತೆ ಗುಂಡಿ|ಫುಟ್‌ಪಾತ್", lower_norm):
        category = "Traffic & Road Obstruction"
    elif re.search(r"drainage|sewage|garbage|sanitation|drinking water|pipeline|water supply|manhole|ನೀರು|ತ್ಯಾಜ್ಯ|ಒಳಚರಂಡಿ|ಕಸ", lower_norm):
        category = "Civic Amenities & Sanitation"

    # 9. Extract Subject / Title
    title = ""
    sub_m = (
        re.search(
            r"(?:Subject|Sub|Regarding|ವಿಷಯ|ಕುರಿತು)\s*[:\-]\s*([^\n.]+?)(?=\.(?:\s+[A-Z\u0C80-\u0CFF]|\s*$)|;|\n|$)",
            norm_text,
            re.IGNORECASE,
        )
        or re.search(r"(?:Subject|Sub|Regarding|ವಿಷಯ|ಕುರಿತು)\s*[:\-]\s*([^\n]+)", norm_text, re.IGNORECASE)
        or re.search(
            r"(?:Complaint|Petition)\s+(?:against|regarding|for)\s*[:\-]?\s*([^\n.]+?)(?=\.(?:\s+[A-Z]|\s*$)|;|\n|$)",
            norm_text,
            re.IGNORECASE,
        )
        or re.search(
            r"([A-Za-z0-9\u0C80-\u0CFF\s,]+?(?:ಬಗ್ಗೆ\s*ದೂರು|ಕುರಿತು\s*ಅರ್ಜಿ|ಕುರಿತು\s*ಮನವಿ))",
            norm_text,
            re.IGNORECASE,
        )
    )
    if sub_m and sub_m.group(1):
        title = sub_m.group(1).strip()
        title = re.sub(r"^[“\"\'\s\-:]+|[”\"\'\s\.]+$", "", title)
    else:
        title = f"Petition regarding {category.lower()} at {locality or taluk}"

    # 10. Extract Incident Location (distinct from residential address)
    incident_location = ""
    loc_patterns = [
        # Explicit incident location label (with required colon or dash)
        r"(?:Place\s+of\s+incident|Incident\s+location|Location|ಘಟನಾ\s*ಸ್ಥಳ)\s*[:\-]?\s*([^\n.,]+)",
        r"(?:ಸ್ಥಳ|ಘಟನಾ\s*ಸ್ಥಳ)\s*[:\-]\s*([^\n.,]+)",
        # English narrative: on rajakaluve buffer zone near Bellandur or near Bellandur Lake
        r"\b(?:on|near|around|opposite|adjacent to|in front of)\s+([A-Za-z0-9\s,\-]{4,60}?(?:Lake|Junction|Road|Cross|Main|Block|Street|Layout|Gate|Circle|Bridge|Flyover|Park|Hospital|Station|Market|Hall|Drain|Rajakaluve|Building|Shop|Pub|Cafe|Bar|Restaurant|Area|zone))\b",
        # English general landmark near:
        r"\b(?:near|opposite|adjacent to)\s+([A-Za-z0-9\s,\-]{4,40}?)(?=\s+(?:on|dated|at around|\d|\.|\n|$))",
        # Kannada narrative: ...ರಸ್ತೆಯಲ್ಲಿ, ...ಕೆರೆ ಬಳಿ, ...ಜಂಕ್ಷನ್ ಬಳಿ, ...ಮುಂಭಾಗ
        r"([A-Za-z0-9\u0C80-\u0CFF\s,/#\-]{3,50}?(?:ರಸ್ತೆ|ಮುಖ್ಯರಸ್ತೆ|ಕೆರೆ|ಜಂಕ್ಷನ್|ವೃತ್ತ|ಪಬ್|ಬಡಾವಣೆ|ಲೇಔಟ್|ಕಟ್ಟಡ|ಮಾರುಕಟ್ಟೆ|ಕಾಮಗಾರಿ))\s*(?:ಯಲ್ಲಿ|ಯ\s*ಪಬ್‌ನಲ್ಲಿ|ಬಳಿ|ಹತ್ತಿರ|ಸಮೀಪ|ಮುಂಭಾಗ|ವ್ಯಾಪ್ತಿಯಲ್ಲಿ)",
    ]
    for pat in loc_patterns:
        lm = re.search(pat, norm_text, re.IGNORECASE)
        if lm and lm.group(1):
            cand = lm.group(1).strip()
            cand = re.sub(
                r"^(?:(?:ದಿನಾಂಕ|on|dated)?\s*\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}\s*(?:ರಂದು|on)?|(?:\d{1,2}[:.]\d{2})\s*(?:ರ\s*ಸುಮಾರಿಗೆ|ಗಂಟೆಗೆ|am|pm)?|(?:\d{1,2})\s*(?:ಗಂಟೆಗೆ|ರ\s*ಸುಮಾರಿಗೆ)|ರಾತ್ರಿ|ಬೆಳಗ್ಗೆ|ಮಧ್ಯಾಹ್ನ|ಸಂಜೆ|ನಮ್ಮ\s*ಮನೆಯ|ಸಮೀಪವಿರುವ|ಬಳಿ\s*ಇರುವ|\s)+",
                "",
                cand,
                flags=re.IGNORECASE,
            ).strip()
            cand = re.sub(r"^[,\s\-:]+|[,\s\-:]+$", "", cand)
            if (
                len(cand) >= 3
                and not re.search(r"^(the|deputy|respected|sir|madam|ಮಾನ್ಯರೆ|ನಮ್ಮ|ಸ್ಥಳೀಯ|ಕಳೆದ)", cand, re.IGNORECASE)
                and cand.lower() not in address.lower()
            ):
                incident_location = cand
                break

    if incident_location:
        if not re.search(r"[\u0C80-\u0CFF]", incident_location) and locality and locality.lower() not in incident_location.lower():
            incident_location = f"{incident_location}, {locality}"
    elif not incident_location:
        if category == "Cyber Financial Fraud":
            incident_location = f"Online / Digital Transaction ({locality or taluk})"
        elif locality:
            incident_location = f"{locality}, {taluk}"

    # 11. Extract Relief / Requested Action
    requested_action = ""
    action_patterns = [
        # Explicit labels
        r"(?:Prayer|Relief|Request|Relief\s*sought|ಮನವಿ|ಕೋರಿಕೆ|ಪ್ರಾರ್ಥನೆ|ಪರಿಹಾರ)\s*[:\-]\s*([^\n]+(?:\n[^\n]+)?)",
        # Kannada narrative: ಆದ್ದರಿಂದ ... ಕ್ರಮ ಕೈಗೊಳ್ಳಬೇಕಾಗಿ ಕೋರುತ್ತೇನೆ or ದಯವಿಟ್ಟು ... ಪ್ರಾರ್ಥನೆ
        r"(?:ಆದ್ದರಿಂದ|ದಯವಿಟ್ಟು|ಕೂಡಲೇ|ಸೂಕ್ತ)\s+([^\n.]+?(?:ಕ್ರಮ\s*(?:ಕೈಗೊಳ್ಳಬೇಕಾಗಿ|ಜರುಗಿಸಬೇಕಾಗಿ|ಕೈಗೊಳ್ಳಲು)|ಕೋರುತ್ತೇನೆ|ಪ್ರಾರ್ಥನೆ|ವಿನಂತಿ|ಆದೇಶಿಸಬೇಕಾಗಿ|ಪರಿಶೀಲಿಸಿ)[^\n.]*)",
        # English narrative: I earnestly request ... to ... or Therefore I request ...
        r"(?:Therefore,?\s+)?(?:I|we)\s+(?:earnestly\s+)?(?:request|pray|urge|appeal|seek|implore)\s+(?:(?:to|you|the\s+(?:concerned\s+)?authorities|the\s+cyber\s+crime\s+cell|the\s+police)\s+to)?\s*([^\n.]+)",
    ]
    for pat in action_patterns:
        am = re.search(pat, norm_text, re.IGNORECASE)
        if am and am.group(1):
            cand = am.group(1).strip()
            cand = re.sub(r"\s+", " ", cand)
            cand = re.sub(r"^[,\s\-:]+|[,\s\-:]+$", "", cand)
            if len(cand) >= 10:
                requested_action = cand
                break

    if not requested_action:
        requested_action = f"Immediate inquiry and statutory administrative action regarding {category.lower()}."

    # 12. Confidence Scores
    citizen_conf = "HIGH" if (citizen_name and phone) else ("NEEDS_REVIEW" if (citizen_name or phone) else "MISSING")
    cat_conf = "HIGH" if category != "General Grievance" else "NEEDS_REVIEW"
    loc_conf = "HIGH" if (incident_location or locality) else "MISSING"
    date_conf = "HIGH" if date_found else "NEEDS_REVIEW"

    # Bilingual separation: prevent mixing Kannada and English strings
    is_kn_primary = bool(re.search(r"[\u0C80-\u0CFF]", clean_text))
    
    # 4b. Citizen Name
    citizen_name_kn = citizen_name if re.search(r"[\u0C80-\u0CFF]", citizen_name) else ""
    if not citizen_name_kn and is_kn_primary and re.search(r"[\u0C80-\u0CFF]", clean_text):
        citizen_name_kn = citizen_name
    
    # Common name mappings
    name_map_kn_to_en = {
        "ರಮೇಶ್ ಕುಮಾರ್": "Ramesh Kumar", "ರಮೇಶ್": "Ramesh",
        "ಚಂದ್ರಶೇಖರ್ ಮೂರ್ತಿ": "Chandrashekar Murthy", "ಚಂದ್ರಶೇಖರ್": "Chandrashekar",
        "ಸುಮಿತ್ರಾಬಾಯಿ": "Sumithrabai", "ಸುಮಿತ್ರ": "Sumitra",
        "ರಾಜೇಶ್ವರಿ ದೇವಿ": "Rajeshwari Devi", "ರಾಜೇಶ್ವರಿ": "Rajeshwari",
        "ವೆಂಕಟೇಶ್ ರಾವ್": "Venkatesh Rao", "ವೆಂಕಟೇಶ್": "Venkatesh",
        "ಸುರೇಶ್ ಬಾಬು": "Suresh Babu", "ಸುರೇಶ್": "Suresh",
        "ಆನಂದ್ ಕುಮಾರ್": "Anand Kumar", "ಮಂಜುನಾಥ್": "Manjunath",
        "ಮಹೇಶ್": "Mahesh", "ಗೋಪಾಲಕೃಷ್ಣ": "Gopalakrishna",
    }
    if re.search(r"[\u0C80-\u0CFF]", citizen_name):
        citizen_name_kn = citizen_name
        citizen_name_en = name_map_kn_to_en.get(citizen_name.strip(), "Complainant Citizen")
    else:
        citizen_name_en = citizen_name

    # Locality & Taluk bilingual
    loc_kn_map = {
        "Koramangala": "ಕೋರಮಂಗಲ", "Indiranagar": "ಇಂದಿರಾನಗರ", "Bellandur": "ಬೆಳ್ಳಂದೂರು",
        "Whitefield": "ವೈಟ್‌ಫೀಲ್ಡ್", "HSR Layout": "ಎಚ್‌ಎಸ್‌ಆರ್ ಲೇಔಟ್", "Jayanagar": "ಜಯನಗರ",
        "BTM Layout": "ಬಿಟಿಎಂ ಲೇಔಟ್", "Electronic City": "ಎಲೆಕ್ಟ್ರಾನಿಕ್ ಸಿಟಿ", "Malleshwaram": "ಮಲ್ಲೇಶ್ವರಂ",
        "Rajajinagar": "ರಾಜಾಜಿನಗರ", "Yelahanka": "ಯಲಹಂಕ", "Anekal": "ಆನೇಕಲ್", "Hebbal": "ಹೆಬ್ಬಾಳ",
        "Basavanagudi": "ಬಸವನಗುಡಿ", "Banashankari": "ಬನಶಂಕರಿ", "Shivajinagar": "ಶಿವಾಜಿನಗರ",
        "Sarjapur Road": "ಸರ್ಜಾಪುರ ರಸ್ತೆ", "MG Road": "ಎಂ ಜಿ ರಸ್ತೆ", "HAL": "ಎಚ್‌ಎಎಲ್",
        "JP Nagar": "ಜೆ ಪಿ ನಗರ", "KR Puram": "ಕೆ ಆರ್ ಪುರಂ", "Peenya": "ಪೀಣ್ಯ",
    }
    taluk_kn_map = {
        "Bengaluru South": "ಬೆಂಗಳೂರು ದಕ್ಷಿಣ", "Bengaluru North": "ಬೆಂಗಳೂರು ಉತ್ತರ",
        "Bengaluru East": "ಬೆಂಗಳೂರು ಪೂರ್ವ", "Anekal": "ಆನೇಕಲ್", "Yelahanka": "ಯಲಹಂಕ",
    }
    taluk_kn = taluk_kn_map.get(taluk, "ಬೆಂಗಳೂರು ದಕ್ಷಿಣ")
    locality_kn = loc_kn_map.get(locality, locality)

    # Categories bilingual
    category_kn_map = {
        "Public Nuisance & Noise Pollution": "ಸಾರ್ವಜನಿಕ ಶಾಂತಿ ಭಂಗ ಮತ್ತು ಶಬ್ದ ಮಾಲಿನ್ಯ",
        "Cyber Financial Fraud": "ಸೈಬರ್ ಹಣಕಾಸು ವಂಚನೆ",
        "Land Encroachment & Illegal Occupation": "ಸರ್ಕಾರಿ ಜಮೀನು ಒತ್ತುವರಿ ಮತ್ತು ಅಕ್ರಮ ಕಬಳಿಕೆ",
        "Theft & Burglary": "ಕಳ್ಳತನ ಮತ್ತು ದರೋಡೆ",
        "Threat & Harassment": "ಬೆದರಿಕೆ ಮತ್ತು ಕಿರುಕುಳ",
        "Traffic & Road Obstruction": "ಸಂಚಾರ ದಟ್ಟಣೆ ಮತ್ತು ರಸ್ತೆ ಅಡಚಣೆ",
        "Civic Amenities & Sanitation": "ನಾಗರಿಕ ಸೌಲಭ್ಯಗಳು ಮತ್ತು ಒಳಚರಂಡಿ ನೈರ್ಮಲ್ಯ",
        "General Grievance": "ಸಾಮಾನ್ಯ ನಾಗರಿಕ ಅಹವಾಲು",
    }
    category_kn = category_kn_map.get(category, "ಸಾಮಾನ್ಯ ನಾಗರಿಕ ಅಹವಾಲು")

    # Clean address bilingual
    address_kn = address if re.search(r"[\u0C80-\u0CFF]", address) else ""
    if re.search(r"[\u0C80-\u0CFF]", address):
        # Convert Kannada address to clean English
        clean_addr_en = address
        clean_addr_en = re.sub(r"80\s*(?:ಅಡಿ|ft|feet)?\s*ರಸ್ತೆ", "80ft Road", clean_addr_en, flags=re.IGNORECASE)
        clean_addr_en = re.sub(r"100\s*(?:ಅಡಿ|ft|feet)?\s*ರಸ್ತೆ", "100ft Road", clean_addr_en, flags=re.IGNORECASE)
        clean_addr_en = re.sub(r"ಮನೆ\s*ನಂ\.?", "House No. ", clean_addr_en)
        clean_addr_en = re.sub(r"ಫ್ಲ್ಯಾಟ್\s*ನಂ\.?", "Flat No. ", clean_addr_en)
        clean_addr_en = re.sub(r"ಡೋರ್\s*ನಂ\.?", "Door No. ", clean_addr_en)
        clean_addr_en = re.sub(r"ಸೈಟ್\s*ನಂ\.?", "Site No. ", clean_addr_en)
        clean_addr_en = re.sub(r"ಖಾತಾ\s*ನಂ\.?", "Khata No. ", clean_addr_en)
        clean_addr_en = re.sub(r"(\d+)\s*ನೇ\s*ಮುಖ್ಯರಸ್ತೆ", r"\1th Main Road", clean_addr_en)
        clean_addr_en = re.sub(r"(\d+)\s*ನೇ\s*ಕ್ರಾಸ್", r"\1th Cross", clean_addr_en)
        clean_addr_en = re.sub(r"(\d+)\s*ನೇ\s*ಬ್ಲಾಕ್", r"\1th Block", clean_addr_en)
        clean_addr_en = re.sub(r"ಮುಖ್ಯರಸ್ತೆ", "Main Road", clean_addr_en)
        clean_addr_en = re.sub(r"ಕ್ರಾಸ್", "Cross", clean_addr_en)
        clean_addr_en = re.sub(r"ಬ್ಲಾಕ್", "Block", clean_addr_en)
        clean_addr_en = re.sub(r"ಬಡಾವಣೆ|ಲೇಔಟ್", "Layout", clean_addr_en)
        clean_addr_en = re.sub(r"ರಸ್ತೆ", "Road", clean_addr_en)
        clean_addr_en = re.sub(r"ನಗರ", "Nagar", clean_addr_en)
        clean_addr_en = re.sub(r"ಕೋರಮಂಗಲ", "Koramangala", clean_addr_en)
        clean_addr_en = re.sub(r"ಇಂದಿರಾನಗರ", "Indiranagar", clean_addr_en)
        clean_addr_en = re.sub(r"ಬೆಳ್ಳಂದೂರು", "Bellandur", clean_addr_en)
        clean_addr_en = re.sub(r"ವೈಟ್‌ಫೀಲ್ಡ್", "Whitefield", clean_addr_en)
        clean_addr_en = re.sub(r"ಜಯನಗರ", "Jayanagar", clean_addr_en)
        clean_addr_en = re.sub(r"ಮಲ್ಲೇಶ್ವರಂ", "Malleshwaram", clean_addr_en)
        clean_addr_en = re.sub(r"ರಾಜಾಜಿನಗರ", "Rajajinagar", clean_addr_en)
        clean_addr_en = re.sub(r"ಯಲಹಂಕ", "Yelahanka", clean_addr_en)
        clean_addr_en = re.sub(r"ಆನೇಕಲ್", "Anekal", clean_addr_en)
        clean_addr_en = re.sub(r"ಕೆರೆ", "Lake", clean_addr_en)
        clean_addr_en = re.sub(r"ವೃತ್ತ", "Circle", clean_addr_en)
        clean_addr_en = re.sub(r"ಜಂಕ್ಷನ್", "Junction", clean_addr_en)
        clean_addr_en = re.sub(r"ಬಳಿ|ಹತ್ತಿರ|ಸಮೀಪ", "Near", clean_addr_en)
        clean_addr_en = re.sub(r"ಮುಂಭಾಗ|ಎದುರು", "Opposite", clean_addr_en)
        clean_addr_en = re.sub(r"ಹಿಂದೆ", "Behind", clean_addr_en)
        clean_addr_en = re.sub(r"ಪಕ್ಕದಲ್ಲಿ", "Beside", clean_addr_en)
        clean_addr_en = re.sub(r"ಪಬ್|ಬಾರ್", "Pub", clean_addr_en)
        clean_addr_en = re.sub(r"ಕಟ್ಟಡ", "Building", clean_addr_en)
        clean_addr_en = re.sub(r"ಮಾರುಕಟ್ಟೆ", "Market", clean_addr_en)
        clean_addr_en = re.sub(r"ಶಾಲೆ", "School", clean_addr_en)
        clean_addr_en = re.sub(r"ಕಾಲೇಜು", "College", clean_addr_en)
        clean_addr_en = re.sub(r"ಆಸ್ಪತ್ರೆ", "Hospital", clean_addr_en)
        clean_addr_en = re.sub(r"ದೇವಸ್ಥಾನ|ದೇವಾಲಯ|ಗುಡಿ", "Temple", clean_addr_en)
        clean_addr_en = re.sub(r"ಉದ್ಯಾನ|ಪಾರ್ಕ್", "Park", clean_addr_en)
        clean_addr_en = re.sub(r"ಫ್ಲೈಓವರ್", "Flyover", clean_addr_en)
        clean_addr_en = re.sub(r"ಸೇತುವೆ|ಬ್ರಿಡ್ಜ್", "Bridge", clean_addr_en)
        clean_addr_en = re.sub(r"ರಾಜಕಾಲುವೆ|ಕಾಲುವೆ", "Rajakaluve", clean_addr_en)
        clean_addr_en = re.sub(r"ವ್ಯಾಪ್ತಿಯಲ್ಲಿ|ವ್ಯಾಪ್ತಿ", "Jurisdiction", clean_addr_en)
        clean_addr_en = re.sub(r"ಬೆಂಗಳೂರು\s*ದಕ್ಷಿಣ\s*(?:ತಾಲೂಕಿನ|ತಾಲೂಕು)", "Bengaluru South Taluk", clean_addr_en)
        clean_addr_en = re.sub(r"ಬೆಂಗಳೂರು\s*ಉತ್ತರ\s*(?:ತಾಲೂಕಿನ|ತಾಲೂಕು)", "Bengaluru North Taluk", clean_addr_en)
        clean_addr_en = re.sub(r"ಬೆಂಗಳೂರು\s*ಪೂರ್ವ\s*(?:ತಾಲೂಕಿನ|ತಾಲೂಕು)", "Bengaluru East Taluk", clean_addr_en)
        clean_addr_en = re.sub(r"ಬೆಂಗಳೂರು", "Bengaluru", clean_addr_en)
        clean_addr_en = re.sub(r"ತಾಲೂಕಿನ|ತಾಲೂಕು", "Taluk", clean_addr_en)
        clean_addr_en = re.sub(r"[\u0C80-\u0CFF]+", "", clean_addr_en).strip()
        clean_addr_en = re.sub(r"\s+", " ", clean_addr_en).strip()
        address_en = clean_addr_en or f"{locality}, {taluk}, Bengaluru Urban"
    else:
        address_en = address
        address_kn = f"{locality_kn}, {taluk_kn}, ಬೆಂಗಳೂರು ನಗರ"

    # Clean Incident Location bilingual: No mixed strings!
    incident_location_kn = ""
    incident_location_en = ""
    if re.search(r"[\u0C80-\u0CFF]", incident_location):
        incident_location_kn = incident_location
        if locality_kn and locality_kn not in incident_location_kn:
            incident_location_kn = f"{incident_location_kn}, {locality_kn}"
        clean_loc = convert_kannada_digits(incident_location)
        clean_loc = re.sub(r"80\s*(?:ಅಡಿ|ft|feet)?\s*ರಸ್ತೆ", "80ft Road", clean_loc, flags=re.IGNORECASE)
        clean_loc = re.sub(r"100\s*(?:ಅಡಿ|ft|feet)?\s*ರಸ್ತೆ", "100ft Road", clean_loc, flags=re.IGNORECASE)
        clean_loc = re.sub(r"(\d+)\s*ನೇ\s*ಮುಖ್ಯರಸ್ತೆ", r"\1th Main Road", clean_loc)
        clean_loc = re.sub(r"(\d+)\s*ನೇ\s*ಕ್ರಾಸ್", r"\1th Cross", clean_loc)
        clean_loc = re.sub(r"ಮುಖ್ಯರಸ್ತೆ", "Main Road", clean_loc)
        clean_loc = re.sub(r"ಕ್ರಾಸ್", "Cross", clean_loc)
        clean_loc = re.sub(r"ರಸ್ತೆ", "Road", clean_loc)
        clean_loc = re.sub(r"ಕೆರೆ", "Lake", clean_loc)
        clean_loc = re.sub(r"ವೃತ್ತ", "Circle", clean_loc)
        clean_loc = re.sub(r"ಜಂಕ್ಷನ್", "Junction", clean_loc)
        clean_loc = re.sub(r"ಬಳಿ|ಹತ್ತಿರ|ಸಮೀಪ", "Near", clean_loc)
        clean_loc = re.sub(r"ಮುಂಭಾಗ|ಎದುರು", "Opposite", clean_loc)
        clean_loc = re.sub(r"ಪಬ್|ಬಾರ್", "Pub", clean_loc)
        clean_loc = re.sub(r"ಕೋರಮಂಗಲ", "Koramangala", clean_loc)
        clean_loc = re.sub(r"ಇಂದಿರಾನಗರ", "Indiranagar", clean_loc)
        clean_loc = re.sub(r"ಬೆಳ್ಳಂದೂರು", "Bellandur", clean_loc)
        clean_loc = re.sub(r"ವೈಟ್‌ಫೀಲ್ಡ್", "Whitefield", clean_loc)
        clean_loc = re.sub(r"[\u0C80-\u0CFF]+", "", clean_loc).strip()
        clean_loc = re.sub(r"\s+", " ", clean_loc).strip()
        if locality and locality.lower() not in clean_loc.lower():
            clean_loc = f"{clean_loc}, {locality}" if clean_loc else f"{locality}, {taluk}"
        incident_location_en = clean_loc or (f"{locality}, {taluk}" if locality else taluk)
    else:
        incident_location_en = incident_location
        incident_location_kn = f"{locality_kn}, {taluk_kn}" if locality_kn else taluk_kn

    # Clean Title bilingual: Pure English and Pure Kannada
    if re.search(r"[\u0C80-\u0CFF]", title):
        title_kn = title
        title_en = f"Petition regarding {category.lower()} at {locality or taluk}"
    else:
        title_en = title
        title_kn = f"{locality_kn or taluk_kn} ವ್ಯಾಪ್ತಿಯಲ್ಲಿ {category_kn} ಕುರಿತು ದೂರು"

    # Clean Relief bilingual
    if re.search(r"[\u0C80-\u0CFF]", requested_action):
        requested_action_kn = requested_action
        requested_action_en = f"Immediate inquiry and statutory administrative action regarding {category.lower()}."
    else:
        requested_action_en = requested_action
        requested_action_kn = f"ಕೂಡಲೇ ಸ್ಥಳ ಪರಿಶೀಲನೆ ನಡೆಸಿ ಸೂಕ್ತ ಕಾನೂನು ಕ್ರಮ ಜರುಗಿಸುವುದು."

    # Directives bilingual
    directives_en = f"Urgent field inquiry regarding {category} at {incident_location_en}. Verify complainant statements, inspect locus, and submit statutory Action Taken Report (ATR) within follow-up timeline."
    directives_kn = f"{incident_location_kn} ವ್ಯಾಪ್ತಿಯಲ್ಲಿ {category_kn} ಕುರಿತು ತುರ್ತು ಸ್ಥಳ ಪರಿಶೀಲನೆ ನಡೆಸಿ, ದೂರುದಾರರ ಹೇಳಿಕೆಯನ್ನು ಪಡೆದು, ನಿಗದಿತ ದಿನಾಂಕದೊಳಗೆ ಕ್ರಮ ಕೈಗೊಂಡ ವರದಿ (ATR) ಸಲ್ಲಿಸುವುದು."

    return {
        "citizen_name": citizen_name_en,
        "citizen_name_kn": citizen_name_kn,
        "phone": phone,
        "email": email,
        "address": address_en,
        "address_kn": address_kn,
        "district": "Bengaluru Urban",
        "district_kn": "ಬೆಂಗಳೂರು ನಗರ",
        "taluk": taluk,
        "taluk_kn": taluk_kn,
        "village_locality": locality,
        "village_locality_kn": locality_kn,
        "title": title_en,
        "title_kn": title_kn,
        "category": category,
        "category_kn": category_kn,
        "incident_date": incident_date,
        "incident_location": incident_location_en,
        "incident_location_kn": incident_location_kn,
        "description": clean_text[:1000].strip() if not is_kn_primary else f"Grievance petition submitted by {citizen_name_en} residing at {address_en}. Incident occurred on {incident_date} at {incident_location_en}. Grievance classification: {category}. Demanded relief: {requested_action_en}.",
        "description_kn": clean_text[:1000].strip() if is_kn_primary else f"{citizen_name_kn or citizen_name_en} ಅವರು ಸಲ್ಲಿಸಿದ ದೂರು ಅರ್ಜಿ. ವಾಸಸ್ಥಳ: {address_kn}. ಘಟನೆ ನಡೆದ ದಿನಾಂಕ: {incident_date}, ಸ್ಥಳ: {incident_location_kn}. ವಿಷಯ: {category_kn}. ಕೋರಿದ ಪರಿಹಾರ: {requested_action_kn}.",
        "requested_action": requested_action_en,
        "requested_action_kn": requested_action_kn,
        "assignment_instructions_en": directives_en,
        "assignment_instructions_kn": directives_kn,
        "language": detected_lang,
        "confidence_scores": {
            "citizen": citizen_conf,
            "category": cat_conf,
            "location": loc_conf,
            "date": date_conf,
        },
    }
