"""Add partial unique index for active escalations

Revision ID: b02d1ef345a1
Revises: a01c9af298b3
Create Date: 2026-11-04 18:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b02d1ef345a1'
down_revision: Union[str, None] = 'a01c9af298b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_escalations_active_grievance",
        "escalations",
        ["grievance_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'ACKNOWLEDGED')"),
    )


def downgrade() -> None:
    op.drop_index("uq_escalations_active_grievance", table_name="escalations")

