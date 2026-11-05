"""Add OCR fields to documents

Revision ID: c03e2fa456b2
Revises: b02d1ef345a1
Create Date: 2026-11-05 08:15:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c03e2fa456b2'
down_revision: Union[str, None] = 'b02d1ef345a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'documents',
        sa.Column('ocr_status', sa.String(length=30), server_default=sa.text("'NOT_STARTED'"), nullable=False)
    )
    op.create_index('ix_documents_ocr_status', 'documents', ['ocr_status'], unique=False)
    op.add_column('documents', sa.Column('extracted_text', sa.Text(), nullable=True))
    op.add_column('documents', sa.Column('reviewed_text', sa.Text(), nullable=True))
    op.add_column('documents', sa.Column('ocr_language', sa.String(length=50), nullable=True))
    op.add_column('documents', sa.Column('ocr_pages_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('documents', sa.Column('ocr_processed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('documents', sa.Column('ocr_error', sa.Text(), nullable=True))
    op.add_column('documents', sa.Column('ocr_reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('documents', sa.Column('ocr_reviewed_by', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_documents_ocr_reviewed_by_users',
        'documents',
        'users',
        ['ocr_reviewed_by'],
        ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    op.drop_constraint('fk_documents_ocr_reviewed_by_users', 'documents', type_='foreignkey')
    op.drop_column('documents', 'ocr_reviewed_by')
    op.drop_column('documents', 'ocr_reviewed_at')
    op.drop_column('documents', 'ocr_error')
    op.drop_column('documents', 'ocr_processed_at')
    op.drop_column('documents', 'ocr_pages_data')
    op.drop_column('documents', 'ocr_language')
    op.drop_column('documents', 'reviewed_text')
    op.drop_column('documents', 'extracted_text')
    op.drop_index('ix_documents_ocr_status', table_name='documents')
    op.drop_column('documents', 'ocr_status')

