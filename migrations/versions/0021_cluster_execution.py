"""Durable remote operations, workers, reservations, artifacts, and previews."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op


revision = "0021_cluster_execution"
down_revision = "0020_incident_diagnostics"
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
        "fleet_operations",
        sa.Column("operation_id", sa.Text(), primary_key=True),
        sa.Column("task_ref", sa.Text(), nullable=False, server_default=""),
        sa.Column("step_ref", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("origin_scope", sa.Text(), nullable=False),
        sa.Column("host_id", sa.Text(), nullable=False),
        sa.Column("resource_ref", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("operation_version", sa.Integer(), nullable=False),
        sa.Column("arguments_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("backend_ref", sa.Text(), nullable=False),
        sa.Column("backend_binding_version", sa.Integer(), nullable=False),
        sa.Column("expected_state_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("resource_version", sa.BigInteger(), nullable=False),
        sa.Column("approval_ref", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("deadline_at", sa.BigInteger(), nullable=False),
        sa.Column("resource_budget_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("verification_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("compensation_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("contract_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("capability_status", sa.Text(), nullable=False),
        sa.Column("backend_operation_id", sa.Text(), nullable=True),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.BigInteger(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "status IN ('planned','awaiting_approval','queued','running','verifying',"
            "'reconciling','succeeded','failed','needs_attention','cancelling','cancelled')",
            name="ck_fleet_operations_status",
        ),
        sa.CheckConstraint(
            "capability_status IN ('available','not_configured','forbidden','unsupported')",
            name="ck_fleet_operations_capability",
        ),
        sa.CheckConstraint(
            "operation IN ('service.start','service.stop','service.restart')",
            name="ck_fleet_operations_action",
        ),
        sa.UniqueConstraint(
            "actor_id", "origin_scope", "idempotency_key",
            name="uq_fleet_operations_idempotency",
        ),
        schema=schema,
    )
    op.create_table(
        "fleet_operation_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "operation_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_operations.operation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "operation_id", "sequence", name="uq_fleet_operation_event_sequence"
        ),
        schema=schema,
    )
    op.create_table(
        "fleet_approvals",
        sa.Column("approval_id", sa.Text(), primary_key=True),
        sa.Column(
            "operation_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_operations.operation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("contract_hash", sa.Text(), nullable=False),
        sa.Column("resource_version", sa.BigInteger(), nullable=False),
        sa.Column("approved_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("consumed_at", sa.BigInteger(), nullable=True),
        sa.Column("revoked_at", sa.BigInteger(), nullable=True),
        schema=schema,
    )
    op.create_table(
        "fleet_workers",
        sa.Column("worker_id", sa.Text(), primary_key=True),
        sa.Column("host_id", sa.Text(), nullable=False),
        sa.Column("boot_id", sa.Text(), nullable=False),
        sa.Column("protocol_version", sa.Integer(), nullable=False),
        sa.Column("availability", sa.Text(), nullable=False),
        sa.Column("capabilities_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("runtime_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("capacity_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("public_base_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("resource_version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "availability IN ('available','draining','unavailable')",
            name="ck_fleet_workers_availability",
        ),
        schema=schema,
    )
    op.create_table(
        "fleet_worker_jobs",
        sa.Column("job_id", sa.Text(), primary_key=True),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("origin_scope", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("constraints_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "worker_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_workers.worker_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.BigInteger(), nullable=True),
        sa.Column("deadline_at", sa.BigInteger(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','verifying','succeeded','failed','cancelling','cancelled')",
            name="ck_fleet_worker_jobs_status",
        ),
        sa.CheckConstraint(
            "kind IN ('probe.http','artifact.inspect','document.verify','media.inspect','preview.static')",
            name="ck_fleet_worker_jobs_kind",
        ),
        sa.UniqueConstraint(
            "actor_id", "origin_scope", "idempotency_key",
            name="uq_fleet_worker_jobs_idempotency",
        ),
        schema=schema,
    )
    op.create_table(
        "fleet_reservations",
        sa.Column("reservation_id", sa.Text(), primary_key=True),
        sa.Column(
            "job_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_worker_jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "worker_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_workers.worker_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cpu_millis", sa.Integer(), nullable=False),
        sa.Column("memory_bytes", sa.BigInteger(), nullable=False),
        sa.Column("gpu_slots", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("fence", sa.BigInteger(), nullable=False),
        sa.Column("lease_expires_at", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("released_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','released','expired','cancelled')",
            name="ck_fleet_reservations_status",
        ),
        sa.UniqueConstraint("job_id", name="uq_fleet_reservations_job"),
        schema=schema,
    )
    op.create_table(
        "fleet_job_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "job_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_worker_jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("job_id", "sequence", name="uq_fleet_job_event_sequence"),
        schema=schema,
    )
    op.create_table(
        "fleet_artifacts",
        sa.Column("artifact_id", sa.Text(), primary_key=True),
        sa.Column(
            "job_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_worker_jobs.job_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("origin_scope", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("storage_ref", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("validated_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('staged','validated','rejected','archived')",
            name="ck_fleet_artifacts_status",
        ),
        sa.CheckConstraint(
            "size_bytes > 0 AND size_bytes <= 26214400 AND length(sha256) = 64",
            name="ck_fleet_artifacts_shape",
        ),
        schema=schema,
    )
    op.create_table(
        "fleet_previews",
        sa.Column("preview_id", sa.Text(), primary_key=True),
        sa.Column(
            "job_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_worker_jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "artifact_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_artifacts.artifact_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "worker_id", sa.Text(),
            sa.ForeignKey(f"{schema}.fleet_workers.worker_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("origin_scope", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("public_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("health_status", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("cleanup_policy", sa.Text(), nullable=False),
        sa.Column("last_checked_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "state IN ('queued','publishing','active','expired','failed','disabled')",
            name="ck_fleet_previews_state",
        ),
        sa.UniqueConstraint("route", name="uq_fleet_previews_route"),
        schema=schema,
    )

    for table, columns in (
        ("fleet_operations", ["updated_at", "operation_id"]),
        ("fleet_operation_events", ["operation_id", "sequence"]),
        ("fleet_workers", ["last_seen_at", "worker_id"]),
        ("fleet_worker_jobs", ["status", "created_at", "job_id"]),
        ("fleet_reservations", ["worker_id", "status", "lease_expires_at"]),
        ("fleet_job_events", ["job_id", "sequence"]),
        ("fleet_artifacts", ["created_at", "artifact_id"]),
        ("fleet_previews", ["state", "expires_at", "preview_id"]),
    ):
        op.create_index(f"ix_{table}_lookup", table, columns, schema=schema)


def downgrade() -> None:
    schema = _schema()
    for table in (
        "fleet_previews", "fleet_artifacts", "fleet_job_events",
        "fleet_reservations", "fleet_worker_jobs", "fleet_workers",
        "fleet_approvals", "fleet_operation_events", "fleet_operations",
    ):
        op.drop_index(f"ix_{table}_lookup", table_name=table, schema=schema)
        op.drop_table(table, schema=schema)
