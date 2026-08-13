"""Add explainable per-turn context plans.

Revision ID: 0003_turn_context_plans
Revises: 0002_media_library
"""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0003_turn_context_plans"
down_revision = "0002_media_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "turn_context_plans",
        sa.Column(
            "turn_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.agent_turns.turn_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("current_message_id", sa.BigInteger(), nullable=False),
        sa.Column("current_principal_id", sa.BigInteger()),
        sa.Column("focus_message_id", sa.BigInteger()),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reason_codes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column(
            "related_message_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("candidates_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("resolver_version", sa.Text(), nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )
    op.create_index(
        "idx_turn_context_plans_scope_time",
        "turn_context_plans",
        ["scope_key", "created_at", "turn_id"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "idx_turn_context_plans_scope_time",
        table_name="turn_context_plans",
        schema=schema,
    )
    op.drop_table("turn_context_plans", schema=schema)


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is not a valid identifier")
    return schema
