import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.department import Department
    from app.models.follow_up_task import FollowUpTask


class FollowUpPolicy(Base):
    """
    Configurable department-level rules for follow-up reminder intervals,
    escalation triggers, and maximum reminder limits.
    """
    __tablename__ = "follow_up_policies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    first_reminder_after_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    reminder_interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    escalation_after_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    max_reminders: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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
    department: Mapped["Department"] = relationship("Department", back_populates="follow_up_policies")
    follow_up_tasks: Mapped[List["FollowUpTask"]] = relationship(
        "FollowUpTask",
        back_populates="policy",
    )

    __table_args__ = (
        CheckConstraint("first_reminder_after_days >= 0", name="ck_policy_first_reminder_positive"),
        CheckConstraint("reminder_interval_days > 0", name="ck_policy_interval_positive"),
        CheckConstraint("escalation_after_days >= first_reminder_after_days", name="ck_policy_escalation_after_reminder"),
        CheckConstraint("max_reminders >= 0", name="ck_policy_max_reminders_positive"),
        Index("ix_policies_dept_active", "department_id", "is_active"),
    )

