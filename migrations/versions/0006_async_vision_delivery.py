"""Deliver transient vision results through the durable outbox.

Revision ID: 0006_async_vision_delivery
Revises: 0005_transient_vision_jobs
"""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0006_async_vision_delivery"
down_revision = "0005_transient_vision_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = _schema()
    op.add_column(
        "vision_jobs",
        sa.Column(
            "auto_deliver",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema=schema,
    )
    op.add_column(
        "vision_jobs",
        sa.Column("target_platform", sa.Text(), nullable=False, server_default=""),
        schema=schema,
    )
    op.add_column(
        "vision_jobs",
        sa.Column("target_kind", sa.Text(), nullable=False, server_default=""),
        schema=schema,
    )
    op.add_column(
        "vision_jobs",
        sa.Column(
            "target_native_conversation_id",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        schema=schema,
    )
    op.add_column(
        "vision_jobs",
        sa.Column(
            "reply_to_native_message_id",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        schema=schema,
    )
    op.add_column(
        "vision_jobs",
        sa.Column("delivery_id", sa.BigInteger()),
        schema=schema,
    )
    op.add_column(
        "vision_jobs",
        sa.Column("delivery_enqueued_at", sa.BigInteger()),
        schema=schema,
    )
    op.create_index(
        "idx_vision_jobs_delivery",
        "vision_jobs",
        ["auto_deliver", "status", "delivery_enqueued_at", "vision_job_id"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "idx_vision_jobs_delivery",
        table_name="vision_jobs",
        schema=schema,
    )
    for name in (
        "delivery_enqueued_at",
        "delivery_id",
        "reply_to_native_message_id",
        "target_native_conversation_id",
        "target_kind",
        "target_platform",
        "auto_deliver",
    ):
        op.drop_column("vision_jobs", name, schema=schema)


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is not a valid identifier")
    return schema
