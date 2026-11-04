import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.department import Department
    from app.models.grievance import Grievance
    from app.models.grievance_assignment import GrievanceAssignment
    from app.models.office import Office
    from app.models.role import Role


class User(Base):
    """
    Authorised system users operating within departments and offices.
    Never stores plaintext passwords.
    """
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)

    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    office_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

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
    department: Mapped[Optional["Department"]] = relationship("Department", back_populates="users")
    office: Mapped[Optional["Office"]] = relationship("Office", back_populates="users")
    role: Mapped["Role"] = relationship("Role", back_populates="users")

    assigned_grievances: Mapped[List["Grievance"]] = relationship(
        "Grievance",
        foreign_keys="[Grievance.assigned_to_user_id]",
        back_populates="assigned_to_user",
    )
    assignments: Mapped[List["GrievanceAssignment"]] = relationship(
        "GrievanceAssignment",
        foreign_keys="[GrievanceAssignment.assigned_to_user_id]",
        back_populates="assigned_to_user",
    )

    __table_args__ = (
        Index("ix_users_dept_role", "department_id", "role_id"),
    )

