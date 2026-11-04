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


class Notification(Base):
    """
    Log and delivery state of citizen and officer reminders/notifications.
    Maintains clean separation between GENERATED, QUEUED, SENT, DELIVERED, and ACKNOWLEDGED.
    Features idempotency keys to strictly guard against duplicate reminders.
    """
    __tablename__ = "notifications"

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
    recipient_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    recipient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_contact: Mapped[str] = mapped_column(String(255), nullable=False)

    notification_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )  # REMINDER, ESCALATION, STATUS_UPDATE, ACKNOWLEDGMENT
    channel: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # EMAIL, SMS, WHATSAPP, IN_APP, POSTAL

    subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="GENERATED",
        index=True,
    )  # GENERATED, QUEUED, SENT, DELIVERED, ACKNOWLEDGED, RESPONSE_RECEIVED, FAILED

    scheduled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    response_received_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Idempotency token to prevent duplicate notification delivery
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    grievance: Mapped["Grievance"] = relationship("Grievance", back_populates="notifications")
    follow_up_task: Mapped[Optional["FollowUpTask"]] = relationship("FollowUpTask", back_populates="notifications")
    recipient_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[recipient_user_id])

    __table_args__ = (
        CheckConstraint(
            "status IN ('GENERATED', 'QUEUED', 'SENT', 'DELIVERED', 'ACKNOWLEDGED', 'RESPONSE_RECEIVED', 'FAILED')",
            name="ck_notifications_status",
        ),
        CheckConstraint(
            "channel IN ('EMAIL', 'SMS', 'WHATSAPP', 'IN_APP', 'POSTAL')",
            name="ck_notifications_channel",
        ),
        Index("ix_notifications_status_scheduled", "status", "scheduled_at"),
    )

