"""Persist external job continuations and recover final outbox enqueue gaps."""
from __future__ import annotations

import os
import re
from alembic import op
import sqlalchemy as sa

revision = "0027_external_continuations"
down_revision = "0026_admin_accounts_otp"
branch_labels = None
depends_on = None

TASK_STATES = "'received','queued','planning','running','verifying','revising','completed','partial','failed','cancelled','cancelling','interrupted'"
RUN_STATES = "'pending','running','succeeded','partial','failed','cancelled','skipped','interrupted'"


def schema_name():
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("Invalid PostgreSQL schema")
    return schema


def upgrade():
    schema = schema_name()
    op.add_column("durable_jobs", sa.Column("resume_on_lease_loss", sa.Integer(), nullable=False, server_default="0"), schema=schema)
    op.execute(sa.text(f'''UPDATE "{schema}".durable_jobs SET resume_on_lease_loss=1 WHERE kind='subagent.workflow' '''))
    for table, states in (("subagent_tasks", TASK_STATES), ("subagent_runs", RUN_STATES)):
        op.drop_constraint(f"ck_{table}_status", table, schema=schema, type_="check")
        op.create_check_constraint(f"ck_{table}_status", table, f"status IN ({states},'waiting_external')", schema=schema)
    op.add_column("subagent_controls", sa.Column("final_queued_revision", sa.Integer(), nullable=False, server_default="0"), schema=schema)
    # Do not resend historical final messages on upgrade. Future gaps reconcile automatically.
    op.execute(sa.text(f'''UPDATE "{schema}".subagent_controls c SET final_queued_revision=c.revision
        FROM "{schema}".subagent_tasks t WHERE t.task_id=c.task_id
        AND t.status IN ('completed','partial','failed','cancelled')'''))
    op.create_table("subagent_external_calls",
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey(f"{schema}.subagent_tasks.task_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey(f"{schema}.subagent_runs.run_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("call_id", sa.Text(), primary_key=True),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("remote_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("response_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.BigInteger(), nullable=False), schema=schema)
    op.create_index("idx_subagent_external_pending", "subagent_external_calls", ["task_id", "revision", "status"], schema=schema)


def downgrade():
    schema = schema_name()
    # Never silently discard a live continuation during rollback.
    bind = op.get_bind()
    live = bind.execute(sa.text(f'''SELECT count(*) FROM "{schema}".subagent_tasks WHERE status='waiting_external' ''')).scalar()
    if live:
        raise RuntimeError("Finish or cancel waiting tasks before removing their continuation records")
    op.drop_table("subagent_external_calls", schema=schema)
    op.drop_column("subagent_controls", "final_queued_revision", schema=schema)
    op.drop_column("durable_jobs", "resume_on_lease_loss", schema=schema)
    op.execute(sa.text(f'''UPDATE "{schema}".subagent_runs SET status='interrupted' WHERE status='waiting_external' '''))
    for table, states in (("subagent_tasks", TASK_STATES), ("subagent_runs", RUN_STATES)):
        op.drop_constraint(f"ck_{table}_status", table, schema=schema, type_="check")
        op.create_check_constraint(f"ck_{table}_status", table, f"status IN ({states})", schema=schema)
