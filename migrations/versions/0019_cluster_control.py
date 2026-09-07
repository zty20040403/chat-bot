"""Durable Ops backend state and fleet evidence projections."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op


revision = "0019_cluster_control"
down_revision = "0018_subagent_controls"
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
        "fleet_backend_states",
        sa.Column("backend_name", sa.Text(), primary_key=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("catalog_version", sa.Integer(), nullable=False),
        sa.Column("operations_json", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_success_at", sa.BigInteger(), nullable=True),
        sa.Column("checked_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "state IN ('disabled','unknown','online','unavailable')",
            name="ck_fleet_backend_states_state",
        ),
        schema=schema,
    )
    op.create_table(
        "fleet_observations",
        sa.Column(
            "observation_id",
            sa.BigInteger(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column("source_backend", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("params_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("observed_at", sa.BigInteger(), nullable=True),
        sa.Column("received_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=False, server_default=""),
        sa.CheckConstraint(
            "status IN ('fresh','stale','unavailable','forbidden','unsupported','invalid_request')",
            name="ck_fleet_observations_status",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_fleet_observations_recent",
        "fleet_observations",
        ["received_at", "observation_id"],
        unique=False,
        schema=schema,
    )
    op.create_index(
        "ix_fleet_observations_target",
        "fleet_observations",
        ["source_backend", "operation", "target_key", "received_at"],
        unique=False,
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "ix_fleet_observations_target",
        table_name="fleet_observations",
        schema=schema,
    )
    op.drop_index(
        "ix_fleet_observations_recent",
        table_name="fleet_observations",
        schema=schema,
    )
    op.drop_table("fleet_observations", schema=schema)
    op.drop_table("fleet_backend_states", schema=schema)
