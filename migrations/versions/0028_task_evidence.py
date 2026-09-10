"""Persist immutable task-scoped tool evidence for outcome acceptance."""
from __future__ import annotations

import os
import re
from alembic import op
import sqlalchemy as sa

revision = "0028_task_evidence"
down_revision = "0027_external_continuations"
branch_labels = None
depends_on = None


def schema_name():
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("Invalid PostgreSQL schema")
    return schema


def upgrade():
    schema = schema_name()
    op.create_table("subagent_evidence",
        sa.Column("evidence_id", sa.Text(), primary_key=True),
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey(f"{schema}.subagent_tasks.task_id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey(f"{schema}.subagent_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("arguments_json", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("complete", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.BigInteger(), nullable=False), schema=schema)
    op.create_index("idx_subagent_evidence_task", "subagent_evidence", ["task_id", "revision", "run_id"], schema=schema)


def downgrade():
    schema = schema_name()
    if op.get_bind().execute(sa.text(f'SELECT count(*) FROM "{schema}".subagent_evidence')).scalar():
        raise RuntimeError("Export task evidence before removing its schema")
    op.drop_table("subagent_evidence", schema=schema)
