"""Add fixed-role Sub-Agent task orchestration records.

Revision ID: 0014_subagent_tasks
Revises: 0013_context_intelligence
"""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0014_subagent_tasks"
down_revision = "0013_context_intelligence"
branch_labels = None
depends_on = None


TASK_STATUSES = (
    "received",
    "planning",
    "running",
    "verifying",
    "completed",
    "partial",
    "failed",
    "cancelling",
    "cancelled",
)
RUN_STATUSES = (
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "skipped",
)


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "subagent_tasks",
        sa.Column("task_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("trace_id", sa.Text(), nullable=False, unique=True),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("requester_user_id", sa.BigInteger(), nullable=False),
        sa.Column("trigger_message_id", sa.BigInteger(), nullable=True),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("max_parallelism", sa.Integer(), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN (" + ",".join(f"'{item}'" for item in TASK_STATUSES) + ")",
            name="ck_subagent_tasks_status",
        ),
        sa.CheckConstraint("max_parallelism >= 1", name="ck_subagent_tasks_parallelism"),
        sa.CheckConstraint("max_steps >= 1", name="ck_subagent_tasks_steps"),
        schema=schema,
    )
    op.create_index(
        "idx_subagent_tasks_scope_time",
        "subagent_tasks",
        ["scope_key", "created_at", "task_id"],
        schema=schema,
    )
    op.create_index(
        "idx_subagent_tasks_status_time",
        "subagent_tasks",
        ["status", "updated_at", "task_id"],
        schema=schema,
    )

    op.create_table(
        "subagent_runs",
        sa.Column("run_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.subagent_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_key", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("deliverable", sa.Text(), nullable=False, server_default=""),
        sa.Column("dependencies_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("allowed_tools_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("model_profile", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.BigInteger(), nullable=True),
        sa.Column("finished_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN (" + ",".join(f"'{item}'" for item in RUN_STATUSES) + ")",
            name="ck_subagent_runs_status",
        ),
        sa.UniqueConstraint("task_id", "step_key", name="uq_subagent_task_step"),
        schema=schema,
    )
    op.create_index(
        "idx_subagent_runs_task_status",
        "subagent_runs",
        ["task_id", "status", "run_id"],
        schema=schema,
    )

    op.create_table(
        "subagent_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.subagent_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.subagent_runs.run_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("task_id", "sequence", name="uq_subagent_event_sequence"),
        schema=schema,
    )
    op.create_index(
        "idx_subagent_events_task_sequence",
        "subagent_events",
        ["task_id", "sequence"],
        schema=schema,
    )

    op.create_table(
        "subagent_artifacts",
        sa.Column("artifact_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.subagent_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.subagent_runs.run_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("handle", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("task_id", "handle", name="uq_subagent_task_artifact"),
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_table("subagent_artifacts", schema=schema)
    op.drop_index(
        "idx_subagent_events_task_sequence",
        table_name="subagent_events",
        schema=schema,
    )
    op.drop_table("subagent_events", schema=schema)
    op.drop_index(
        "idx_subagent_runs_task_status",
        table_name="subagent_runs",
        schema=schema,
    )
    op.drop_table("subagent_runs", schema=schema)
    op.drop_index(
        "idx_subagent_tasks_status_time",
        table_name="subagent_tasks",
        schema=schema,
    )
    op.drop_index(
        "idx_subagent_tasks_scope_time",
        table_name="subagent_tasks",
        schema=schema,
    )
    op.drop_table("subagent_tasks", schema=schema)


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is not a valid SQL identifier")
    return schema
