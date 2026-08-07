from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Literal

from .conversation_scope import ConversationScope
from .message_ir import (
    MessageBody,
    body_from_json,
    body_to_json,
    canonicalize_for_storage,
    render_prompt_text,
    resolve_mentions,
)


Direction = Literal["inbound", "outbound", "system"]
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CanonicalMessage:
    canonical_message_id: int
    scope_key: str
    native_message_id: str
    sender_native_user_id: str
    sender_principal_id: int | None
    sender_display: str
    direction: str
    body: MessageBody
    rendered_text: str
    occurred_at: int
    reply_to_native_message_id: str | None
    reply_to_canonical_message_id: int | None


class MessageLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path) if str(path) != ":memory:" else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(path),
            timeout=10.0,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._configure()
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def record_message(
        self,
        scope: ConversationScope,
        *,
        native_message_id: str,
        sender_native_user_id: str,
        sender_display: str,
        body: MessageBody,
        occurred_at: int = 0,
        direction: Direction = "inbound",
        reply_to_native_message_id: str | None = None,
        raw_event: dict[str, Any] | None = None,
    ) -> CanonicalMessage:
        if direction not in {"inbound", "outbound", "system"}:
            raise ValueError("unsupported message direction")
        native_message_id = str(native_message_id).strip()
        sender_native_user_id = str(sender_native_user_id).strip()
        sender_display = sender_display.strip() or (
            "群成员" if sender_native_user_id else "未知用户"
        )
        occurred_at = int(occurred_at or time.time())
        reply_to_native_message_id = (
            str(reply_to_native_message_id).strip()
            if reply_to_native_message_id
            else None
        )

        with self._transaction() as cursor:
            conversation_id = self._ensure_conversation(cursor, scope)
            identity_id: int | None = None
            principal_id: int | None = None
            if sender_native_user_id:
                identity_id, principal_id = self._ensure_identity(
                    cursor,
                    scope.platform,
                    sender_native_user_id,
                    sender_display,
                    occurred_at,
                )

            resolved_body = resolve_mentions(
                canonicalize_for_storage(body),
                lambda native_id, display: self._resolve_mention(
                    cursor,
                    scope.platform,
                    native_id,
                    display,
                    occurred_at,
                ),
            )
            reply_to_canonical_id = self._canonical_for_native(
                cursor,
                conversation_id,
                reply_to_native_message_id,
            )
            existing = None
            if native_message_id:
                existing = cursor.execute(
                    """
                    SELECT canonical_message_id
                    FROM messages
                    WHERE conversation_id = ? AND native_message_id = ?
                    """,
                    (conversation_id, native_message_id),
                ).fetchone()

            if existing is None:
                cursor.execute(
                    """
                    INSERT INTO messages (
                        conversation_id,
                        native_message_id,
                        sender_identity_id,
                        sender_principal_id,
                        sender_native_user_id,
                        sender_display,
                        direction,
                        body_json,
                        rendered_text,
                        occurred_at,
                        reply_to_native_message_id,
                        reply_to_canonical_message_id,
                        raw_event_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        native_message_id or None,
                        identity_id,
                        principal_id,
                        sender_native_user_id,
                        sender_display,
                        direction,
                        body_to_json(resolved_body),
                        occurred_at,
                        reply_to_native_message_id,
                        reply_to_canonical_id,
                        _json_dump(raw_event or {}),
                    ),
                )
                canonical_message_id = int(cursor.lastrowid)
                rendered_text = render_prompt_text(
                    resolved_body,
                    canonical_message_id,
                )
                cursor.execute(
                    """
                    UPDATE messages
                    SET rendered_text = ?
                    WHERE canonical_message_id = ?
                    """,
                    (rendered_text, canonical_message_id),
                )
            else:
                canonical_message_id = int(existing["canonical_message_id"])
            if native_message_id:
                cursor.execute(
                    """
                    UPDATE messages
                    SET reply_to_canonical_message_id = ?
                    WHERE conversation_id = ?
                      AND reply_to_native_message_id = ?
                    """,
                    (
                        canonical_message_id,
                        conversation_id,
                        native_message_id,
                    ),
                )
            cursor.execute(
                """
                UPDATE conversations SET updated_at = ?
                WHERE conversation_id = ?
                """,
                (int(time.time()), conversation_id),
            )
            row = self._message_row(cursor, canonical_message_id)

        if row is None:
            raise RuntimeError("message was not stored")
        return self._row_to_message(row)

    def get_in_scope(
        self,
        scope: ConversationScope,
        canonical_message_id: int,
    ) -> CanonicalMessage | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT m.*, c.scope_key
                FROM messages AS m
                JOIN conversations AS c
                  ON c.conversation_id = m.conversation_id
                JOIN conversation_visibility AS v
                  ON v.conversation_id = m.conversation_id
                WHERE c.scope_key = ?
                  AND m.canonical_message_id = ?
                  AND m.canonical_message_id >= v.min_canonical_message_id
                """,
                (scope.key, int(canonical_message_id)),
            ).fetchone()
        return self._row_to_message(row) if row is not None else None

    def canonical_id_for_native(
        self,
        scope: ConversationScope,
        native_message_id: str | int,
    ) -> int | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT m.canonical_message_id
                FROM messages AS m
                JOIN conversations AS c
                  ON c.conversation_id = m.conversation_id
                JOIN conversation_visibility AS v
                  ON v.conversation_id = m.conversation_id
                WHERE c.scope_key = ?
                  AND m.native_message_id = ?
                  AND m.canonical_message_id >= v.min_canonical_message_id
                """,
                (scope.key, str(native_message_id)),
            ).fetchone()
        return int(row[0]) if row is not None else None

    def recent_in_scope(
        self,
        scope: ConversationScope,
        limit: int = 40,
    ) -> list[CanonicalMessage]:
        limit = min(max(int(limit), 1), 500)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM (
                    SELECT m.*, c.scope_key
                    FROM messages AS m
                    JOIN conversations AS c
                      ON c.conversation_id = m.conversation_id
                    JOIN conversation_visibility AS v
                      ON v.conversation_id = m.conversation_id
                    WHERE c.scope_key = ?
                      AND m.canonical_message_id >= v.min_canonical_message_id
                    ORDER BY m.occurred_at DESC, m.canonical_message_id DESC
                    LIMIT ?
                ) ORDER BY occurred_at ASC, canonical_message_id ASC
                """,
                (scope.key, limit),
            ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def visible_messages_after(
        self,
        scope: ConversationScope,
        canonical_message_id: int = 0,
        *,
        limit: int = 500,
    ) -> list[CanonicalMessage]:
        """Read visible source messages in immutable ingest order."""
        limit = min(max(int(limit), 1), 5000)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT m.*, c.scope_key
                FROM messages AS m
                JOIN conversations AS c
                  ON c.conversation_id = m.conversation_id
                JOIN conversation_visibility AS v
                  ON v.conversation_id = m.conversation_id
                WHERE c.scope_key = ?
                  AND m.canonical_message_id > ?
                  AND m.canonical_message_id >= v.min_canonical_message_id
                ORDER BY m.canonical_message_id ASC
                LIMIT ?
                """,
                (scope.key, max(int(canonical_message_id), 0), limit),
            ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def visible_message_count_after(
        self,
        scope: ConversationScope,
        canonical_message_id: int = 0,
    ) -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*)
                FROM messages AS m
                JOIN conversations AS c
                  ON c.conversation_id = m.conversation_id
                JOIN conversation_visibility AS v
                  ON v.conversation_id = m.conversation_id
                WHERE c.scope_key = ?
                  AND m.canonical_message_id > ?
                  AND m.canonical_message_id >= v.min_canonical_message_id
                """,
                (scope.key, max(int(canonical_message_id), 0)),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def visible_message_floor(self, scope: ConversationScope) -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT v.min_canonical_message_id
                FROM conversations AS c
                JOIN conversation_visibility AS v
                  ON v.conversation_id = c.conversation_id
                WHERE c.scope_key = ?
                """,
                (scope.key,),
            ).fetchone()
        return int(row[0]) if row is not None else 1

    def visible_messages_by_ids(
        self,
        scope: ConversationScope,
        canonical_message_ids: list[int] | tuple[int, ...],
    ) -> list[CanonicalMessage]:
        ids = [int(message_id) for message_id in canonical_message_ids]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT m.*, c.scope_key
                FROM messages AS m
                JOIN conversations AS c
                  ON c.conversation_id = m.conversation_id
                JOIN conversation_visibility AS v
                  ON v.conversation_id = m.conversation_id
                WHERE c.scope_key = ?
                  AND m.canonical_message_id IN ({placeholders})
                  AND m.canonical_message_id >= v.min_canonical_message_id
                ORDER BY m.canonical_message_id ASC
                """,
                [scope.key, *ids],
            ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def search_in_scope(
        self,
        scope: ConversationScope,
        query: str,
        limit: int = 10,
    ) -> list[CanonicalMessage]:
        query = query.strip()
        if not query:
            return []
        limit = min(max(int(limit), 1), 100)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT m.*, c.scope_key
                FROM messages AS m
                JOIN conversations AS c
                  ON c.conversation_id = m.conversation_id
                JOIN conversation_visibility AS v
                  ON v.conversation_id = m.conversation_id
                WHERE c.scope_key = ?
                  AND m.canonical_message_id >= v.min_canonical_message_id
                ORDER BY m.occurred_at DESC, m.canonical_message_id DESC
                LIMIT 2000
                """,
                (scope.key,),
            ).fetchall()
        folded_query = query.casefold()
        matches = [
            self._row_to_message(row)
            for row in rows
            if folded_query in str(row["rendered_text"]).casefold()
        ]
        return matches[:limit]

    def render_recent(
        self,
        scope: ConversationScope,
        *,
        max_messages: int = 40,
        max_chars: int = 4000,
        exclude_native_message_id: str | int | None = None,
        exclude_canonical_message_ids: tuple[int, ...] = (),
    ) -> str:
        messages = self.recent_in_scope(scope, max_messages)
        excluded_canonical_ids = set(
            int(item) for item in exclude_canonical_message_ids
        )
        if excluded_canonical_ids:
            messages = [
                message
                for message in messages
                if message.canonical_message_id not in excluded_canonical_ids
            ]
        if exclude_native_message_id is not None:
            excluded = str(exclude_native_message_id)
            messages = [
                message
                for message in messages
                if message.native_message_id != excluded
            ]
        lines: list[str] = []
        used_chars = 0
        for message in reversed(messages):
            if not message.rendered_text:
                continue
            sender = (
                f"@#{message.sender_principal_id} {message.sender_display}"
                if message.sender_principal_id is not None
                else message.sender_display
            )
            reply = (
                f" reply:msg#{message.reply_to_canonical_message_id}"
                if message.reply_to_canonical_message_id is not None
                else ""
            )
            timestamp = datetime.fromtimestamp(message.occurred_at).strftime(
                "%m-%d %H:%M"
            )
            line = (
                f"[msg#{message.canonical_message_id}{reply} | {timestamp} | "
                f"{sender}] {message.rendered_text}"
            )
            if lines and used_chars + len(line) + 1 > max_chars:
                break
            if not lines and len(line) > max_chars:
                line = line[:max_chars]
            lines.append(line)
            used_chars += len(line) + 1
        return "\n".join(reversed(lines))

    def render_roster(
        self,
        scope: ConversationScope,
        limit: int = 40,
    ) -> str:
        limit = min(max(int(limit), 1), 100)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT
                    m.sender_principal_id,
                    m.sender_display,
                    MAX(m.canonical_message_id) AS last_message_id
                FROM messages AS m
                JOIN conversations AS c
                  ON c.conversation_id = m.conversation_id
                JOIN conversation_visibility AS v
                  ON v.conversation_id = m.conversation_id
                WHERE c.scope_key = ?
                  AND m.canonical_message_id >= v.min_canonical_message_id
                  AND m.sender_principal_id IS NOT NULL
                GROUP BY m.sender_principal_id
                ORDER BY last_message_id DESC
                LIMIT ?
                """,
                (scope.key, limit),
            ).fetchall()
        return "\n".join(
            f"@#{int(row['sender_principal_id'])}: {row['sender_display']}"
            for row in rows
        )

    def principal_label_for_native(
        self,
        platform: str,
        native_user_id: str | int,
    ) -> str | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT p.principal_id, p.display_name
                FROM principal_identities AS i
                JOIN principals AS p ON p.principal_id = i.principal_id
                WHERE i.platform = ? AND i.native_user_id = ?
                """,
                (platform, str(native_user_id)),
            ).fetchone()
        if row is None:
            return None
        return f"@#{int(row['principal_id'])} {row['display_name']}"

    def principal_id_for_native(
        self,
        platform: str,
        native_user_id: str | int,
    ) -> int | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT principal_id FROM principal_identities
                WHERE platform = ? AND native_user_id = ?
                """,
                (platform, str(native_user_id)),
            ).fetchone()
        return int(row[0]) if row is not None else None

    def activity_since(
        self,
        scope: ConversationScope,
        since_timestamp: int,
        *,
        limit: int = 3,
        exclude_native_message_id: str | int | None = None,
    ) -> tuple[int, list[CanonicalMessage]]:
        limit = min(max(int(limit), 1), 20)
        parameters: list[Any] = [scope.key, int(since_timestamp)]
        exclusion = ""
        if exclude_native_message_id is not None:
            exclusion = "AND m.native_message_id != ?"
            parameters.append(str(exclude_native_message_id))
        with self._lock:
            count_row = self._connection.execute(
                f"""
                SELECT COUNT(*)
                FROM messages AS m
                JOIN conversations AS c
                  ON c.conversation_id = m.conversation_id
                JOIN conversation_visibility AS v
                  ON v.conversation_id = m.conversation_id
                WHERE c.scope_key = ?
                  AND m.occurred_at > ?
                  AND m.canonical_message_id >= v.min_canonical_message_id
                  AND m.direction = 'inbound'
                  {exclusion}
                """,
                parameters,
            ).fetchone()
            rows = self._connection.execute(
                f"""
                SELECT * FROM (
                    SELECT m.*, c.scope_key
                    FROM messages AS m
                    JOIN conversations AS c
                      ON c.conversation_id = m.conversation_id
                    JOIN conversation_visibility AS v
                      ON v.conversation_id = m.conversation_id
                    WHERE c.scope_key = ?
                      AND m.occurred_at > ?
                      AND m.canonical_message_id >= v.min_canonical_message_id
                      AND m.direction = 'inbound'
                      {exclusion}
                    ORDER BY m.occurred_at DESC, m.canonical_message_id DESC
                    LIMIT ?
                ) ORDER BY occurred_at ASC, canonical_message_id ASC
                """,
                [*parameters, limit],
            ).fetchall()
        count = int(count_row[0]) if count_row is not None else 0
        return count, [self._row_to_message(row) for row in rows]

    def hide_history(self, scope: ConversationScope) -> int:
        with self._transaction() as cursor:
            conversation = cursor.execute(
                """
                SELECT conversation_id FROM conversations WHERE scope_key = ?
                """,
                (scope.key,),
            ).fetchone()
            if conversation is None:
                return 0
            conversation_id = int(conversation["conversation_id"])
            visibility = cursor.execute(
                """
                SELECT min_canonical_message_id
                FROM conversation_visibility
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            minimum = int(visibility[0]) if visibility is not None else 1
            aggregate = cursor.execute(
                """
                SELECT COUNT(*) AS count, MAX(canonical_message_id) AS maximum
                FROM messages
                WHERE conversation_id = ? AND canonical_message_id >= ?
                """,
                (conversation_id, minimum),
            ).fetchone()
            count = int(aggregate["count"] or 0)
            maximum = int(aggregate["maximum"] or (minimum - 1))
            cursor.execute(
                """
                INSERT INTO conversation_visibility (
                    conversation_id, min_canonical_message_id, cleared_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    min_canonical_message_id = excluded.min_canonical_message_id,
                    cleared_at = excluded.cleared_at
                """,
                (conversation_id, maximum + 1, int(time.time())),
            )
            return count

    def _configure(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 10000")
            if self.path is not None:
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")

    def _migrate(self) -> None:
        with self._transaction() as cursor:
            cursor.executescript(
                """
                CREATE TABLE IF NOT EXISTS ledger_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_key TEXT NOT NULL UNIQUE,
                    platform TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('group', 'private')),
                    native_conversation_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS principals (
                    principal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    display_name TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS principal_identities (
                    identity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    principal_id INTEGER NOT NULL REFERENCES principals(principal_id),
                    platform TEXT NOT NULL,
                    native_user_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    UNIQUE(platform, native_user_id)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    canonical_message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL REFERENCES conversations(conversation_id),
                    native_message_id TEXT,
                    sender_identity_id INTEGER REFERENCES principal_identities(identity_id),
                    sender_principal_id INTEGER REFERENCES principals(principal_id),
                    sender_native_user_id TEXT NOT NULL,
                    sender_display TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('inbound', 'outbound', 'system')),
                    body_json TEXT NOT NULL,
                    rendered_text TEXT NOT NULL,
                    occurred_at INTEGER NOT NULL,
                    reply_to_native_message_id TEXT,
                    reply_to_canonical_message_id INTEGER REFERENCES messages(canonical_message_id),
                    raw_event_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(conversation_id, native_message_id)
                );

                CREATE TABLE IF NOT EXISTS conversation_visibility (
                    conversation_id INTEGER PRIMARY KEY REFERENCES conversations(conversation_id),
                    min_canonical_message_id INTEGER NOT NULL DEFAULT 1,
                    cleared_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS embeddings (
                    embedding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_key TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    vector BLOB NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    UNIQUE(scope_key, source_type, source_id, model, content_hash)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conversation_time
                    ON messages(conversation_id, occurred_at, canonical_message_id);
                CREATE INDEX IF NOT EXISTS idx_messages_reply_native
                    ON messages(conversation_id, reply_to_native_message_id);
                CREATE INDEX IF NOT EXISTS idx_messages_sender
                    ON messages(conversation_id, sender_principal_id);
                CREATE INDEX IF NOT EXISTS idx_embeddings_scope_source
                    ON embeddings(scope_key, source_type, source_id);
                """
            )
            row = cursor.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is not None and int(row[0]) > SCHEMA_VERSION:
                raise RuntimeError("message ledger schema is newer than this bot")
            cursor.execute(
                """
                INSERT INTO ledger_meta(key, value) VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                yield cursor
            except Exception:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()
            finally:
                cursor.close()

    def _ensure_conversation(
        self,
        cursor: sqlite3.Cursor,
        scope: ConversationScope,
    ) -> int:
        row = cursor.execute(
            "SELECT conversation_id FROM conversations WHERE scope_key = ?",
            (scope.key,),
        ).fetchone()
        if row is not None:
            return int(row["conversation_id"])
        now = int(time.time())
        cursor.execute(
            """
            INSERT INTO conversations (
                scope_key, platform, kind, native_conversation_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                scope.key,
                scope.platform,
                scope.kind,
                scope.native_conversation_id,
                now,
                now,
            ),
        )
        conversation_id = int(cursor.lastrowid)
        cursor.execute(
            """
            INSERT INTO conversation_visibility (
                conversation_id, min_canonical_message_id
            ) VALUES (?, 1)
            """,
            (conversation_id,),
        )
        return conversation_id

    def _ensure_identity(
        self,
        cursor: sqlite3.Cursor,
        platform: str,
        native_user_id: str,
        display: str,
        seen_at: int,
    ) -> tuple[int, int]:
        display = display.strip()
        row = cursor.execute(
            """
            SELECT identity_id, principal_id
            FROM principal_identities
            WHERE platform = ? AND native_user_id = ?
            """,
            (platform, native_user_id),
        ).fetchone()
        if row is None:
            stored_display = display or "群成员"
            cursor.execute(
                """
                INSERT INTO principals(display_name, created_at, updated_at)
                VALUES (?, ?, ?)
                """,
                (stored_display, seen_at, seen_at),
            )
            principal_id = int(cursor.lastrowid)
            cursor.execute(
                """
                INSERT INTO principal_identities (
                    principal_id, platform, native_user_id,
                    display_name, last_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    principal_id,
                    platform,
                    native_user_id,
                    stored_display,
                    seen_at,
                ),
            )
            return int(cursor.lastrowid), principal_id

        identity_id = int(row["identity_id"])
        principal_id = int(row["principal_id"])
        if display:
            cursor.execute(
                """
                UPDATE principal_identities
                SET display_name = ?, last_seen_at = ?
                WHERE identity_id = ?
                """,
                (display, seen_at, identity_id),
            )
            cursor.execute(
                """
                UPDATE principals SET display_name = ?, updated_at = ?
                WHERE principal_id = ?
                """,
                (display, seen_at, principal_id),
            )
        else:
            cursor.execute(
                """
                UPDATE principal_identities SET last_seen_at = ?
                WHERE identity_id = ?
                """,
                (seen_at, identity_id),
            )
        return identity_id, principal_id

    def _resolve_mention(
        self,
        cursor: sqlite3.Cursor,
        platform: str,
        native_user_id: str,
        display: str,
        seen_at: int,
    ) -> int | None:
        if not native_user_id or native_user_id == "all":
            return None
        _, principal_id = self._ensure_identity(
            cursor,
            platform,
            native_user_id,
            display,
            seen_at,
        )
        return principal_id

    @staticmethod
    def _canonical_for_native(
        cursor: sqlite3.Cursor,
        conversation_id: int,
        native_message_id: str | None,
    ) -> int | None:
        if not native_message_id:
            return None
        row = cursor.execute(
            """
            SELECT canonical_message_id FROM messages
            WHERE conversation_id = ? AND native_message_id = ?
            """,
            (conversation_id, native_message_id),
        ).fetchone()
        return int(row[0]) if row is not None else None

    @staticmethod
    def _message_row(
        cursor: sqlite3.Cursor,
        canonical_message_id: int,
    ) -> sqlite3.Row | None:
        return cursor.execute(
            """
            SELECT m.*, c.scope_key
            FROM messages AS m
            JOIN conversations AS c
              ON c.conversation_id = m.conversation_id
            WHERE m.canonical_message_id = ?
            """,
            (canonical_message_id,),
        ).fetchone()

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> CanonicalMessage:
        canonical_message_id = int(row["canonical_message_id"])
        body = body_from_json(str(row["body_json"]))
        return CanonicalMessage(
            canonical_message_id=canonical_message_id,
            scope_key=str(row["scope_key"]),
            native_message_id=str(row["native_message_id"] or ""),
            sender_native_user_id=str(row["sender_native_user_id"] or ""),
            sender_principal_id=(
                int(row["sender_principal_id"])
                if row["sender_principal_id"] is not None
                else None
            ),
            sender_display=str(row["sender_display"] or ""),
            direction=str(row["direction"]),
            body=body,
            # rendered_text is a rebuildable search cache. The canonical IR is
            # the runtime authority for every prompt projection.
            rendered_text=render_prompt_text(body, canonical_message_id),
            occurred_at=int(row["occurred_at"]),
            reply_to_native_message_id=(
                str(row["reply_to_native_message_id"])
                if row["reply_to_native_message_id"] is not None
                else None
            ),
            reply_to_canonical_message_id=(
                int(row["reply_to_canonical_message_id"])
                if row["reply_to_canonical_message_id"] is not None
                else None
            ),
        )


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
