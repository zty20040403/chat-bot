"""Persist alert lifecycle events and QQ notification history.

Revision ID: 0012_alert_event_history
Revises: 0011_admin_control_plane
"""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0012_alert_event_history"
down_revision = "0011_admin_control_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "alert_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("alert_key", sa.Text(), nullable=False, unique=True),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("incident_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("instance", sa.Text(), nullable=False, server_default=""),
        sa.Column("peer", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("labels_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("suppressed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("starts_at", sa.BigInteger(), nullable=False),
        sa.Column("first_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("resolved_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('firing', 'resolved')",
            name="ck_alert_events_status",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_alert_events_status",
        "alert_events",
        ["status", "last_seen_at"],
        schema=schema,
    )
    op.create_index(
        "idx_alert_events_started",
        "alert_events",
        ["first_seen_at", "event_id"],
        schema=schema,
    )
    op.create_index(
        "idx_alert_events_incident",
        "alert_events",
        ["incident_key", "status", "last_seen_at"],
        schema=schema,
    )

    op.create_table(
        "alert_notifications",
        sa.Column(
            "notification_id",
            sa.BigInteger(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column("incident_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("alert_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('firing', 'recovery', 'escalation')",
            name="ck_alert_notifications_kind",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_alert_notifications_created",
        "alert_notifications",
        ["created_at", "notification_id"],
        schema=schema,
    )
    op.create_index(
        "idx_alert_notifications_incident",
        "alert_notifications",
        ["incident_key", "created_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "idx_alert_notifications_incident",
        table_name="alert_notifications",
        schema=schema,
    )
    op.drop_index(
        "idx_alert_notifications_created",
        table_name="alert_notifications",
        schema=schema,
    )
    op.drop_table("alert_notifications", schema=schema)
    op.drop_index(
        "idx_alert_events_incident",
        table_name="alert_events",
        schema=schema,
    )
    op.drop_index(
        "idx_alert_events_started",
        table_name="alert_events",
        schema=schema,
    )
    op.drop_index(
        "idx_alert_events_status",
        table_name="alert_events",
        schema=schema,
    )
    op.drop_table("alert_events", schema=schema)


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is not a valid identifier")
    return schema
