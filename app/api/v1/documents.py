"""
API router for Grievance Document Upload, Retrieval, Download, and OCR Processing.
Enforces strict authentication, permissions, and grievance-level authorization.
"""
import logging
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_any_permission, require_permission
from app.core.config import get_settings
from app.core.database import get_db
from app.core.roles import Permission
from app.models.document import Document
from app.models.user import User
from app.schemas.document import (
    DocumentOcrResponse,
    DocumentOcrReviewRequest,
    DocumentOcrTriggerRequest,
    DocumentResponse,
)
from app.services import document_service, ocr_service

logger = logging.getLogger(__name__)
settings = get_settings()

documents_router = APIRouter(tags=["Documents"])


def _to_document_response(doc: Document) -> DocumentResponse:
    res = DocumentResponse.model_validate(doc)
    res.download_url = f"/api/v1/documents/{doc.id}/download"
    return res


@documents_router.post(
    "/grievances/{grievance_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Grievance Document",
    description="Uploads a physical document (petition, identity proof, report, etc.) to local disk storage and saves metadata.",
)
def upload_grievance_document(
    grievance_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(..., description="Document file to upload (Max 25 MB)"),
    document_type: str = Form("PETITION", description="Document type: PETITION, IDENTITY_PROOF, EVIDENCE, REPORT, RESPONSE, TRANSLATION, OTHER"),
    language: str = Form("en", description="Language code: en or kn"),
    document_name: Optional[str] = Form(None, description="Optional friendly document title"),
    parent_document_id: Optional[uuid.UUID] = Form(None, description="Parent document ID if derived/translated"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_any_permission(Permission.CREATE_GRIEVANCE, Permission.EDIT_GRIEVANCE)),
) -> DocumentResponse:
    ip = request.client.host if request.client else None
    doc = document_service.save_document(
        db=db,
        grievance_id=grievance_id,
        file=file,
        document_type=document_type,
        language=language,
        document_name=document_name,
        parent_document_id=parent_document_id,
        current_user=current_user,
        ip_address=ip,
    )
    return _to_document_response(doc)


@documents_router.get(
    "/grievances/{grievance_id}/documents",
    response_model=List[DocumentResponse],
    status_code=status.HTTP_200_OK,
    summary="List Documents for Grievance",
    description="Returns all document attachments for a grievance, adhering to department scoping.",
)
def list_grievance_documents(
    grievance_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_GRIEVANCE)),
) -> List[DocumentResponse]:
    docs = document_service.list_documents(
        db=db,
        grievance_id=grievance_id,
        current_user=current_user,
    )
    return [_to_document_response(d) for d in docs]



@documents_router.post(
    "/documents/extract-text",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Direct Document Text Extraction / OCR",
    description="Validates uploaded file and extracts embedded text or executes OCR directly before grievance registration.",
)
async def extract_ad_hoc_document_text(
    file: UploadFile = File(...),
    languages: Optional[str] = Form(default="eng+kan"),
    current_user: User = Depends(require_any_permission(Permission.CREATE_GRIEVANCE, Permission.VIEW_GRIEVANCE)),
) -> Dict[str, Any]:
    import tempfile
    from pathlib import Path

    raw_filename = file.filename or "uploaded_file"
    ext, _ = document_service.validate_extension_and_mime(raw_filename, file.content_type)
    suffix = ext if ext.startswith(".") else f".{ext}"

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty (0 bytes).",
        )
    if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB.",
        )

    # Validate file signature
    document_service.validate_file_signature(ext, content[:512])

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        res = ocr_service.extract_text_from_file_path(tmp_path, languages=languages)
        return {
            "filename": raw_filename,
            "extracted_text": res["extracted_text"],
            "reviewed_text": res["extracted_text"],
            "pages_data": res["pages_data"],
            "ocr_status": res["ocr_status"],
            "ocr_error": res["ocr_error"],
            "languages": res.get("languages", languages),
        }
    finally:
        tmp_path.unlink(missing_ok=True)


@documents_router.get(
    "/documents/ocr/status",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="OCR Engine System Status",
    description="Returns Tesseract OCR installation status, version, and supported languages.",
)
def get_ocr_engine_status(
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    return ocr_service.get_tesseract_status()


@documents_router.get(
    "/documents/{document_id}",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Document Metadata",
    description="Returns metadata for a specific document without exposing filesystem paths.",
)
def get_document_metadata(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_GRIEVANCE)),
) -> DocumentResponse:
    doc = document_service.get_document(
        db=db,
        document_id=document_id,
        current_user=current_user,
    )
    return _to_document_response(doc)


@documents_router.get(
    "/documents/{document_id}/download",
    summary="Download or Preview Grievance Document File",
    description="Downloads or previews the binary file content from local disk storage with grievance-level authorization.",
)
def download_document_file(
    document_id: uuid.UUID,
    preview: bool = Query(False, description="Set True for inline browser preview, False for attachment download"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_GRIEVANCE)),
) -> FileResponse:
    file_path, filename, mime_type = document_service.get_document_file_for_download(
        db=db,
        document_id=document_id,
        current_user=current_user,
    )
    disposition = "inline" if preview else "attachment"
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type=mime_type,
        content_disposition_type=disposition,
    )


# ===========================================================================
# OCR Text Extraction and Human Review Endpoints (Stage 1)
# ===========================================================================

@documents_router.post(
    "/documents/{document_id}/ocr",
    response_model=DocumentOcrResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger OCR Processing for Document",
    description="Extracts embedded text or executes OCR for a document. Department-isolated and permission-controlled.",
)
def trigger_document_ocr(
    document_id: uuid.UUID,
    request: Request,
    payload: Optional[DocumentOcrTriggerRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_any_permission(Permission.CREATE_GRIEVANCE, Permission.EDIT_GRIEVANCE)),
) -> DocumentOcrResponse:
    ip = request.client.host if request.client else None
    languages = payload.languages if payload else None
    doc = ocr_service.process_document_ocr(
        db=db,
        document_id=document_id,
        languages=languages,
        current_user=current_user,
        ip_address=ip,
    )
    return DocumentOcrResponse.model_validate(doc)


@documents_router.get(
    "/documents/{document_id}/ocr",
    response_model=DocumentOcrResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Document OCR Results",
    description="Retrieves OCR status, extracted text, and review state for an authorized document.",
)
def get_document_ocr_result(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_GRIEVANCE)),
) -> DocumentOcrResponse:
    doc = ocr_service.get_document_ocr(
        db=db,
        document_id=document_id,
        current_user=current_user,
    )
    return DocumentOcrResponse.model_validate(doc)


@documents_router.patch(
    "/documents/{document_id}/ocr/review",
    response_model=DocumentOcrResponse,
    status_code=status.HTTP_200_OK,
    summary="Save Corrected / Reviewed OCR Text",
    description="Saves manually edited extracted text. The original OCR output is preserved and distinguishable.",
)
def review_document_ocr(
    document_id: uuid.UUID,
    payload: DocumentOcrReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_any_permission(Permission.CREATE_GRIEVANCE, Permission.EDIT_GRIEVANCE)),
) -> DocumentOcrResponse:
    ip = request.client.host if request.client else None
    doc = ocr_service.review_document_ocr(
        db=db,
        document_id=document_id,
        review_in=payload,
        current_user=current_user,
        ip_address=ip,
    )
    return DocumentOcrResponse.model_validate(doc)
