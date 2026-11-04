import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import Boolean, DateTime, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.follow_up_policy import FollowUpPolicy
    from app.models.message_template import MessageTemplate
    from app.models.office import Office
    from app.models.user import User


class Department(Base):
    """
    Government departments (e.g., Police, Revenue, Health).
    Initially tailored for the Police Department, extensible to all government agencies.
    """
    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
    offices: Mapped[List["Office"]] = relationship(
        "Office",
        back_populates="department",
        cascade="all, delete-orphan",
    )
    users: Mapped[List["User"]] = relationship(
        "User",
        back_populates="department",
    )
    categories: Mapped[List["Category"]] = relationship(
        "Category",
        back_populates="department",
        cascade="all, delete-orphan",
    )
    follow_up_policies: Mapped[List["FollowUpPolicy"]] = relationship(
        "FollowUpPolicy",
        back_populates="department",
        cascade="all, delete-orphan",
    )
    message_templates: Mapped[List["MessageTemplate"]] = relationship(
        "MessageTemplate",
        back_populates="department",
    )

