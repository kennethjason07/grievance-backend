import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.follow_up_task import FollowUpTask
    from app.models.grievance import Grievance
    from app.models.user import User


class FollowUpAction(Base):
    """
    Log of actual follow-up actions executed (phone calls, letters, citizen interactions, etc.).
    Keeps response receipt and action recorded as distinct concepts.
    """
    __tablename__ = "follow_up_actions"

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
    follow_up_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("follow_up_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    performed_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    action_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )  # CALL_ATTEMPTED, EMAIL_SENT, LETTER_SENT, OFFICER_CONTACTED, CITIZEN_CONTACTED,
    # DOCUMENT_REQUESTED, RESPONSE_RECEIVED, FIELD_VISIT, STATUS_UPDATED, OTHER

    description: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_received: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    action_taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    next_follow_up_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    grievance: Mapped["Grievance"] = relationship("Grievance", back_populates="follow_up_actions")
    follow_up_task: Mapped[Optional["FollowUpTask"]] = relationship("FollowUpTask", back_populates="actions")
    performed_by_user: Mapped["User"] = relationship("User", foreign_keys=[performed_by_user_id])

    __table_args__ = (
        Index("ix_actions_grievance_taken", "grievance_id", "action_taken_at"),
    )

