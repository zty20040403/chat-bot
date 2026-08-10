"""Create the canonical PostgreSQL schema.

Revision ID: 0001_unified_postgres
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence
import os
import re

from alembic import op
import sqlalchemy as sa


revision = "0001_unified_postgres"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = _schema()
    bigint_id = lambda name: sa.Column(
        name,
        sa.BigInteger(),
        sa.Identity(),
        primary_key=True,
    )

    op.create_table(
        "conversations",
        bigint_id("conversation_id"),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("native_conversation_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("kind IN ('group', 'private')", name="ck_conversations_kind"),
        sa.UniqueConstraint("scope_key", name="uq_conversations_scope_key"),
        schema=schema,
    )
    op.create_table(
        "principals",
        bigint_id("principal_id"),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )
    op.create_table(
        "principal_identities",
        bigint_id("identity_id"),
        sa.Column(
            "principal_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.principals.principal_id"),
            nullable=False,
        ),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("native_user_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "platform",
            "native_user_id",
            name="uq_principal_identities_platform_native",
        ),
        schema=schema,
    )
    op.create_table(
        "messages",
        bigint_id("canonical_message_id"),
        sa.Column(
            "conversation_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.conversations.conversation_id"),
            nullable=False,
        ),
        sa.Column("native_message_id", sa.Text()),
        sa.Column(
            "sender_identity_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.principal_identities.identity_id"),
        ),
        sa.Column(
            "sender_principal_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.principals.principal_id"),
        ),
        sa.Column("sender_native_user_id", sa.Text(), nullable=False),
        sa.Column("sender_display", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column(
            "message_kind",
            sa.Text(),
            nullable=False,
            server_default="chat",
        ),
        sa.Column("body_json", sa.Text(), nullable=False),
        sa.Column("rendered_text", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.BigInteger(), nullable=False),
        sa.Column("reply_to_native_message_id", sa.Text()),
        sa.Column(
            "reply_to_canonical_message_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.messages.canonical_message_id"),
        ),
        sa.Column(
            "raw_event_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound', 'system')",
            name="ck_messages_direction",
        ),
        sa.CheckConstraint(
            "message_kind IN ('chat', 'command', 'system')",
            name="ck_messages_kind",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "native_message_id",
            name="uq_messages_conversation_native",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_messages_conversation_time",
        "messages",
        ["conversation_id", "occurred_at", "canonical_message_id"],
        schema=schema,
    )
    op.create_index(
        "idx_messages_reply_native",
        "messages",
        ["conversation_id", "reply_to_native_message_id"],
        schema=schema,
    )
    op.create_index(
        "idx_messages_sender",
        "messages",
        ["conversation_id", "sender_principal_id"],
        schema=schema,
    )
    op.create_table(
        "conversation_visibility",
        sa.Column(
            "conversation_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.conversations.conversation_id"),
            primary_key=True,
        ),
        sa.Column(
            "min_canonical_message_id",
            sa.BigInteger(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("cleared_at", sa.BigInteger()),
        schema=schema,
    )
    op.create_table(
        "embeddings",
        bigint_id("embedding_id"),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "scope_key",
            "source_type",
            "source_id",
            "model",
            "content_hash",
            name="uq_embeddings_source_model_hash",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_embeddings_scope_source",
        "embeddings",
        ["scope_key", "source_type", "source_id"],
        schema=schema,
    )

    op.create_table(
        "context_state",
        sa.Column("scope_key", sa.Text(), primary_key=True),
        sa.Column("visibility_floor", sa.BigInteger(), nullable=False),
        sa.Column("last_compacted_message_id", sa.BigInteger(), nullable=False),
        sa.Column("next_ordinal", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )
    op.create_table(
        "context_compartments",
        bigint_id("compartment_id"),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("expand_handle", sa.Text(), nullable=False),
        sa.Column("start_message_id", sa.BigInteger(), nullable=False),
        sa.Column("end_message_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_ids_json", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.Text(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("summary_p1", sa.Text(), nullable=False),
        sa.Column("summary_p2", sa.Text(), nullable=False),
        sa.Column("summary_p3", sa.Text(), nullable=False),
        sa.Column("active", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("active IN (0, 1)", name="ck_context_active"),
        sa.UniqueConstraint("expand_handle", name="uq_context_expand_handle"),
        sa.UniqueConstraint("scope_key", "ordinal", name="uq_context_scope_ordinal"),
        schema=schema,
    )
    op.create_index(
        "idx_context_compartments_scope",
        "context_compartments",
        ["scope_key", "active", "ordinal"],
        schema=schema,
    )
    op.create_index(
        "idx_context_compartments_range",
        "context_compartments",
        ["scope_key", "start_message_id", "end_message_id"],
        schema=schema,
    )
    op.create_table(
        "pinned_messages",
        sa.Column("scope_key", sa.Text(), primary_key=True),
        sa.Column("canonical_message_id", sa.BigInteger(), primary_key=True),
        sa.Column("pinned_by_principal_id", sa.BigInteger()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )
    op.create_index(
        "idx_pins_scope_created",
        "pinned_messages",
        ["scope_key", "created_at"],
        schema=schema,
    )

    op.create_table(
        "reminders",
        bigint_id("reminder_id"),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("conversation_kind", sa.Text(), nullable=False),
        sa.Column("native_conversation_id", sa.Text(), nullable=False),
        sa.Column("creator_native_user_id", sa.Text(), nullable=False),
        sa.Column("creator_principal_id", sa.BigInteger()),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("scheduled_for", sa.BigInteger(), nullable=False),
        sa.Column("next_attempt_at", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("sent_at", sa.BigInteger()),
        sa.Column("lease_until", sa.BigInteger()),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'cancelled')",
            name="ck_reminders_status",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_reminders_due",
        "reminders",
        ["status", "next_attempt_at"],
        schema=schema,
    )
    op.create_index(
        "idx_reminders_scope",
        "reminders",
        ["scope_key", "status", "scheduled_for"],
        schema=schema,
    )

    op.create_table(
        "deliveries",
        bigint_id("delivery_id"),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("source_scope_key", sa.Text(), nullable=False),
        sa.Column("source_canonical_message_id", sa.BigInteger()),
        sa.Column("turn_id", sa.BigInteger()),
        sa.Column("target_platform", sa.Text(), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_native_conversation_id", sa.Text(), nullable=False),
        sa.Column(
            "reply_to_native_message_id",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column("body_json", sa.Text(), nullable=False),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.BigInteger(), nullable=False),
        sa.Column("lease_until", sa.BigInteger()),
        sa.Column("native_message_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("target_kind IN ('group', 'private')", name="ck_deliveries_kind"),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'ambiguous', 'committed', 'failed', 'cancelled')",
            name="ck_deliveries_status",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_deliveries_idempotency_key"),
        schema=schema,
    )
    op.create_index(
        "idx_deliveries_due",
        "deliveries",
        ["status", "next_attempt_at", "delivery_id"],
        schema=schema,
    )
    op.create_index(
        "idx_deliveries_echo",
        "deliveries",
        [
            "target_platform",
            "target_kind",
            "target_native_conversation_id",
            "content_fingerprint",
            "status",
            "created_at",
        ],
        schema=schema,
    )
    op.create_table(
        "delivery_attempts",
        bigint_id("attempt_id"),
        sa.Column(
            "delivery_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.deliveries.delivery_id"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger()),
        sa.UniqueConstraint("delivery_id", "attempt", name="uq_delivery_attempt"),
        schema=schema,
    )

    op.create_table(
        "bridge_sources",
        bigint_id("source_id"),
        sa.Column("canonical_message_id", sa.BigInteger(), nullable=False),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("native_event_id", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("scope_key", "native_event_id", name="uq_bridge_source"),
        schema=schema,
    )
    op.create_index(
        "idx_bridge_sources_canonical",
        "bridge_sources",
        ["canonical_message_id"],
        schema=schema,
    )
    op.create_table(
        "bridge_deliveries",
        sa.Column(
            "delivery_id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("canonical_message_id", sa.BigInteger(), nullable=False),
        sa.Column("target_scope_key", sa.Text(), nullable=False),
        sa.Column("target_native_event_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("confirmed_at", sa.BigInteger()),
        schema=schema,
    )
    op.create_index(
        "idx_bridge_deliveries_reply",
        "bridge_deliveries",
        ["canonical_message_id", "target_scope_key", "confirmed_at"],
        schema=schema,
    )
    op.create_table(
        "bridge_cursors",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )

    op.create_table(
        "usage_events",
        bigint_id("usage_id"),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cached_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("turn_id", sa.BigInteger()),
        sa.Column("occurred_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )
    op.create_index(
        "idx_usage_scope_time",
        "usage_events",
        ["scope_key", "occurred_at"],
        schema=schema,
    )
    op.create_index(
        "idx_usage_time",
        "usage_events",
        ["occurred_at"],
        schema=schema,
    )
    op.create_table(
        "quota_overrides",
        sa.Column("scope_key", sa.Text(), primary_key=True),
        sa.Column("call_limit", sa.BigInteger(), nullable=False),
        sa.Column("input_limit", sa.BigInteger(), nullable=False),
        sa.Column("output_limit", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )
    op.create_table(
        "semantic_index_state",
        sa.Column("source_key", sa.Text(), primary_key=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("indexed_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )
    op.create_table(
        "maintenance_state",
        sa.Column("job_name", sa.Text(), primary_key=True),
        sa.Column("last_success_key", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )

    op.create_table(
        "agent_turns",
        bigint_id("turn_id"),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("turn_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("trigger_canonical_message_id", sa.BigInteger()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("profile", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("final_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger()),
        sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("prompt_version", sa.Text(), nullable=False, server_default=""),
        sa.Column("tool_catalog_version", sa.Text(), nullable=False, server_default=""),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'silence', 'aborted', 'crashed')",
            name="ck_agent_turns_status",
        ),
        sa.UniqueConstraint("scope_key", "turn_ordinal", name="uq_agent_turn_scope_ordinal"),
        schema=schema,
    )
    op.create_index(
        "idx_agent_turns_scope_time",
        "agent_turns",
        ["scope_key", "started_at", "turn_ordinal"],
        schema=schema,
    )
    op.create_table(
        "turn_journal_events",
        bigint_id("event_id"),
        sa.Column(
            "turn_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.agent_turns.turn_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("loop_sequence", sa.BigInteger(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("event_kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("input_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("result_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("effect_labels_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("occurred_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("event_kind IN ('tool', 'model_note')", name="ck_turn_event_kind"),
        schema=schema,
    )
    op.create_index(
        "idx_turn_events_turn",
        "turn_journal_events",
        ["turn_id", "event_id"],
        schema=schema,
    )
    op.create_table(
        "turn_edges",
        bigint_id("edge_id"),
        sa.Column(
            "from_turn_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.agent_turns.turn_id"),
            nullable=False,
        ),
        sa.Column(
            "to_turn_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.agent_turns.turn_id"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("created_by_principal_id", sa.BigInteger()),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("kind IN ('fork-from')", name="ck_turn_edges_kind"),
        sa.UniqueConstraint("from_turn_id", "to_turn_id", "kind", name="uq_turn_edge"),
        schema=schema,
    )
    op.create_index(
        "idx_turn_edges_from",
        "turn_edges",
        ["from_turn_id", "kind"],
        schema=schema,
    )
    op.create_table(
        "turn_send_links",
        sa.Column(
            "canonical_message_id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column(
            "turn_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.agent_turns.turn_id"),
            nullable=False,
        ),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )
    op.create_index(
        "idx_turn_send_links_turn",
        "turn_send_links",
        ["turn_id", "chunk_index"],
        schema=schema,
    )
    op.create_table(
        "turn_archives",
        sa.Column(
            "turn_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.agent_turns.turn_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("byte_count", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )
    op.create_index(
        "idx_turn_archives_expiry",
        "turn_archives",
        ["expires_at"],
        schema=schema,
    )
    op.create_table(
        "turn_visibility",
        sa.Column("scope_key", sa.Text(), primary_key=True),
        sa.Column("min_turn_ordinal", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("cleared_at", sa.BigInteger()),
        schema=schema,
    )
    op.create_table(
        "turn_digests",
        sa.Column(
            "turn_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{schema}.agent_turns.turn_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("skeleton", sa.Text(), nullable=False),
        sa.Column("approach", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        sa.text(
            f'''CREATE TABLE "{schema}".semantic_documents (
                document_id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                scope_key TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_handle TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                embedding_model TEXT NOT NULL,
                embedding vector(1536) NOT NULL,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                CONSTRAINT uq_semantic_document_source
                    UNIQUE(scope_key, source_type, source_handle)
            )'''
        )
    )
    op.create_index(
        "idx_semantic_scope_type",
        "semantic_documents",
        ["scope_key", "source_type", "updated_at"],
        schema=schema,
    )
    op.execute(
        sa.text(
            f'''CREATE INDEX idx_semantic_embedding_hnsw
                ON "{schema}".semantic_documents
                USING hnsw (embedding vector_cosine_ops)'''
        )
    )

    op.create_table(
        "state_blobs",
        sa.Column("namespace", sa.Text(), primary_key=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        schema=schema,
    )
    op.create_table(
        "storage_migration_runs",
        sa.Column("migration_id", sa.Text(), primary_key=True),
        sa.Column("source_fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger()),
        sa.CheckConstraint(
            "status IN ('running', 'verified', 'failed')",
            name="ck_storage_migration_status",
        ),
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    tables: Sequence[str] = (
        "storage_migration_runs",
        "state_blobs",
        "semantic_documents",
        "turn_digests",
        "turn_visibility",
        "turn_archives",
        "turn_send_links",
        "turn_edges",
        "turn_journal_events",
        "agent_turns",
        "maintenance_state",
        "semantic_index_state",
        "quota_overrides",
        "usage_events",
        "bridge_cursors",
        "bridge_deliveries",
        "bridge_sources",
        "delivery_attempts",
        "deliveries",
        "reminders",
        "pinned_messages",
        "context_compartments",
        "context_state",
        "embeddings",
        "conversation_visibility",
        "messages",
        "principal_identities",
        "principals",
        "conversations",
    )
    for table in tables:
        op.drop_table(table, schema=schema)


def _schema() -> str:
    schema = os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("AI_POSTGRES_SCHEMA is not a valid identifier")
    return schema
