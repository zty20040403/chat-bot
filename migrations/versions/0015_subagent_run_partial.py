"""Add an explicit partial state for Sub-Agent runs.

Revision ID: 0015_subagent_run_partial
Revises: 0014_subagent_tasks
"""

from __future__ import annotations

import os
import re

from alembic import op


revision = "0015_subagent_run_partial"
down_revision = "0014_subagent_tasks"
branch_labels = None
depends_on = None


BASE_RUN_STATUSES = (
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "skipped",
)


def upgrade() -> None:
    _replace_status_constraint((*BASE_RUN_STATUSES, "partial"))


def downgrade() -> None:
    schema = _schema()
    op.execute(
        f'UPDATE "{schema}".subagent_runs '
        "SET status = 'succeeded' WHERE status = 'partial'"
    )
    _replace_status_constraint(BASE_RUN_STATUSES)


def _replace_status_constraint(statuses: tuple[str, ...]) -> None:
    schema = _schema()
    op.drop_constraint(
        "ck_subagent_runs_status",
        "subagent_runs",
        type_="check",
        schema=schema,
    )
    allowed = ",".join(f"'{status}'" for status in statuses)
    op.create_check_constraint(
        "ck_subagent_runs_status",
        "subagent_runs",
        f"status IN ({allowed})",
        schema=schema,
    )


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is invalid")
    return schema
