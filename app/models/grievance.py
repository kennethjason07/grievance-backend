import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.activity_log import ActivityLog
    from app.models.category import Category
    from app.models.department import Department
    from app.models.document import Document
    from app.models.escalation import Escalation
    from app.models.follow_up_action import FollowUpAction
    from app.models.follow_up_task import FollowUpTask
    from app.models.grievance_assignment import GrievanceAssignment
    from app.models.notification import Notification
    from app.models.office import Office
    from app.models.user import User


class Grievance(Base):
    """
    Core grievance entity representing an individual citizen complaint.
    Maintains clean separation between suggested and confirmed departments,
    tracks lifecycle statuses, priorities, and escalation schedules.
    """
    __tablename__ = "grievances"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    grievance_number: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    neutral_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="ONLINE_PORTAL",
    )  # ONLINE_PORTAL, IN_PERSON, WRITTEN_LETTER, HELPLINE, SOCIAL_MEDIA

    original_language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    translated_language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    original_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    translated_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Department separation: suggested vs confirmed
    suggested_department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    confirmed_department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    assigned_to_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    office_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # Lifecycle & Priority
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="NEW",
        index=True,
    )
    priority: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="MEDIUM",
        index=True,
    )

    date_received: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    estimated_resolution_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_action_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    next_follow_up_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    suggested_department: Mapped[Optional["Department"]] = relationship(
        "Department",
        foreign_keys=[suggested_department_id],
    )
    confirmed_department: Mapped[Optional["Department"]] = relationship(
        "Department",
        foreign_keys=[confirmed_department_id],
    )
    category: Mapped[Optional["Category"]] = relationship(
        "Category",
        back_populates="grievances",
    )
    assigned_to_user: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[assigned_to_user_id],
        back_populates="assigned_grievances",
    )
    creator: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[created_by],
    )
    office: Mapped[Optional["Office"]] = relationship(
        "Office",
        back_populates="grievances",
    )

    assignments: Mapped[List["GrievanceAssignment"]] = relationship(
        "GrievanceAssignment",
        back_populates="grievance",
        cascade="all, delete-orphan",
    )
    documents: Mapped[List["Document"]] = relationship(
        "Document",
        back_populates="grievance",
        cascade="all, delete-orphan",
    )
    follow_up_tasks: Mapped[List["FollowUpTask"]] = relationship(
        "FollowUpTask",
        back_populates="grievance",
        cascade="all, delete-orphan",
    )
    follow_up_actions: Mapped[List["FollowUpAction"]] = relationship(
        "FollowUpAction",
        back_populates="grievance",
        cascade="all, delete-orphan",
    )
    notifications: Mapped[List["Notification"]] = relationship(
        "Notification",
        back_populates="grievance",
        cascade="all, delete-orphan",
    )
    escalations: Mapped[List["Escalation"]] = relationship(
        "Escalation",
        back_populates="grievance",
        cascade="all, delete-orphan",
    )
    activity_logs: Mapped[List["ActivityLog"]] = relationship(
        "ActivityLog",
        back_populates="grievance",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('NEW', 'UNDER_REVIEW', 'ASSIGNED', 'AWAITING_INFORMATION', 'IN_PROGRESS', 'RESOLVED', 'CLOSED')",
            name="ck_grievances_status",
        ),
        CheckConstraint(
            "priority IN ('LOW', 'MEDIUM', 'HIGH', 'URGENT')",
            name="ck_grievances_priority",
        ),
        Index("ix_grievances_dept_status", "confirmed_department_id", "status"),
        Index("ix_grievances_assigned_status", "assigned_to_user_id", "status"),
    )

