import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.escalation import Escalation
    from app.models.follow_up_action import FollowUpAction
    from app.models.follow_up_policy import FollowUpPolicy
    from app.models.grievance import Grievance
    from app.models.notification import Notification
    from app.models.user import User


class FollowUpTask(Base):
    """
    Scheduled follow-up reminder activities.
    Prevents duplicate active scheduled tasks per grievance.
    Overdue state is derived dynamically by comparing due_at with CURRENT_TIMESTAMP.
    """
    __tablename__ = "follow_up_tasks"

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
    assigned_to_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    policy_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("follow_up_policies.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="SCHEDULED",
        index=True,
    )  # SCHEDULED, COMPLETED, CANCELLED

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    grievance: Mapped["Grievance"] = relationship("Grievance", back_populates="follow_up_tasks")
    assigned_to_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[assigned_to_user_id])
    policy: Mapped[Optional["FollowUpPolicy"]] = relationship("FollowUpPolicy", back_populates="follow_up_tasks")

    actions: Mapped[List["FollowUpAction"]] = relationship(
        "FollowUpAction",
        back_populates="follow_up_task",
    )
    notifications: Mapped[List["Notification"]] = relationship(
        "Notification",
        back_populates="follow_up_task",
    )
    escalations: Mapped[List["Escalation"]] = relationship(
        "Escalation",
        back_populates="follow_up_task",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('SCHEDULED', 'COMPLETED', 'CANCELLED')",
            name="ck_follow_up_task_status",
        ),
        # Partial unique index to prevent duplicate scheduled tasks for the same grievance
        Index(
            "uq_follow_up_tasks_single_active_scheduled",
            "grievance_id",
            unique=True,
            postgresql_where=(status == "SCHEDULED"),
        ),
        Index("ix_tasks_due_status", "due_at", "status"),
    )

