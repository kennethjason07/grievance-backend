"""create document_translations table

Revision ID: d04f3ab567c3
Revises: c03e2fa456b2
Create Date: 2026-11-05 08:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd04f3ab567c3'
down_revision: Union[str, None] = 'c03e2fa456b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'document_translations',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source_language', sa.String(length=10), nullable=False),
        sa.Column('target_language', sa.String(length=10), nullable=False),
        sa.Column('text_source', sa.String(length=20), server_default=sa.text("'REVIEWED_OCR'"), nullable=False),
        sa.Column('source_text', sa.Text(), nullable=False),
        sa.Column('translated_text', sa.Text(), nullable=True),
        sa.Column('provider_name', sa.String(length=100), nullable=False),
        sa.Column('model_version', sa.String(length=100), nullable=True),
        sa.Column('status', sa.String(length=30), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('reviewed_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('corrected_text', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['approved_by'], ['users.id'], name='fk_translations_approved_by_users', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_translations_created_by_users', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name='fk_translations_document_id_documents', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name='fk_translations_reviewed_by_users', ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_document_translations_document_id'), 'document_translations', ['document_id'], unique=False)
    op.create_index(op.f('ix_document_translations_status'), 'document_translations', ['status'], unique=False)
    op.create_index('ix_translations_doc_status', 'document_translations', ['document_id', 'status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_translations_doc_status', table_name='document_translations')
    op.drop_index(op.f('ix_document_translations_status'), table_name='document_translations')
    op.drop_index(op.f('ix_document_translations_document_id'), table_name='document_translations')
    op.drop_table('document_translations')

