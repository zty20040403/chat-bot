"""Switch semantic vectors to BGE-M3 dimensions.

Revision ID: 0010_bge_m3_embeddings
Revises: 0009_durable_jobs
"""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0010_bge_m3_embeddings"
down_revision = "0009_durable_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = _schema()
    op.execute(
        sa.text(f'DROP INDEX IF EXISTS "{schema}".idx_semantic_embedding_hnsw')
    )
    op.execute(sa.text(f'TRUNCATE TABLE "{schema}".semantic_documents'))
    op.execute(sa.text(f'TRUNCATE TABLE "{schema}".semantic_index_state'))
    op.execute(
        sa.text(
            f'''ALTER TABLE "{schema}".semantic_documents
                ALTER COLUMN embedding TYPE vector(1024)'''
        )
    )
    op.execute(
        sa.text(
            f'''CREATE INDEX idx_semantic_embedding_hnsw
                ON "{schema}".semantic_documents
                USING hnsw (embedding vector_cosine_ops)'''
        )
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(
        sa.text(f'DROP INDEX IF EXISTS "{schema}".idx_semantic_embedding_hnsw')
    )
    op.execute(sa.text(f'TRUNCATE TABLE "{schema}".semantic_documents'))
    op.execute(sa.text(f'TRUNCATE TABLE "{schema}".semantic_index_state'))
    op.execute(
        sa.text(
            f'''ALTER TABLE "{schema}".semantic_documents
                ALTER COLUMN embedding TYPE vector(1536)'''
        )
    )
    op.execute(
        sa.text(
            f'''CREATE INDEX idx_semantic_embedding_hnsw
                ON "{schema}".semantic_documents
                USING hnsw (embedding vector_cosine_ops)'''
        )
    )


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is not a valid identifier")
    return schema
