"""Durable experimental incident diagnostics and structured evidence."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op


revision = "0020_incident_diagnostics"
down_revision = "0019_cluster_control"
branch_labels = None
depends_on = None


def _schema() -> str:
    value = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise RuntimeError("Invalid PostgreSQL schema")
    return value


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "diagnostic_runs",
        sa.Column("run_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("host_id", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("conclusion_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("probe_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running','completed','inconclusive','failed')",
            name="ck_diagnostic_runs_status",
        ),
        sa.CheckConstraint(
            "confidence IN ('confirmed','supported','unknown','contradicted')",
            name="ck_diagnostic_runs_confidence",
        ),
        schema=schema,
    )
    op.create_table(
        "diagnostic_evidence",
        sa.Column("evidence_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.diagnostic_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phase", sa.Integer(), nullable=False),
        sa.Column("source_backend", sa.Text(), nullable=False),
        sa.Column("source_version", sa.Text(), nullable=False),
        sa.Column("observer_host", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_ref", sa.Text(), nullable=False),
        sa.Column("check_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("facts_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("observed_at", sa.BigInteger(), nullable=True),
        sa.Column("received_at", sa.BigInteger(), nullable=False),
        sa.Column("valid_for_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_ref", sa.Text(), nullable=False, server_default=""),
        sa.Column("sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("phase IN (1, 2)", name="ck_diagnostic_evidence_phase"),
        sa.CheckConstraint(
            "status IN ('passed','failed','warning','unknown')",
            name="ck_diagnostic_evidence_status",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_diagnostic_runs_recent",
        "diagnostic_runs",
        ["started_at", "run_id"],
        schema=schema,
    )
    op.create_index(
        "ix_diagnostic_evidence_run",
        "diagnostic_evidence",
        ["run_id", "phase", "evidence_id"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "ix_diagnostic_evidence_run",
        table_name="diagnostic_evidence",
        schema=schema,
    )
    op.drop_index(
        "ix_diagnostic_runs_recent",
        table_name="diagnostic_runs",
        schema=schema,
    )
    op.drop_table("diagnostic_evidence", schema=schema)
    op.drop_table("diagnostic_runs", schema=schema)
