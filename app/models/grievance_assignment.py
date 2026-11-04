import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.grievance import Grievance
    from app.models.user import User


class GrievanceAssignment(Base):
    """
    Maintains assignment audit history while enforcing at most one active assignment per grievance.
    """
    __tablename__ = "grievance_assignments"

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
    assigned_to_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assigned_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    unassigned_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    remarks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    # Relationships
    grievance: Mapped["Grievance"] = relationship("Grievance", back_populates="assignments")
    assigned_to_user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[assigned_to_user_id],
        back_populates="assignments",
    )
    assigned_by_user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[assigned_by_user_id],
    )

    __table_args__ = (
        # Partial unique index enforcing at most one active assignment per grievance
        Index(
            "uq_grievance_assignments_single_active",
            "grievance_id",
            unique=True,
            postgresql_where=(is_active.is_(True)),
        ),
        Index("ix_assignments_user_active", "assigned_to_user_id", "is_active"),
    )

