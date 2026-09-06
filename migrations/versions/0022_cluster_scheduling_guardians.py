"""Borrow-aware scheduling, resumable checkpoints, incidents, and guardians."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op


revision = "0022_cluster_guardians"
down_revision = "0021_cluster_execution"
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
        "fleet_worker_policies",
        sa.Column(
            "worker_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_workers.worker_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("owner_actor_id", sa.Text(), nullable=False),
        sa.Column("desired_availability", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("allow_gpu", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cpu_limit_millis", sa.Integer(), nullable=False),
        sa.Column("memory_limit_bytes", sa.BigInteger(), nullable=False),
        sa.Column("gpu_limit_slots", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resource_version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "desired_availability IN ('available','draining','unavailable')",
            name="ck_fleet_worker_policy_availability",
        ),
        schema=schema,
    )
    op.create_table(
        "fleet_borrow_grants",
        sa.Column("grant_id", sa.Text(), primary_key=True),
        sa.Column(
            "worker_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_workers.worker_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_actor_id", sa.Text(), nullable=False),
        sa.Column("grantee_actor_id", sa.Text(), nullable=False),
        sa.Column("origin_scope", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("allowed_kinds_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("valid_from", sa.BigInteger(), nullable=False),
        sa.Column("valid_until", sa.BigInteger(), nullable=False),
        sa.Column("cpu_limit_millis", sa.Integer(), nullable=False),
        sa.Column("memory_limit_bytes", sa.BigInteger(), nullable=False),
        sa.Column("gpu_limit_slots", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("budget_limit_microunits", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("budget_reserved_microunits", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("budget_spent_microunits", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("max_priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("resource_version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "status IN ('available','draining','revoked','expired')",
            name="ck_fleet_borrow_grant_status",
        ),
        sa.CheckConstraint(
            "valid_until > valid_from AND cpu_limit_millis >= 0 AND "
            "memory_limit_bytes >= 0 AND gpu_limit_slots >= 0",
            name="ck_fleet_borrow_grant_limits",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_fleet_borrow_grants_match",
        "fleet_borrow_grants",
        ["worker_id", "status", "valid_from", "valid_until"],
        schema=schema,
    )

    for name, column in (
        ("priority", sa.Column("priority", sa.Integer(), nullable=False, server_default="50")),
        ("grant_id", sa.Column("grant_id", sa.Text(), nullable=True)),
        ("reserved_cost_microunits", sa.Column("reserved_cost_microunits", sa.BigInteger(), nullable=False, server_default="0")),
        ("settled_cost_microunits", sa.Column("settled_cost_microunits", sa.BigInteger(), nullable=False, server_default="0")),
        ("resume_checkpoint_id", sa.Column("resume_checkpoint_id", sa.Text(), nullable=True)),
        ("scheduler_reason", sa.Column("scheduler_reason", sa.Text(), nullable=False, server_default="")),
    ):
        op.add_column("fleet_worker_jobs", column, schema=schema)
    op.create_foreign_key(
        "fk_fleet_worker_jobs_grant",
        "fleet_worker_jobs", "fleet_borrow_grants",
        ["grant_id"], ["grant_id"], source_schema=schema, referent_schema=schema,
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_fleet_worker_jobs_schedule",
        "fleet_worker_jobs",
        ["status", "priority", "deadline_at", "created_at"],
        schema=schema,
    )
    op.create_table(
        "fleet_job_checkpoints",
        sa.Column("checkpoint_id", sa.Text(), primary_key=True),
        sa.Column(
            "job_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_worker_jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("fence", sa.BigInteger(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("format_version", sa.Integer(), nullable=False),
        sa.Column("executor_version", sa.Text(), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("state_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("job_id", "sequence", name="uq_fleet_job_checkpoint_sequence"),
        sa.CheckConstraint(
            "phase IN ('started','progress','completed') AND length(state_hash) = 64",
            name="ck_fleet_job_checkpoint_shape",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_fleet_job_checkpoints_latest",
        "fleet_job_checkpoints", ["job_id", "sequence"], schema=schema,
    )

    op.create_table(
        "fleet_incidents",
        sa.Column("incident_id", sa.Text(), primary_key=True),
        sa.Column("incident_key", sa.Text(), nullable=False),
        sa.Column("host_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("service_ref", sa.Text(), nullable=False, server_default=""),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("visibility_scope", sa.Text(), nullable=False, server_default="admin"),
        sa.Column("first_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("resolved_at", sa.BigInteger(), nullable=True),
        sa.Column("resource_version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "severity IN ('info','warning','critical')",
            name="ck_fleet_incident_severity",
        ),
        sa.CheckConstraint(
            "status IN ('open','investigating','mitigated','resolved')",
            name="ck_fleet_incident_status",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_fleet_incidents_lifecycle",
        "fleet_incidents", ["incident_key", "status", "last_seen_at"], schema=schema,
    )
    op.create_table(
        "fleet_incident_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "incident_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_incidents.incident_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("occurred_at", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "confidence IN ('confirmed','supported','unknown','contradicted')",
            name="ck_fleet_incident_event_confidence",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_fleet_incident_events_timeline",
        "fleet_incident_events", ["incident_id", "occurred_at", "event_id"], schema=schema,
    )
    op.create_table(
        "fleet_runbook_cases",
        sa.Column("case_id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("host_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("service_ref", sa.Text(), nullable=False, server_default=""),
        sa.Column("symptoms", sa.Text(), nullable=False),
        sa.Column("confirmed_cause", sa.Text(), nullable=False),
        sa.Column("resolution_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("applicability_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("evidence_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','verified','deprecated')",
            name="ck_fleet_runbook_case_status",
        ),
        sa.CheckConstraint(
            "confidence IN ('confirmed','supported','unknown')",
            name="ck_fleet_runbook_case_confidence",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_fleet_runbook_cases_search",
        "fleet_runbook_cases", ["status", "service_ref", "updated_at"], schema=schema,
    )

    op.create_table(
        "fleet_guardians",
        sa.Column("guardian_id", sa.Text(), primary_key=True),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("origin_scope", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("host_id", sa.Text(), nullable=False),
        sa.Column("service_ref", sa.Text(), nullable=False, server_default=""),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("failure_threshold", sa.Integer(), nullable=False),
        sa.Column("max_actions", sa.Integer(), nullable=False),
        sa.Column("actions_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("probe_policy_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("authorized_action_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_checked_at", sa.BigInteger(), nullable=True),
        sa.Column("next_check_at", sa.BigInteger(), nullable=False),
        sa.Column("check_lease_owner", sa.Text(), nullable=True),
        sa.Column("check_lease_until", sa.BigInteger(), nullable=True),
        sa.Column("resource_version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "mode IN ('observe','remediate')",
            name="ck_fleet_guardian_mode",
        ),
        sa.CheckConstraint(
            "status IN ('scheduled','active','paused','completed','needs_attention','cancelled')",
            name="ck_fleet_guardian_status",
        ),
        sa.CheckConstraint(
            "expires_at > starts_at AND interval_seconds >= 15 AND "
            "failure_threshold >= 1 AND max_actions >= 0",
            name="ck_fleet_guardian_limits",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_fleet_guardians_due",
        "fleet_guardians", ["status", "next_check_at", "expires_at"], schema=schema,
    )
    op.create_table(
        "fleet_guardian_checks",
        sa.Column("check_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "guardian_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_guardians.guardian_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("facts_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("incident_id", sa.Text(), nullable=True),
        sa.Column("operation_id", sa.Text(), nullable=True),
        sa.Column("model_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("checked_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "status IN ('passed','failed','unknown')",
            name="ck_fleet_guardian_check_status",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_fleet_guardian_checks_recent",
        "fleet_guardian_checks", ["guardian_id", "checked_at", "check_id"], schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    for table in (
        "fleet_guardian_checks", "fleet_guardians", "fleet_runbook_cases",
        "fleet_incident_events", "fleet_incidents", "fleet_job_checkpoints",
    ):
        op.drop_index(
            {
                "fleet_guardian_checks": "ix_fleet_guardian_checks_recent",
                "fleet_guardians": "ix_fleet_guardians_due",
                "fleet_runbook_cases": "ix_fleet_runbook_cases_search",
                "fleet_incident_events": "ix_fleet_incident_events_timeline",
                "fleet_incidents": "ix_fleet_incidents_lifecycle",
                "fleet_job_checkpoints": "ix_fleet_job_checkpoints_latest",
            }[table], table_name=table, schema=schema,
        )
        op.drop_table(table, schema=schema)
    op.drop_index("ix_fleet_worker_jobs_schedule", table_name="fleet_worker_jobs", schema=schema)
    op.drop_constraint(
        "fk_fleet_worker_jobs_grant", "fleet_worker_jobs", schema=schema,
        type_="foreignkey",
    )
    for name in (
        "scheduler_reason", "resume_checkpoint_id", "settled_cost_microunits",
        "reserved_cost_microunits", "grant_id", "priority",
    ):
        op.drop_column("fleet_worker_jobs", name, schema=schema)
    op.drop_index("ix_fleet_borrow_grants_match", table_name="fleet_borrow_grants", schema=schema)
    op.drop_table("fleet_borrow_grants", schema=schema)
    op.drop_table("fleet_worker_policies", schema=schema)
