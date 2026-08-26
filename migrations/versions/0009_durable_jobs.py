"""Add the generic durable application job queue.

Revision ID: 0009_durable_jobs
Revises: 0008_content_sources
"""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0009_durable_jobs"
down_revision = "0008_content_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "durable_jobs",
        sa.Column("job_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("scope_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_attempt_at", sa.BigInteger(), nullable=False),
        sa.Column("lease_owner", sa.Text(), nullable=False, server_default=""),
        sa.Column("lease_until", sa.BigInteger()),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger()),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_durable_jobs_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_durable_jobs_attempts"),
        sa.CheckConstraint(
            "max_attempts >= 1", name="ck_durable_jobs_max_attempts"
        ),
        schema=schema,
    )
    op.create_index(
        "idx_durable_jobs_due",
        "durable_jobs",
        ["status", "next_attempt_at", "priority", "job_id"],
        schema=schema,
    )
    op.create_index(
        "idx_durable_jobs_scope_time",
        "durable_jobs",
        ["scope_key", "created_at", "job_id"],
        schema=schema,
    )
    op.create_index(
        "idx_durable_jobs_lease",
        "durable_jobs",
        ["status", "lease_until"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "idx_durable_jobs_lease", table_name="durable_jobs", schema=schema
    )
    op.drop_index(
        "idx_durable_jobs_scope_time", table_name="durable_jobs", schema=schema
    )
    op.drop_index(
        "idx_durable_jobs_due", table_name="durable_jobs", schema=schema
    )
    op.drop_table("durable_jobs", schema=schema)


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is not a valid identifier")
    return schema
