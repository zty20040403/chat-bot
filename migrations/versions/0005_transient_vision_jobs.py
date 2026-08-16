"""Separate transient vision jobs from the durable sticker library.

Revision ID: 0005_transient_vision_jobs
Revises: 0004_cold_archive
"""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0005_transient_vision_jobs"
down_revision = "0004_cold_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "vision_jobs",
        sa.Column(
            "vision_job_id",
            sa.BigInteger(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("native_message_id", sa.Text(), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("requester_native_user_id", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("mode", sa.Text(), nullable=False, server_default="summary"),
        sa.Column("question", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.BigInteger(), nullable=False),
        sa.Column("lease_until", sa.BigInteger()),
        sa.Column("worker_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("result_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger()),
        sa.Column("expires_at", sa.BigInteger()),
        sa.CheckConstraint(
            "mode IN ('summary', 'detail')",
            name="ck_vision_jobs_mode",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', "
            "'delivered', 'expired')",
            name="ck_vision_jobs_status",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_vision_jobs_due",
        "vision_jobs",
        ["status", "next_attempt_at", "vision_job_id"],
        schema=schema,
    )
    op.create_index(
        "idx_vision_jobs_scope_message",
        "vision_jobs",
        ["scope_key", "native_message_id", "segment_index", "created_at"],
        schema=schema,
    )
    op.create_index(
        "idx_vision_jobs_expiry",
        "vision_jobs",
        ["status", "expires_at", "vision_job_id"],
        schema=schema,
    )

    # Keep every approved sticker, but remove durable copies that were only
    # collected from ordinary images by the previous mixed pipeline.
    op.execute(
        sa.text(
            f'DELETE FROM "{schema}".message_media '
            "WHERE media_kind = 'image'"
        )
    )
    op.execute(
        sa.text(
            f'DELETE FROM "{schema}".media_blobs AS blob '
            f'WHERE NOT EXISTS (SELECT 1 FROM "{schema}".sticker_library AS sticker '
            "WHERE sticker.media_id = blob.media_id)"
        )
    )
    op.execute(
        sa.text(
            f'DELETE FROM "{schema}".semantic_documents '
            "WHERE source_type = 'media' AND scope_key <> 'global:stickers'"
        )
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "idx_vision_jobs_expiry",
        table_name="vision_jobs",
        schema=schema,
    )
    op.drop_index(
        "idx_vision_jobs_scope_message",
        table_name="vision_jobs",
        schema=schema,
    )
    op.drop_index(
        "idx_vision_jobs_due",
        table_name="vision_jobs",
        schema=schema,
    )
    op.drop_table("vision_jobs", schema=schema)


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is not a valid identifier")
    return schema
