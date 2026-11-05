"""
Document Service.
Handles secure file storage on local disk, rigorous security validation,
content signature checking, atomic cleanup, and authorization-enforced retrieval.
"""
import logging
import mimetypes
import re
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.document import Document
from app.models.grievance import Grievance
from app.models.user import User
from app.schemas.document import ALLOWED_DOCUMENT_TYPES
from app.services.audit import log_activity
from app.services.grievance_service import _check_grievance_department_access

logger = logging.getLogger(__name__)
settings = get_settings()

CHUNK_SIZE = 1024 * 1024  # 1 MB

ALLOWED_DOCUMENT_EXTENSIONS: Dict[str, Set[str]] = {
    ".pdf": {"application/pdf"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".doc": {"application/msword", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    },
    ".txt": {"text/plain"},
}

DANGEROUS_EXTENSIONS: Set[str] = {
    ".exe", ".dll", ".bat", ".cmd", ".sh", ".bash", ".ps1", ".vbs",
    ".js", ".jsx", ".ts", ".tsx", ".py", ".pyw", ".pl", ".php",
    ".phtml", ".html", ".htm", ".xhtml", ".jar", ".war", ".ear",
    ".bin", ".msi", ".com", ".scr", ".cpl", ".hta", ".wsf", ".apk",
    ".so", ".elf", ".dylib",
}


def validate_extension_and_mime(raw_filename: str, declared_mime: Optional[str]) -> Tuple[str, str]:
    """
    Validate that the file extension is supported and not dangerous,
    and verify MIME consistency.
    """
    # Extract lower extension
    ext = Path(raw_filename).suffix.lower()

    if not ext or ext in DANGEROUS_EXTENSIONS or ext not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported or dangerous file extension '{ext}'. Allowed extensions: {', '.join(sorted(ALLOWED_DOCUMENT_EXTENSIONS.keys()))}",
        )

    allowed_mimes = ALLOWED_DOCUMENT_EXTENSIONS[ext]

    if declared_mime:
        clean_declared = declared_mime.split(";")[0].strip().lower()
        if clean_declared != "application/octet-stream" and clean_declared not in allowed_mimes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"MIME type mismatch: '{declared_mime}' is not permitted for extension '{ext}'.",
            )
        final_mime = clean_declared if clean_declared in allowed_mimes else next(iter(allowed_mimes))
    else:
        final_mime = next(iter(allowed_mimes))

    return ext, final_mime


def validate_file_signature(ext: str, header: bytes, file_path: Optional[Path] = None) -> None:
    """
    Verify actual file content magic bytes to prevent masquerading.
    """
    if ext == ".pdf":
        if not (header.startswith(b"%PDF-") or b"%PDF-" in header[:1024]):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file signature: Content does not match PDF format.",
            )
    elif ext == ".png":
        if not header.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file signature: Content does not match PNG format.",
            )
    elif ext in {".jpg", ".jpeg"}:
        if not header.startswith(b"\xff\xd8\xff"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file signature: Content does not match JPEG format.",
            )
    elif ext == ".doc":
        if not header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file signature: Content does not match Microsoft Word (.doc) format.",
            )
    elif ext == ".docx":
        if not header.startswith(b"PK\x03\x04"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file signature: Content does not match Microsoft Word (.docx) format.",
            )
        if file_path and file_path.exists():
            try:
                with zipfile.ZipFile(file_path, "r") as zf:
                    namelist = zf.namelist()
                    if "[Content_Types].xml" not in namelist or not any(n.startswith("word/") for n in namelist):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Invalid .docx package structure: Missing Word document components.",
                        )
            except zipfile.BadZipFile:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid file signature: Corrupted .docx package.",
                )
    elif ext == ".txt":
        if b"\x00" in header or header.startswith(b"MZ") or header.startswith(b"\x7fELF"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid text file: Executable or binary content detected.",
            )
        try:
            header.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid text file: File must be UTF-8 encoded text.",
            )


