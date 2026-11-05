"""
API router for Grievance Document Translation (Stage 2).
Provides bidirectional Kannada <-> English translation, human review,
and administrative approval workflows under strict department isolation.
"""
import logging
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_any_permission, require_permission
from app.core.database import get_db
from app.core.roles import Permission
from app.models.user import User
from app.schemas.translation import (
    TranslationEngineInfo,
    TranslationResponse,
    TranslationReviewRequest,
    TranslationTriggerRequest,
)
from app.services import translation_service

logger = logging.getLogger(__name__)

translations_router = APIRouter(tags=["Translations"])


@translations_router.get(
    "/translations/engine/status",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Translation Engine Status",
    description="Returns current translation provider architecture, model metadata, and offline readiness.",
)
def get_translation_engine_status(
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    return translation_service.get_translation_engine_status()


@translations_router.post(
    "/documents/{document_id}/translate",
    response_model=TranslationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Trigger Document Translation",
    description="Initiates bidirectional Kannada <-> English translation of OCR text. Preserves original document and OCR text immutably.",
)
def trigger_document_translation(
    document_id: uuid.UUID,
    payload: TranslationTriggerRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_any_permission(Permission.CREATE_GRIEVANCE, Permission.EDIT_GRIEVANCE)),
) -> TranslationResponse:
    ip = request.client.host if request.client else None
    rec = translation_service.translate_document(
        db=db,
        document_id=document_id,
        payload=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return TranslationResponse.model_validate(rec)


@translations_router.get(
    "/documents/{document_id}/translations",
    response_model=List[TranslationResponse],
    status_code=status.HTTP_200_OK,
    summary="List Document Translations",
    description="Returns complete historical translation records for a document, ordered newest first.",
)
def list_document_translations(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_GRIEVANCE)),
) -> List[TranslationResponse]:
    recs = translation_service.list_document_translations(
        db=db,
        document_id=document_id,
        current_user=current_user,
    )
    return [TranslationResponse.model_validate(r) for r in recs]


@translations_router.get(
    "/translations/{translation_id}",
    response_model=TranslationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Translation by ID",
    description="Retrieves a specific translation record by ID under department isolation.",
)
def get_translation(
    translation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_GRIEVANCE)),
) -> TranslationResponse:
    rec = translation_service.get_translation_by_id(
        db=db,
        translation_id=translation_id,
        current_user=current_user,
    )
    return TranslationResponse.model_validate(rec)


@translations_router.patch(
    "/translations/{translation_id}/review",
    response_model=TranslationResponse,
    status_code=status.HTTP_200_OK,
    summary="Save Operator Translation Review & Corrections",
    description="Saves human-corrected translation text. The original machine translation is preserved intact.",
)
def review_translation(
    translation_id: uuid.UUID,
    payload: TranslationReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_any_permission(Permission.CREATE_GRIEVANCE, Permission.EDIT_GRIEVANCE)),
) -> TranslationResponse:
    ip = request.client.host if request.client else None
    rec = translation_service.review_translation(
        db=db,
        translation_id=translation_id,
        review_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return TranslationResponse.model_validate(rec)


@translations_router.post(
    "/translations/{translation_id}/approve",
    response_model=TranslationResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve Translation as Official Record",
    description="Officially approves a translation record. Restricted to Officers, Supervisors, and Administrators.",
)
def approve_translation(
    translation_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TranslationResponse:
    ip = request.client.host if request.client else None
    rec = translation_service.approve_translation(
        db=db,
        translation_id=translation_id,
        current_user=current_user,
        ip_address=ip,
    )
    return TranslationResponse.model_validate(rec)

