import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.user import User


class DocumentTranslation(Base):
    """
    Dedicated entity for document translations.
    Preserves full translation history, distinct from OCR raw/reviewed text.
    Machine translations remain unverified until reviewed and approved by an authorized officer.
    """
    __tablename__ = "document_translations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_language: Mapped[str] = mapped_column(String(10), nullable=False)
    target_language: Mapped[str] = mapped_column(String(10), nullable=False)
    text_source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="REVIEWED_OCR",
        server_default=text("'REVIEWED_OCR'"),
    )  # RAW_OCR or REVIEWED_OCR
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    translated_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="PENDING",
        server_default=text("'PENDING'"),
        index=True,
    )  # PENDING, PROCESSING, COMPLETED, FAILED, UNDER_REVIEW, APPROVED

    # User lifecycle tracking
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Human edits & error tracking
    corrected_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    document: Mapped["Document"] = relationship("Document", back_populates="translations")
    creator: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by])
    reviewer: Mapped[Optional["User"]] = relationship("User", foreign_keys=[reviewed_by])
    approver: Mapped[Optional["User"]] = relationship("User", foreign_keys=[approved_by])

    __table_args__ = (
        Index("ix_translations_doc_status", "document_id", "status"),
    )

