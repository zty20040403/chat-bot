"""Add versioned admin control-plane state and audit history.

Revision ID: 0011_admin_control_plane
Revises: 0010_bge_m3_embeddings
"""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0011_admin_control_plane"
down_revision = "0010_bge_m3_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "admin_resource_versions",
        sa.Column("resource_key", sa.Text(), primary_key=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )
    op.create_table(
        "admin_tool_overrides",
        sa.Column("tool_name", sa.Text(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("resource_version", sa.BigInteger(), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )
    op.create_table(
        "admin_audit_log",
        sa.Column("audit_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("resource_key", sa.Text(), nullable=False),
        sa.Column("resource_version", sa.BigInteger(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("before_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("after_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="ck_admin_audit_status",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_admin_audit_created",
        "admin_audit_log",
        ["created_at", "audit_id"],
        schema=schema,
    )
    op.create_index(
        "idx_admin_audit_resource",
        "admin_audit_log",
        ["resource_key", "resource_version"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "idx_admin_audit_resource",
        table_name="admin_audit_log",
        schema=schema,
    )
    op.drop_index(
        "idx_admin_audit_created",
        table_name="admin_audit_log",
        schema=schema,
    )
    op.drop_table("admin_audit_log", schema=schema)
    op.drop_table("admin_tool_overrides", schema=schema)
    op.drop_table("admin_resource_versions", schema=schema)


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is not a valid identifier")
    return schema
