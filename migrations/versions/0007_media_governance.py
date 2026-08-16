"""Add structured sticker tags and audited media cleanup.

Revision ID: 0007_media_governance
Revises: 0006_async_vision_delivery
"""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0007_media_governance"
down_revision = "0006_async_vision_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = _schema()
    op.add_column(
        "media_analysis",
        sa.Column("subjects_json", sa.Text(), nullable=False, server_default="[]"),
        schema=schema,
    )
    op.add_column(
        "media_analysis",
        sa.Column("actions_json", sa.Text(), nullable=False, server_default="[]"),
        schema=schema,
    )
    op.execute(
        sa.text(
            f"""
            UPDATE "{schema}".media_analysis
            SET subjects_json = json_build_array(summary)::text
            WHERE is_sticker = 1 AND summary <> '' AND subjects_json = '[]'
            """
        )
    )
    op.create_table(
        "media_cleanup_runs",
        sa.Column(
            "cleanup_id",
            sa.BigInteger(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column("cleanup_kind", sa.Text(), nullable=False),
        sa.Column("confirmation_token", sa.String(length=64), nullable=False),
        sa.Column("candidate_count", sa.BigInteger(), nullable=False),
        sa.Column("candidate_bytes", sa.BigInteger(), nullable=False),
        sa.Column("deleted_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("requested_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger()),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'partial', 'failed')",
            name="ck_media_cleanup_runs_status",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_media_cleanup_runs_time",
        "media_cleanup_runs",
        ["requested_at", "cleanup_id"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "idx_media_cleanup_runs_time",
        table_name="media_cleanup_runs",
        schema=schema,
    )
    op.drop_table("media_cleanup_runs", schema=schema)
    op.drop_column("media_analysis", "actions_json", schema=schema)
    op.drop_column("media_analysis", "subjects_json", schema=schema)


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is not a valid identifier")
    return schema