def save_document(
    db: Session,
    grievance_id: uuid.UUID,
    file: UploadFile,
    document_type: str = "PETITION",
    language: str = "en",
    document_name: Optional[str] = None,
    parent_document_id: Optional[uuid.UUID] = None,
    current_user: Optional[User] = None,
    ip_address: Optional[str] = None,
) -> Document:
    """
    Store an uploaded document on disk with rigorous validation and atomic cleanup.
    """
    grievance = db.get(Grievance, grievance_id)
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Grievance with ID {grievance_id} does not exist.",
        )

    if current_user:
        _check_grievance_department_access(current_user, grievance)

    # Validate parent document if specified
    if parent_document_id:
        parent_doc = db.get(Document, parent_document_id)
        if not parent_doc or parent_doc.grievance_id != grievance_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent document not found or belongs to another grievance.",
            )

    # Validate file extension and MIME type
    raw_incoming_name = file.filename or "document"
    ext, mime_type = validate_extension_and_mime(raw_incoming_name, file.content_type)

    # Normalize type and language
    clean_type = document_type.strip().upper() if document_type else "PETITION"
    if clean_type not in ALLOWED_DOCUMENT_TYPES:
        clean_type = "PETITION"

    clean_lang = (language.strip().lower() if language else "en")[:10]

    # Sanitize original filename (strip directories, traversal, null bytes)
    clean_original = Path(raw_incoming_name).name
    clean_original = re.sub(r'[\/\\\x00]', '', clean_original)
    clean_original = re.sub(r'[^a-zA-Z0-9_\-\. ]', '_', clean_original)
    if not clean_original or clean_original.startswith('.'):
        clean_original = f"document{ext}"

    final_doc_name = (document_name.strip() if document_name and document_name.strip() else clean_original)[:255]

    # Server-generated random filename prevents path traversal and collisions
    base_upload_dir = Path(settings.UPLOAD_DIR).resolve()
    target_dir = base_upload_dir / str(grievance_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    unique_stored_name = f"{uuid.uuid4().hex}{ext}"
    file_path = (target_dir / unique_stored_name).resolve()

    if not file_path.is_relative_to(base_upload_dir):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path traversal detected.",
        )

    total_bytes = 0
    header_sample = b""

    try:
        with open(file_path, "wb") as buffer:
            while True:
                chunk = file.file.read(CHUNK_SIZE)
                if not chunk:
                    break
                if total_bytes < 4096:
                    header_sample += chunk[: 4096 - len(header_sample)]
                total_bytes += len(chunk)
                if total_bytes > settings.MAX_UPLOAD_SIZE_BYTES:
                    buffer.close()
                    if file_path.exists():
                        file_path.unlink()
                    max_mb = settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"Uploaded file exceeds maximum limit of {max_mb} MB.",
                    )
                buffer.write(chunk)

        if total_bytes == 0:
            if file_path.exists():
                file_path.unlink()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot upload an empty file.",
            )

        # Validate file content signature (magic bytes)
        validate_file_signature(ext=ext, header=header_sample, file_path=file_path)

    except HTTPException:
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                pass
        raise
    except Exception as exc:
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                pass
        logger.exception("Failed to write uploaded file to disk.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save uploaded file: {str(exc)}",
        )

    # Persist in DB with atomic rollback and disk cleanup on failure
    document = Document(
        grievance_id=grievance_id,
        document_name=final_doc_name,
        original_filename=clean_original[:255],
        storage_path=str(file_path),
        mime_type=mime_type[:100],
        file_size=total_bytes,
        document_type=clean_type,
        language=clean_lang,
        parent_document_id=parent_document_id,
        uploaded_by=current_user.id if current_user else None,
    )

    try:
        db.add(document)
        db.commit()
        db.refresh(document)
    except Exception as db_exc:
        db.rollback()
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                pass
        logger.exception("Database error while persisting document. Cleaned up written file.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database transaction failed during document persistence. Upload aborted.",
        )

    if current_user:
        try:
            log_activity(
                db=db,
                action="DOCUMENT_UPLOADED",
                entity_type="document",
                entity_id=document.id,
                user_id=current_user.id,
                grievance_id=grievance_id,
                new_values={
                    "document_id": str(document.id),
                    "document_name": document.document_name,
                    "original_filename": document.original_filename,
                    "file_size": document.file_size,
                    "mime_type": document.mime_type,
                    "document_type": document.document_type,
                    "language": document.language,
                },
                ip_address=ip_address,
            )
        except Exception:
            logger.warning("Failed to record document upload audit log; document was persisted.")

    return document


def list_documents(
    db: Session,
    grievance_id: uuid.UUID,
    current_user: User,
) -> List[Document]:
    """
    List all documents associated with a grievance, enforcing department scoping.
    """
    grievance = db.get(Grievance, grievance_id)
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Grievance with ID {grievance_id} does not exist.",
        )

    _check_grievance_department_access(current_user, grievance)

    stmt = (
        select(Document)
        .where(Document.grievance_id == grievance_id)
        .order_by(Document.created_at.asc())
    )
    return list(db.scalars(stmt).all())


def get_document(
    db: Session,
    document_id: uuid.UUID,
    current_user: User,
) -> Document:
    """
    Get a single document's metadata with grievance-level authorization.
    """
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with ID {document_id} not found.",
        )

    grievance = db.get(Grievance, doc.grievance_id)
    if not grievance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated grievance not found.",
        )

    _check_grievance_department_access(current_user, grievance)
    return doc


def get_document_file_for_download(
    db: Session,
    document_id: uuid.UUID,
    current_user: User,
) -> Tuple[Path, str, str]:
    """
    Verify access and return the resolved local path, original filename, and mime type.
    Enforces containment within settings.UPLOAD_DIR.
    """
    doc = get_document(db=db, document_id=document_id, current_user=current_user)

    base_upload_dir = Path(settings.UPLOAD_DIR).resolve()
    file_path = Path(doc.storage_path).resolve()

    if not file_path.is_relative_to(base_upload_dir):
        logger.error(f"Potential path traversal blocked: {doc.storage_path}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid storage path.",
        )

    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File content not found on local storage.",
        )

    return file_path, doc.original_filename, doc.mime_type
