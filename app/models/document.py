import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.grievance import Grievance
    from app.models.user import User


class Document(Base):
    """
    Metadata and secure filesystem/object storage references for grievance attachments.
    Binary files are stored externally; only metadata and references reside in PostgreSQL.
    """
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    grievance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grievances.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    document_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="PETITION",
    )  # PETITION, IDENTITY_PROOF, EVIDENCE, REPORT, RESPONSE, TRANSLATION, OTHER
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")

    # Self-referencing link for translated or derived documents
    parent_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    grievance: Mapped["Grievance"] = relationship("Grievance", back_populates="documents")
    parent_document: Mapped[Optional["Document"]] = relationship(
        "Document",
        remote_side=[id],
        back_populates="derived_documents",
    )
    derived_documents: Mapped[List["Document"]] = relationship(
        "Document",
        back_populates="parent_document",
    )
    uploader: Mapped[Optional["User"]] = relationship("User", foreign_keys=[uploaded_by])

    __table_args__ = (
        CheckConstraint("file_size > 0", name="ck_documents_positive_file_size"),
        Index("ix_documents_grievance_type", "grievance_id", "document_type"),
    )

