import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.follow_up_task import FollowUpTask
    from app.models.grievance import Grievance
    from app.models.user import User


class Escalation(Base):
    """
    Formal escalation audit trail when follow-up reminders are unheeded or SLA is breached.
    Preserves complete escalation lineage and supervisory assignments.
    """
    __tablename__ = "escalations"

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
    escalated_from_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    escalated_to_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    escalated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="PENDING",
        index=True,
    )  # PENDING, ACKNOWLEDGED, RESOLVED, DISMISSED

    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    grievance: Mapped["Grievance"] = relationship("Grievance", back_populates="escalations")
    follow_up_task: Mapped[Optional["FollowUpTask"]] = relationship("FollowUpTask", back_populates="escalations")
    escalated_from_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[escalated_from_user_id])
    escalated_to_user: Mapped["User"] = relationship("User", foreign_keys=[escalated_to_user_id])

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'ACKNOWLEDGED', 'RESOLVED', 'DISMISSED')",
            name="ck_escalations_status",
        ),
        Index("ix_escalations_status_trigger", "status", "trigger_date"),
        # Partial unique index to prevent duplicate active escalations for the same grievance
        Index(
            "uq_escalations_active_grievance",
            "grievance_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'ACKNOWLEDGED')"),
        ),
    )

