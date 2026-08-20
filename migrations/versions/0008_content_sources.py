"""Add durable shared-content sources and scoped message links.

Revision ID: 0008_content_sources
Revises: 0007_media_governance
"""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0008_content_sources"
down_revision = "0007_media_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "content_sources",
        sa.Column("source_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False, unique=True),
        sa.Column("remote_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("author", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("comments_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("fetched_at", sa.BigInteger()),
        sa.Column("expires_at", sa.BigInteger()),
        sa.Column("first_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'fetching', 'ready', 'failed')",
            name="ck_content_sources_status",
        ),
        sa.CheckConstraint(
            "content_kind IN ('post', 'video', 'article', 'webpage', 'unknown')",
            name="ck_content_sources_kind",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_content_sources_status_time",
        "content_sources",
        ["status", "updated_at", "source_id"],
        schema=schema,
    )
    op.create_index(
        "idx_content_sources_platform_time",
        "content_sources",
        ["platform", "last_seen_at", "source_id"],
        schema=schema,
    )
    op.create_table(
        "message_sources",
        sa.Column(
            "message_source_id",
            sa.BigInteger(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column(
            "source_id",
            sa.BigInteger(),
            sa.ForeignKey(
                f"{schema}.content_sources.source_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("canonical_message_id", sa.BigInteger()),
        sa.Column("native_message_id", sa.Text(), nullable=False),
        sa.Column("sender_native_user_id", sa.Text(), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "scope_key",
            "native_message_id",
            "segment_index",
            "source_id",
            name="uq_message_sources_location",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_message_sources_scope_time",
        "message_sources",
        ["scope_key", "created_at", "message_source_id"],
        schema=schema,
    )
    op.create_index(
        "idx_message_sources_scope_message",
        "message_sources",
        ["scope_key", "canonical_message_id"],
        schema=schema,
    )
    op.create_index(
        "idx_message_sources_source",
        "message_sources",
        ["source_id", "created_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "idx_message_sources_source",
        table_name="message_sources",
        schema=schema,
    )
    op.drop_index(
        "idx_message_sources_scope_message",
        table_name="message_sources",
        schema=schema,
    )
    op.drop_index(
        "idx_message_sources_scope_time",
        table_name="message_sources",
        schema=schema,
    )
    op.drop_table("message_sources", schema=schema)
    op.drop_index(
        "idx_content_sources_platform_time",
        table_name="content_sources",
        schema=schema,
    )
    op.drop_index(
        "idx_content_sources_status_time",
        table_name="content_sources",
        schema=schema,
    )
    op.drop_table("content_sources", schema=schema)


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is not a valid identifier")
    return schema
