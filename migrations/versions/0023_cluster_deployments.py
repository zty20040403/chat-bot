"""Fixed-revision deployment contracts and fenced deployer leases."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op


revision = "0023_cluster_deployments"
down_revision = "0022_cluster_guardians"
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
        "fleet_deployments",
        sa.Column("deployment_id", sa.Text(), primary_key=True),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("origin_scope", sa.Text(), nullable=False),
        sa.Column("repository_id", sa.Text(), nullable=False),
        sa.Column("source_revision", sa.Text(), nullable=False),
        sa.Column("expected_remote_revision", sa.Text(), nullable=False),
        sa.Column("target_hosts_json", sa.Text(), nullable=False),
        sa.Column("requested_changes_json", sa.Text(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("canary_host_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("failure_policy", sa.Text(), nullable=False),
        sa.Column("contract_json", sa.Text(), nullable=False),
        sa.Column("contract_hash", sa.Text(), nullable=False),
        sa.Column("preflight_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("approval_ref", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("deployer_id", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.BigInteger(), nullable=True),
        sa.Column("fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deadline_at", sa.BigInteger(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("resource_version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint(
            "actor_id",
            "origin_scope",
            "idempotency_key",
            name="uq_fleet_deployment_idempotency",
        ),
        sa.CheckConstraint(
            "length(source_revision) = 40 AND length(expected_remote_revision) = 40 "
            "AND length(contract_hash) = 64",
            name="ck_fleet_deployment_hashes",
        ),
        sa.CheckConstraint(
            "strategy IN ('serial','canary') AND "
            "failure_policy IN ('pause','rollback_deployed')",
            name="ck_fleet_deployment_policy",
        ),
        sa.CheckConstraint(
            "status IN ('preflight_queued','preflighting','awaiting_approval',"
            "'queued','deploying','verifying','succeeded','partial','failed',"
            "'cancelling','cancelled','rolling_back','rolled_back','needs_attention')",
            name="ck_fleet_deployment_status",
        ),
        sa.CheckConstraint(
            "phase IN ('preflight','apply','rollback','complete')",
            name="ck_fleet_deployment_phase",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_fleet_deployments_claim",
        "fleet_deployments",
        ["status", "phase", "deadline_at", "created_at"],
        schema=schema,
    )
    op.create_table(
        "fleet_deployment_targets",
        sa.Column(
            "deployment_id",
            sa.Text(),
            sa.ForeignKey(
                f"{schema}.fleet_deployments.deployment_id",
                ondelete="CASCADE",
            ),
            primary_key=True,
        ),
        sa.Column("host_id", sa.Text(), primary_key=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_toplevel", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_toplevel", sa.Text(), nullable=False, server_default=""),
        sa.Column("actual_toplevel", sa.Text(), nullable=False, server_default=""),
        sa.Column("step", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("verification_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.BigInteger(), nullable=True),
        sa.Column("finished_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','preflighting','build_ready','deploying',"
            "'verifying','succeeded','failed','unknown','rollback_pending','rolling_back',"
            "'rolled_back','rollback_failed','skipped')",
            name="ck_fleet_deployment_target_status",
        ),
        sa.UniqueConstraint(
            "deployment_id",
            "ordinal",
            name="uq_fleet_deployment_target_ordinal",
        ),
        schema=schema,
    )
    op.create_table(
        "fleet_deployment_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "deployment_id",
            sa.Text(),
            sa.ForeignKey(
                f"{schema}.fleet_deployments.deployment_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("host_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("step", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "deployment_id",
            "sequence",
            name="uq_fleet_deployment_event_sequence",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_fleet_deployment_events_timeline",
        "fleet_deployment_events",
        ["deployment_id", "sequence"],
        schema=schema,
    )
    op.create_table(
        "fleet_deployment_approvals",
        sa.Column("approval_id", sa.Text(), primary_key=True),
        sa.Column(
            "deployment_id",
            sa.Text(),
            sa.ForeignKey(
                f"{schema}.fleet_deployments.deployment_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("contract_hash", sa.Text(), nullable=False),
        sa.Column("resource_version", sa.BigInteger(), nullable=False),
        sa.Column("approved_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("consumed_at", sa.BigInteger(), nullable=True),
        schema=schema,
    )
    op.create_table(
        "fleet_maintenance_locks",
        sa.Column("host_id", sa.Text(), primary_key=True),
        sa.Column("deployment_id", sa.Text(), nullable=False),
        sa.Column("deployer_id", sa.Text(), nullable=False),
        sa.Column("fence", sa.BigInteger(), nullable=False),
        sa.Column("lease_expires_at", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_table("fleet_maintenance_locks", schema=schema)
    op.drop_table("fleet_deployment_approvals", schema=schema)
    op.drop_index(
        "ix_fleet_deployment_events_timeline",
        table_name="fleet_deployment_events",
        schema=schema,
    )
    op.drop_table("fleet_deployment_events", schema=schema)
    op.drop_table("fleet_deployment_targets", schema=schema)
    op.drop_index(
        "ix_fleet_deployments_claim",
        table_name="fleet_deployments",
        schema=schema,
    )
    op.drop_table("fleet_deployments", schema=schema)
