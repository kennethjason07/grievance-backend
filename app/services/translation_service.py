"""
Translation Service for JanSetu Grievance System (Stage 2).
Handles bidirectional Kannada <-> English translation of grievance documents:
- Department isolation & role-based permissions
- Document chunking while preserving paragraphs, numbers, and identifiers
- Separation of machine translated text vs human corrected text
- Complete translation history retention (prior translations are never overwritten)
- Original document and OCR text immutability
- Audit logging for creation, review, and approval
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.roles import RoleName
from app.models.document import Document
from app.models.grievance import Grievance
from app.models.translation import DocumentTranslation
from app.models.user import User
from app.schemas.translation import (
    ALLOWED_LANGUAGES,
    ALLOWED_TEXT_SOURCES,
    TranslationReviewRequest,
    TranslationTriggerRequest,
)
from app.services.audit import log_activity
from app.services.grievance_service import _check_grievance_department_access
from app.services.translation.factory import get_translation_provider

logger = logging.getLogger(__name__)
settings = get_settings()


def _chunk_text_preserving_paragraphs(text: str, max_chars: int) -> List[str]:
    """
    Splits text into chunks preserving paragraph integrity and order.
    Never splits across a paragraph unless a single paragraph exceeds max_chars.
    """
    paragraphs = text.split("\n\n")
    chunks: List[str] = []
    current_chunk: List[str] = []
    current_len = 0

    for p in paragraphs:
        p_len = len(p)
        if current_chunk and (current_len + p_len + 2 > max_chars):
            chunks.append("\n\n".join(current_chunk))
            current_chunk = [p]
            current_len = p_len
        else:
            current_chunk.append(p)
            current_len += p_len + 2

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks if chunks else [text]


def translate_document(
    db: Session,
    document_id: uuid.UUID,
    payload: TranslationTriggerRequest,
    current_user: User,
    ip_address: Optional[str] = None,
) -> DocumentTranslation:
    """
    Translate an authorized document's OCR text.
    Preserves all historical translations, never overwriting previous records or original OCR text.
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

    # Enforce department multi-tenancy
    _check_grievance_department_access(current_user, grievance)

    # Validate OCR completion
    if document.ocr_status not in ["COMPLETED", "NEEDS_REVIEW"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot translate document in '{document.ocr_status}' status. "
                "Document must complete OCR text extraction before translation."
            ),
        )

    # Select text source
    src_text: Optional[str] = None
    if payload.text_source == "REVIEWED_OCR":
        src_text = document.reviewed_text or document.extracted_text
    else:  # RAW_OCR
        src_text = document.extracted_text

    if not src_text or not src_text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selected OCR text source is empty. Please extract document text before translating.",
        )

    # Validate language pair
    src_lang = payload.source_language.strip().lower()
    tgt_lang = payload.target_language.strip().lower()

    if src_lang not in ALLOWED_LANGUAGES or tgt_lang not in ALLOWED_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported language pair '{src_lang}' -> '{tgt_lang}'. Supported: {sorted(ALLOWED_LANGUAGES)}",
        )

    if src_lang == tgt_lang:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Source and target languages cannot be identical ('{src_lang}').",
        )

    # Initialize translation record in PENDING state
    provider = get_translation_provider()
    provider_info = provider.get_provider_info()

    translation_rec = DocumentTranslation(
        document_id=document.id,
        source_language=src_lang,
        target_language=tgt_lang,
        text_source=payload.text_source,
        source_text=src_text,
        translated_text=None,
        provider_name=provider_info.get("provider_name", "UnknownProvider"),
        model_version=provider_info.get("model_version"),
        status="PROCESSING",
        created_by=current_user.id,
    )
    db.add(translation_rec)
    db.commit()
    db.refresh(translation_rec)

    # Execute translation with chunking for long documents
    try:
        chunks = _chunk_text_preserving_paragraphs(
            src_text, max_chars=settings.TRANSLATION_MAX_CHUNK_CHARS
        )
        translated_chunks: List[str] = []

        for chunk in chunks:
            chunk_trans, _ = provider.translate(
                chunk,
                source_language=src_lang,
                target_language=tgt_lang,
            )
            translated_chunks.append(chunk_trans)

        full_translation = "\n\n".join(translated_chunks)
        translation_rec.translated_text = full_translation
        translation_rec.corrected_text = full_translation  # initialized to raw machine output for review
        translation_rec.status = "COMPLETED"
        translation_rec.error_message = None

    except Exception as exc:
        logger.exception(f"Translation failed for document {document_id}: {exc}")
        translation_rec.status = "FAILED"
        translation_rec.error_message = str(exc)

    db.commit()
    db.refresh(translation_rec)

    # Record audit event
    try:
        log_activity(
            db=db,
            action="DOCUMENT_TRANSLATION_CREATED",
            entity_type="translation",
            entity_id=translation_rec.id,
            user_id=current_user.id,
            grievance_id=document.grievance_id,
            new_values={
                "document_id": str(document.id),
                "translation_id": str(translation_rec.id),
                "source_language": src_lang,
                "target_language": tgt_lang,
                "text_source": payload.text_source,
                "status": translation_rec.status,
                "provider": translation_rec.provider_name,
                "error": translation_rec.error_message,
            },
            ip_address=ip_address,
        )
    except Exception as e:
        logger.warning(f"Failed to record translation creation audit log: {e}")

    return translation_rec


