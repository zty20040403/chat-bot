"""Add resumable Sub-Agent checkpoints and interrupted states.

Revision ID: 0016_subagent_checkpoints
Revises: 0015_subagent_run_partial
"""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0016_subagent_checkpoints"
down_revision = "0015_subagent_run_partial"
branch_labels = None
depends_on = None


TASK_STATUSES = (
    "received",
    "planning",
    "running",
    "verifying",
    "interrupted",
    "completed",
    "partial",
    "failed",
    "cancelling",
    "cancelled",
)
RUN_STATUSES = (
    "pending",
    "running",
    "interrupted",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
    "skipped",
)


def upgrade() -> None:
    schema = _schema()
    _replace_constraint(
        "subagent_tasks",
        "ck_subagent_tasks_status",
        TASK_STATUSES,
        schema,
    )
    _replace_constraint(
        "subagent_runs",
        "ck_subagent_runs_status",
        RUN_STATUSES,
        schema,
    )
    op.create_table(
        "subagent_checkpoints",
        sa.Column("checkpoint_id", sa.BigInteger(), sa.Identity(), primary_key=True),
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
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "task_id",
            "sequence",
            name="uq_subagent_checkpoint_sequence",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_subagent_checkpoints_task_sequence",
        "subagent_checkpoints",
        ["task_id", "sequence"],
        schema=schema,
    )
    op.create_table(
        "subagent_run_contexts",
        sa.Column("context_id", sa.BigInteger(), sa.Identity(), primary_key=True),
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
            nullable=False,
            unique=True,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("context_hash", sa.Text(), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )
    op.create_index(
        "idx_subagent_run_contexts_task",
        "subagent_run_contexts",
        ["task_id", "run_id"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "idx_subagent_run_contexts_task",
        table_name="subagent_run_contexts",
        schema=schema,
    )
    op.drop_table("subagent_run_contexts", schema=schema)
    op.drop_index(
        "idx_subagent_checkpoints_task_sequence",
        table_name="subagent_checkpoints",
        schema=schema,
    )
    op.drop_table("subagent_checkpoints", schema=schema)
    op.execute(
        f'UPDATE "{schema}".subagent_tasks '
        "SET status = 'failed', last_error = '恢复到旧版本时中断任务已失败' "
        "WHERE status = 'interrupted'"
    )
    op.execute(
        f'UPDATE "{schema}".subagent_runs '
        "SET status = 'failed', last_error = '恢复到旧版本时中断步骤已失败' "
        "WHERE status = 'interrupted'"
    )
    _replace_constraint(
        "subagent_tasks",
        "ck_subagent_tasks_status",
        tuple(item for item in TASK_STATUSES if item != "interrupted"),
        schema,
    )
    _replace_constraint(
        "subagent_runs",
        "ck_subagent_runs_status",
        tuple(item for item in RUN_STATUSES if item != "interrupted"),
        schema,
    )


def _replace_constraint(
    table: str,
    name: str,
    statuses: tuple[str, ...],
    schema: str,
) -> None:
    op.drop_constraint(name, table, type_="check", schema=schema)
    allowed = ",".join(f"'{status}'" for status in statuses)
    op.create_check_constraint(name, table, f"status IN ({allowed})", schema=schema)


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is invalid")
    return schema
