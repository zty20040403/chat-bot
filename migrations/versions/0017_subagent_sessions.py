"""Persist isolated, versioned transcripts for each Sub-Agent step."""
from __future__ import annotations

import os
import re
from alembic import op
import sqlalchemy as sa

revision = "0017_subagent_sessions"
down_revision = "0016_subagent_checkpoints"
branch_labels = None
depends_on = None


def _schema() -> str:
    value = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise RuntimeError("AI_POSTGRES_SCHEMA is invalid")
    return value


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "subagent_sessions",
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey(f"{schema}.subagent_runs.run_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey(f"{schema}.subagent_tasks.task_id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("transcript_json", sa.Text(), nullable=False),
        sa.Column("model_profile", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )
    op.create_index("idx_subagent_sessions_task", "subagent_sessions", ["task_id"], schema=schema)


def downgrade() -> None:
    op.drop_table("subagent_sessions", schema=_schema())
