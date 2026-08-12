"""Add the durable media library and worker queue.

Revision ID: 0002_media_library
Revises: 0001_unified_postgres
"""

from __future__ import annotations

from collections.abc import Sequence
import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0002_media_library"
down_revision = "0001_unified_postgres"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = _schema()
    bigint_id = lambda name: sa.Column(
        name,
        sa.BigInteger(),
        sa.Identity(),
        primary_key=True,
    )

    op.create_table(
        "media_blobs",
        bigint_id("media_id"),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("prepared_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="ready"),
        sa.Column("first_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("times_seen", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN ('ready', 'quarantined', 'missing')",
            name="ck_media_blobs_status",
        ),
        sa.UniqueConstraint("sha256", name="uq_media_blobs_sha256"),
        schema=schema,
    )
    op.create_index(
        "idx_media_blobs_last_seen",
        "media_blobs",
        ["last_seen_at", "media_id"],
        schema=schema,
    )

    op.create_table(
        "message_media",
        bigint_id("message_media_id"),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("canonical_message_id", sa.BigInteger()),
        sa.Column("native_message_id", sa.Text(), nullable=False),
        sa.Column("sender_native_user_id", sa.Text(), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("media_kind", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "media_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.media_blobs.media_id", ondelete="SET NULL"),
        ),
        sa.Column("fetch_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "media_kind IN ('image', 'sticker')",
            name="ck_message_media_kind",
        ),
        sa.CheckConstraint(
            "fetch_status IN ('pending', 'fetching', 'ready', 'failed')",
            name="ck_message_media_fetch_status",
        ),
        sa.UniqueConstraint(
            "scope_key",
            "native_message_id",
            "segment_index",
            name="uq_message_media_scope_message_segment",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_message_media_scope",
        "message_media",
        ["scope_key", "created_at", "message_media_id"],
        schema=schema,
    )
    op.create_index(
        "idx_message_media_blob",
        "message_media",
        ["media_id", "scope_key"],
        schema=schema,
    )

    op.create_table(
        "media_analysis",
        sa.Column(
            "media_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.media_blobs.media_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("vision_profile", sa.Text(), nullable=False),
        sa.Column("vision_model", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("emotions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("usage_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("is_sticker", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("contains_person", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "contains_private_info",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("safety", sa.Text(), nullable=False, server_default="safe"),
        sa.Column("raw_response_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("analyzed_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("is_sticker IN (0, 1)", name="ck_media_analysis_sticker"),
        sa.CheckConstraint("contains_person IN (0, 1)", name="ck_media_analysis_person"),
        sa.CheckConstraint(
            "contains_private_info IN (0, 1)",
            name="ck_media_analysis_private",
        ),
        sa.CheckConstraint(
            "safety IN ('safe', 'review', 'blocked')",
            name="ck_media_analysis_safety",
        ),
        schema=schema,
    )

    op.create_table(
        "sticker_library",
        sa.Column(
            "media_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.media_blobs.media_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("banned", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("times_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_sent_at", sa.BigInteger()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("enabled IN (0, 1)", name="ck_sticker_library_enabled"),
        sa.CheckConstraint("banned IN (0, 1)", name="ck_sticker_library_banned"),
        schema=schema,
    )

    op.create_table(
        "media_jobs",
        bigint_id("job_id"),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column(
            "message_media_id",
            sa.BigInteger(),
            sa.ForeignKey(
                f"{schema}.message_media.message_media_id",
                ondelete="CASCADE",
            ),
        ),
        sa.Column(
            "media_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.media_blobs.media_id", ondelete="CASCADE"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.BigInteger(), nullable=False),
        sa.Column("lease_until", sa.BigInteger()),
        sa.Column("worker_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger()),
        sa.CheckConstraint(
            "job_type IN ('fetch', 'caption', 'embedding')",
            name="ck_media_jobs_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_media_jobs_status",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_media_jobs_due",
        "media_jobs",
        ["status", "next_attempt_at", "job_id"],
        schema=schema,
    )
    op.create_index(
        "idx_media_jobs_media",
        "media_jobs",
        ["media_id", "job_type", "status"],
        schema=schema,
    )
    op.create_index(
        "uq_media_jobs_active_fetch",
        "media_jobs",
        ["message_media_id", "job_type"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    op.create_index(
        "uq_media_jobs_active_blob",
        "media_jobs",
        ["media_id", "job_type"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    schema = _schema()
    tables: Sequence[str] = (
        "media_jobs",
        "sticker_library",
        "media_analysis",
        "message_media",
        "media_blobs",
    )
    for table in tables:
        op.drop_table(table, schema=schema)


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is not a valid identifier")
    return schema
