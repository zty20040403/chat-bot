"""Add automatic cold-archive metadata.

Revision ID: 0004_cold_archive
Revises: 0003_turn_context_plans
"""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0004_cold_archive"
down_revision = "0003_turn_context_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = _schema()
    op.add_column(
        "media_blobs",
        sa.Column("archive_path", sa.Text(), nullable=False, server_default=""),
        schema=schema,
    )
    op.add_column(
        "media_blobs",
        sa.Column("archived_at", sa.BigInteger()),
        schema=schema,
    )
    op.add_column(
        "media_blobs",
        sa.Column("last_accessed_at", sa.BigInteger()),
        schema=schema,
    )
    op.add_column(
        "media_blobs",
        sa.Column("local_deleted_at", sa.BigInteger()),
        schema=schema,
    )
    op.execute(
        sa.text(
            f'UPDATE "{schema}".media_blobs '
            "SET last_accessed_at = last_seen_at WHERE last_accessed_at IS NULL"
        )
    )
    op.create_index(
        "idx_media_blobs_archive",
        "media_blobs",
        ["archived_at", "last_accessed_at", "media_id"],
        schema=schema,
    )

    for name, column in (
        ("body_archive_path", sa.Text()),
        ("body_archive_sha256", sa.String(64)),
        ("body_size_bytes", sa.BigInteger()),
        ("body_archived_at", sa.BigInteger()),
    ):
        op.add_column("deliveries", sa.Column(name, column), schema=schema)
    op.create_index(
        "idx_deliveries_archive",
        "deliveries",
        ["body_archived_at", "updated_at", "delivery_id"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("idx_deliveries_archive", table_name="deliveries", schema=schema)
    for name in (
        "body_archived_at",
        "body_size_bytes",
        "body_archive_sha256",
        "body_archive_path",
    ):
        op.drop_column("deliveries", name, schema=schema)
    op.drop_index("idx_media_blobs_archive", table_name="media_blobs", schema=schema)
    for name in (
        "local_deleted_at",
        "last_accessed_at",
        "archived_at",
        "archive_path",
    ):
        op.drop_column("media_blobs", name, schema=schema)


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is not a valid identifier")
    return schema
