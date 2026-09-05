"""Task revisions, dispatch envelopes and at-most-once delivery intents."""
from __future__ import annotations

import os
import re
from alembic import op
import sqlalchemy as sa

revision = "0018_subagent_controls"
down_revision = "0017_subagent_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("Invalid PostgreSQL schema")
    op.add_column("subagent_sessions", sa.Column("covered_sequence", sa.BigInteger(), nullable=False, server_default="0"), schema=schema)
    op.drop_constraint("ck_subagent_tasks_status", "subagent_tasks", schema=schema, type_="check")
    op.create_check_constraint("ck_subagent_tasks_status", "subagent_tasks",
        "status IN ('received','queued','planning','running','verifying','revising','completed','partial','failed','cancelled','cancelling','interrupted')", schema=schema)
    op.create_table("subagent_controls",
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey(f"{schema}.subagent_tasks.task_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("policy_json", sa.Text(), nullable=False), sa.Column("dispatch_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False), schema=schema)
    op.create_table("subagent_deliveries",
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey(f"{schema}.subagent_tasks.task_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True), sa.Column("delivery_key", sa.Text(), primary_key=True),
        sa.Column("state", sa.Text(), nullable=False), sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False), schema=schema)


def downgrade() -> None:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    op.drop_table("subagent_deliveries", schema=schema)
    op.drop_table("subagent_controls", schema=schema)
    op.drop_column("subagent_sessions", "covered_sequence", schema=schema)
    op.execute(sa.text(f'UPDATE "{schema}".subagent_tasks SET status=\'interrupted\' WHERE status IN (\'queued\',\'revising\')'))
    op.drop_constraint("ck_subagent_tasks_status", "subagent_tasks", schema=schema, type_="check")
    op.create_check_constraint("ck_subagent_tasks_status", "subagent_tasks",
        "status IN ('received','planning','running','verifying','completed','partial','failed','cancelled','cancelling','interrupted')", schema=schema)
