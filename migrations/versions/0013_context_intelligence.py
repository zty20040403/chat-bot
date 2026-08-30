"""Add durable Historian metadata and scope-local topic graph edges.

Revision ID: 0013_context_intelligence
Revises: 0012_alert_event_history
"""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0013_context_intelligence"
down_revision = "0012_alert_event_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = _schema()
    op.add_column(
        "context_compartments",
        sa.Column("summary_p4", sa.Text(), nullable=False, server_default=""),
        schema=schema,
    )
    op.add_column(
        "context_compartments",
        sa.Column("topic", sa.Text(), nullable=False, server_default=""),
        schema=schema,
    )
    op.add_column(
        "context_compartments",
        sa.Column(
            "importance",
            sa.Float(),
            nullable=False,
            server_default="0.5",
        ),
        schema=schema,
    )
    op.add_column(
        "context_compartments",
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default="0.5",
        ),
        schema=schema,
    )
    op.add_column(
        "context_compartments",
        sa.Column(
            "participants_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        schema=schema,
    )
    op.add_column(
        "context_compartments",
        sa.Column(
            "evidence_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        schema=schema,
    )
    op.add_column(
        "context_compartments",
        sa.Column(
            "generation_mode",
            sa.Text(),
            nullable=False,
            server_default="legacy",
        ),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_context_importance",
        "context_compartments",
        "importance >= 0 AND importance <= 1",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_context_confidence",
        "context_compartments",
        "confidence >= 0 AND confidence <= 1",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_context_generation_mode",
        "context_compartments",
        "generation_mode IN ('legacy', 'historian', 'fallback')",
        schema=schema,
    )
    for column, default in (
        ("recall_route_json", "{}"),
        ("adaptive_budget_json", "{}"),
        ("evidence_guard_json", "{}"),
        ("topic_message_ids_json", "[]"),
        ("recall_candidates_json", "[]"),
    ):
        op.add_column(
            "turn_context_plans",
            sa.Column(column, sa.Text(), nullable=False, server_default=default),
            schema=schema,
        )
    op.add_column(
        "turn_context_plans",
        sa.Column("topic_id", sa.BigInteger(), nullable=True),
        schema=schema,
    )
    op.add_column(
        "turn_context_plans",
        sa.Column("topic_query", sa.Text(), nullable=False, server_default=""),
        schema=schema,
    )

    op.create_table(
        "turn_context_feedback",
        sa.Column(
            "turn_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.agent_turns.turn_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("resource_version", sa.BigInteger(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "verdict IN ('correct', 'off_topic')",
            name="ck_turn_context_feedback_verdict",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_turn_context_feedback_updated",
        "turn_context_feedback",
        ["updated_at", "turn_id"],
        schema=schema,
    )

    op.create_table(
        "message_topic_edges",
        sa.Column("edge_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("target_message_id", sa.BigInteger(), nullable=False),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column(
            "evidence_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "source_message_id <> target_message_id",
            name="ck_topic_edge_distinct_nodes",
        ),
        sa.CheckConstraint(
            "weight >= 0 AND weight <= 1",
            name="ck_topic_edge_weight",
        ),
        sa.UniqueConstraint(
            "scope_key",
            "source_message_id",
            "target_message_id",
            "relation",
            name="uq_topic_edge",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_topic_edges_source",
        "message_topic_edges",
        ["scope_key", "source_message_id", "weight"],
        schema=schema,
    )
    op.create_index(
        "idx_topic_edges_target",
        "message_topic_edges",
        ["scope_key", "target_message_id", "weight"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "idx_turn_context_feedback_updated",
        table_name="turn_context_feedback",
        schema=schema,
    )
    op.drop_table("turn_context_feedback", schema=schema)
    op.drop_index(
        "idx_topic_edges_target",
        table_name="message_topic_edges",
        schema=schema,
    )
    op.drop_index(
        "idx_topic_edges_source",
        table_name="message_topic_edges",
        schema=schema,
    )
    op.drop_table("message_topic_edges", schema=schema)
    for column in (
        "topic_query",
        "topic_id",
        "recall_candidates_json",
        "topic_message_ids_json",
        "evidence_guard_json",
        "adaptive_budget_json",
        "recall_route_json",
    ):
        op.drop_column("turn_context_plans", column, schema=schema)
    op.drop_constraint(
        "ck_context_generation_mode",
        "context_compartments",
        type_="check",
        schema=schema,
    )
    op.drop_constraint(
        "ck_context_confidence",
        "context_compartments",
        type_="check",
        schema=schema,
    )
    op.drop_constraint(
        "ck_context_importance",
        "context_compartments",
        type_="check",
        schema=schema,
    )
    for column in (
        "generation_mode",
        "evidence_ids_json",
        "participants_json",
        "confidence",
        "importance",
        "topic",
        "summary_p4",
    ):
        op.drop_column("context_compartments", column, schema=schema)


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is not a valid identifier")
    return schema