def list_document_translations(
    db: Session,
    document_id: uuid.UUID,
    current_user: User,
) -> List[DocumentTranslation]:
    """Retrieve full translation history for a document, scoped by department access."""
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

    return (
        db.query(DocumentTranslation)
        .filter_by(document_id=document_id)
        .order_by(DocumentTranslation.created_at.desc())
        .all()
    )


def get_translation_by_id(
    db: Session,
    translation_id: uuid.UUID,
    current_user: User,
) -> DocumentTranslation:
    """Retrieve a specific translation record by ID with department isolation."""
    translation = db.get(DocumentTranslation, translation_id)
    if not translation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Translation with ID {translation_id} not found.",
        )

    document = db.get(Document, translation.document_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated document not found.",
        )

    grievance = db.get(Grievance, document.grievance_id)
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated grievance not found.",
        )

    _check_grievance_department_access(current_user, grievance)
    return translation


def review_translation(
    db: Session,
    translation_id: uuid.UUID,
    review_in: TranslationReviewRequest,
    current_user: User,
    ip_address: Optional[str] = None,
) -> DocumentTranslation:
    """
    Allow an authorized operator to save human corrections to translated text.
    The machine-generated translated_text remains immutably preserved in the record.
    """
    translation = get_translation_by_id(db, translation_id, current_user)

    if translation.status not in ["COMPLETED", "UNDER_REVIEW"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot review translation in '{translation.status}' status. Translation must be completed first.",
        )

    old_corrected = translation.corrected_text
    translation.corrected_text = review_in.corrected_text
    translation.reviewed_by = current_user.id
    translation.reviewed_at = datetime.now(timezone.utc)
    translation.status = "UNDER_REVIEW"

    db.commit()
    db.refresh(translation)

    log_activity(
        db=db,
        action="DOCUMENT_TRANSLATION_REVIEWED",
        entity_type="translation",
        entity_id=translation.id,
        user_id=current_user.id,
        grievance_id=translation.document.grievance_id,
        old_values={"preview": (old_corrected[:100] if old_corrected else None)},
        new_values={
            "preview": review_in.corrected_text[:100],
            "reviewed_by": str(current_user.id),
            "status": translation.status,
        },
        ip_address=ip_address,
    )

    return translation


def approve_translation(
    db: Session,
    translation_id: uuid.UUID,
    current_user: User,
    ip_address: Optional[str] = None,
) -> DocumentTranslation:
    """
    Approve a translation as verified official case documentation.
    Requires Officer, Supervisor, or Administrative authority.
    Data Entry Operators or Viewers are forbidden from approving translations.
    """
    translation = get_translation_by_id(db, translation_id, current_user)

    # Role authority guard
    allowed_roles = {
        RoleName.SUPER_ADMIN,
        RoleName.DEPARTMENT_ADMIN,
        RoleName.SUPERVISOR,
        RoleName.OFFICER,
    }
    user_role = current_user.role.name if current_user.role else ""
    if user_role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{user_role}' is not authorized to approve translations. Officer or supervisory review required.",
        )

    if translation.status not in ["COMPLETED", "UNDER_REVIEW"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot approve translation in '{translation.status}' status. Only COMPLETED or UNDER_REVIEW translations can be approved.",
        )

    translation.status = "APPROVED"
    translation.approved_by = current_user.id
    translation.approved_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(translation)

    log_activity(
        db=db,
        action="DOCUMENT_TRANSLATION_APPROVED",
        entity_type="translation",
        entity_id=translation.id,
        user_id=current_user.id,
        grievance_id=translation.document.grievance_id,
        new_values={
            "approved_by": str(current_user.id),
            "approved_at": translation.approved_at.isoformat(),
            "status": translation.status,
        },
        ip_address=ip_address,
    )

    return translation


def get_translation_engine_status() -> Dict[str, Any]:
    """Retrieve operational status and model capabilities of the configured translation engine."""
    provider = get_translation_provider()
    return provider.get_provider_info()

