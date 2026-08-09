from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, Request

from .conversation_scope import ConversationScope
from .delivery import Delivery, DeliveryStore
from .ledger import MessageLedger
from .message_ir import (
    MediaNode,
    MentionNode,
    MessageBody,
    TextNode,
    render_fallback_text,
)
from .message_lowering import OutboundCapabilities, lower_message


MATRIX_CAPABILITIES = OutboundCapabilities(
    text=True,
    mention="native",
    image="text",
    sticker="text",
    video="text",
    audio="text",
    file="text",
    max_text_bytes=65536,
)

IMESSAGE_CAPABILITIES = OutboundCapabilities(
    text=True,
    mention="text",
    image="text",
    sticker="text",
    video="text",
    audio="text",
    file="text",
    max_text_bytes=20000,
)


class BridgeError(RuntimeError):
    pass


class BridgeRetryableError(BridgeError):
    """The platform proved that no final outcome is known, but retry is safe."""


class BridgeOutcomeUnknown(BridgeError):
    """The request may have taken effect and must not be retried blindly."""


class BridgePermanentError(BridgeError):
    """The remote platform rejected the request permanently."""


@dataclass(frozen=True)
class BridgeEndpoint:
    platform: str
    kind: str
    conversation_id: str
    bot_user_id: str = ""
    canonical: bool = False

    @property
    def scope(self) -> ConversationScope:
        return ConversationScope(
            self.platform,
            self.kind,  # type: ignore[arg-type]
            self.conversation_id,
            bot_native_user_id=self.bot_user_id,
        )

    @property
    def key(self) -> str:
        return self.scope.key


@dataclass(frozen=True)
class MirrorBundle:
    name: str
    endpoints: tuple[BridgeEndpoint, ...]

    @property
    def canonical_endpoint(self) -> BridgeEndpoint:
        onebot = [
            endpoint
            for endpoint in self.endpoints
            if endpoint.platform == "onebot-v11"
        ]
        if onebot:
            return onebot[0]
        explicit = [endpoint for endpoint in self.endpoints if endpoint.canonical]
        return explicit[0] if explicit else self.endpoints[0]


class MirrorRouter:
    def __init__(self, bundles: tuple[MirrorBundle, ...] = ()) -> None:
        self.bundles = bundles
        self._by_scope: dict[str, MirrorBundle] = {}
        for bundle in bundles:
            for endpoint in bundle.endpoints:
                if endpoint.key in self._by_scope:
                    raise ValueError(
                        f"mirror endpoint appears in multiple bundles: {endpoint.key}"
                    )
                self._by_scope[endpoint.key] = bundle

    @classmethod
    def from_json(cls, raw: str) -> "MirrorRouter":
        if not raw.strip():
            return cls()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"AI_MIRROR_ROUTES_JSON is invalid JSON: {exc}") from exc
        if not isinstance(payload, list):
            raise ValueError("AI_MIRROR_ROUTES_JSON must be a JSON array")
        bundles: list[MirrorBundle] = []
        names: set[str] = set()
        for bundle_index, raw_bundle in enumerate(payload):
            if not isinstance(raw_bundle, dict):
                raise ValueError("every mirror bundle must be an object")
            name = str(raw_bundle.get("name") or f"bundle-{bundle_index + 1}").strip()
            if not name or name in names:
                raise ValueError(f"duplicate or empty mirror bundle name: {name!r}")
            names.add(name)
            raw_endpoints = raw_bundle.get("endpoints")
            if not isinstance(raw_endpoints, list) or len(raw_endpoints) < 2:
                raise ValueError(f"mirror bundle {name!r} needs at least two endpoints")
            endpoints: list[BridgeEndpoint] = []
            canonical_count = 0
            for raw_endpoint in raw_endpoints:
                if not isinstance(raw_endpoint, dict):
                    raise ValueError(f"mirror bundle {name!r} has an invalid endpoint")
                platform = str(raw_endpoint.get("platform") or "").strip().lower()
                kind = str(raw_endpoint.get("kind") or "group").strip().lower()
                conversation_id = str(
                    raw_endpoint.get("id")
                    or raw_endpoint.get("conversation_id")
                    or ""
                ).strip()
                if platform not in {"onebot-v11", "matrix", "imessage"}:
                    raise ValueError(f"unsupported mirror platform: {platform!r}")
                if kind not in {"group", "private"}:
                    raise ValueError(f"unsupported conversation kind: {kind!r}")
                if not conversation_id:
                    raise ValueError(f"mirror endpoint in {name!r} has no id")
                canonical = bool(raw_endpoint.get("canonical", False))
                canonical_count += int(canonical)
                endpoints.append(
                    BridgeEndpoint(
                        platform=platform,
                        kind=kind,
                        conversation_id=conversation_id,
                        bot_user_id=str(raw_endpoint.get("bot_user_id") or "").strip(),
                        canonical=canonical,
                    )
                )
            if canonical_count > 1:
                raise ValueError(f"mirror bundle {name!r} has multiple canonical endpoints")
            onebot_endpoints = [
                endpoint
                for endpoint in endpoints
                if endpoint.platform == "onebot-v11"
            ]
            explicit = [endpoint for endpoint in endpoints if endpoint.canonical]
            if (
                onebot_endpoints
                and explicit
                and explicit[0].platform != "onebot-v11"
            ):
                raise ValueError(
                    f"mirror bundle {name!r} must keep OneBot as canonical"
                )
            bundles.append(MirrorBundle(name, tuple(endpoints)))
        return cls(tuple(bundles))

    def bundle_for(self, scope: ConversationScope) -> MirrorBundle | None:
        return self._by_scope.get(scope.key)

    def targets(self, scope: ConversationScope) -> tuple[BridgeEndpoint, ...]:
        bundle = self.bundle_for(scope)
        if bundle is None:
            return ()
        return tuple(endpoint for endpoint in bundle.endpoints if endpoint.key != scope.key)

    def canonical_scope(self, scope: ConversationScope) -> ConversationScope:
        bundle = self.bundle_for(scope)
        return bundle.canonical_endpoint.scope if bundle is not None else scope

    def endpoints(self, platform: str = "") -> tuple[BridgeEndpoint, ...]:
        return tuple(
            endpoint
            for bundle in self.bundles
            for endpoint in bundle.endpoints
            if not platform or endpoint.platform == platform
        )

    def describe(self) -> list[dict[str, object]]:
        return [
            {
                "name": bundle.name,
                "canonical_scope": bundle.canonical_endpoint.key,
                "endpoints": [endpoint.key for endpoint in bundle.endpoints],
            }
            for bundle in self.bundles
        ]


@dataclass(frozen=True)
class BridgeEvent:
    scope: ConversationScope
    native_event_id: str
    sender_native_user_id: str
    sender_display: str
    body: MessageBody
    occurred_at: int
    reply_to_native_message_id: str | None = None
    is_from_bot: bool = False
    message_kind: str = "chat"
    raw_event: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class BridgeIngestResult:
    status: str
    canonical_message_id: int | None = None
    deliveries_created: int = 0


class MirrorStateStore:
    """Persistent transport evidence for bridge dedupe and native reply mapping."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path) if str(path) != ":memory:" else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(path), timeout=10.0, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 10000")
            if self.path is not None:
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def source_seen(self, scope: ConversationScope, native_event_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM bridge_sources
                WHERE scope_key = ? AND native_event_id = ?
                """,
                (scope.key, str(native_event_id)),
            ).fetchone()
        return row is not None

    def register_source(
        self,
        canonical_message_id: int,
        scope: ConversationScope,
        native_event_id: str,
        *,
        occurred_at: int,
    ) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO bridge_sources (
                    canonical_message_id, scope_key, native_event_id, occurred_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(scope_key, native_event_id) DO UPDATE SET
                    canonical_message_id = excluded.canonical_message_id
                """,
                (
                    int(canonical_message_id),
                    scope.key,
                    str(native_event_id),
                    int(occurred_at),
                ),
            )

    def register_delivery(
        self,
        delivery_id: int,
        canonical_message_id: int,
        target_scope: ConversationScope,
    ) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO bridge_deliveries (
                    delivery_id, canonical_message_id, target_scope_key,
                    target_native_event_id, confirmed_at
                ) VALUES (?, ?, ?, '', NULL)
                ON CONFLICT(delivery_id) DO NOTHING
                """,
                (int(delivery_id), int(canonical_message_id), target_scope.key),
            )

    def confirm_delivery(
        self,
        delivery_id: int,
        native_event_id: str,
        *,
        confirmed_at: int | None = None,
    ) -> None:
        timestamp = int(time.time() if confirmed_at is None else confirmed_at)
        with self._transaction() as cursor:
            cursor.execute(
                """
                UPDATE bridge_deliveries
                SET target_native_event_id = ?, confirmed_at = ?
                WHERE delivery_id = ?
                """,
                (str(native_event_id), timestamp, int(delivery_id)),
            )

    def is_mirror_delivery(self, delivery_id: int) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM bridge_deliveries WHERE delivery_id = ?",
                (int(delivery_id),),
            ).fetchone()
        return row is not None

    def resolve_reply(
        self,
        source_scope: ConversationScope,
        source_native_event_id: str | None,
        target_scope: ConversationScope,
    ) -> str:
        if not source_native_event_id:
            return ""
        with self._lock:
            row = self._connection.execute(
                """
                WITH parent(canonical_message_id) AS (
                    SELECT canonical_message_id FROM bridge_sources
                    WHERE scope_key = ? AND native_event_id = ?
                    UNION
                    SELECT canonical_message_id FROM bridge_deliveries
                    WHERE target_scope_key = ? AND target_native_event_id = ?
                ), target(native_event_id, confirmed_at) AS (
                    SELECT s.native_event_id, s.occurred_at
                    FROM bridge_sources AS s JOIN parent AS p
                      ON p.canonical_message_id = s.canonical_message_id
                    WHERE s.scope_key = ?
                    UNION ALL
                    SELECT d.target_native_event_id, d.confirmed_at
                    FROM bridge_deliveries AS d JOIN parent AS p
                      ON p.canonical_message_id = d.canonical_message_id
                    WHERE d.target_scope_key = ?
                      AND d.target_native_event_id != ''
                )
                SELECT native_event_id FROM target
                ORDER BY confirmed_at DESC LIMIT 1
                """,
                (
                    source_scope.key,
                    str(source_native_event_id),
                    source_scope.key,
                    str(source_native_event_id),
                    target_scope.key,
                    target_scope.key,
                ),
            ).fetchone()
        return str(row["native_event_id"]) if row is not None else ""

    def get_cursor(self, key: str) -> str:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM bridge_cursors WHERE key = ?",
                (str(key),),
            ).fetchone()
        return str(row["value"]) if row is not None else ""

    def set_cursor(self, key: str, value: str) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO bridge_cursors (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (str(key), str(value), int(time.time())),
            )

    def stats(self) -> dict[str, int]:
        with self._lock:
            sources = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM bridge_sources"
                ).fetchone()[0]
            )
            copies = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM bridge_deliveries WHERE confirmed_at IS NOT NULL"
                ).fetchone()[0]
            )
        return {"source_events": sources, "confirmed_copies": copies}

    def _migrate(self) -> None:
        with self._transaction() as cursor:
            cursor.executescript(
                """
                CREATE TABLE IF NOT EXISTS bridge_sources (
                    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical_message_id INTEGER NOT NULL,
                    scope_key TEXT NOT NULL,
                    native_event_id TEXT NOT NULL,
                    occurred_at INTEGER NOT NULL,
                    UNIQUE(scope_key, native_event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_bridge_sources_canonical
                    ON bridge_sources(canonical_message_id);

                CREATE TABLE IF NOT EXISTS bridge_deliveries (
                    delivery_id INTEGER PRIMARY KEY,
                    canonical_message_id INTEGER NOT NULL,
                    target_scope_key TEXT NOT NULL,
                    target_native_event_id TEXT NOT NULL DEFAULT '',
                    confirmed_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_bridge_deliveries_reply
                    ON bridge_deliveries(
                        canonical_message_id, target_scope_key, confirmed_at
                    );

                CREATE TABLE IF NOT EXISTS bridge_cursors (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
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


class MatrixClient:
    def __init__(
        self,
        homeserver: str,
        access_token: str,
        *,
        user_id: str,
        sync_timeout_ms: int = 30000,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.homeserver = homeserver.rstrip("/")
        self.access_token = access_token
        self.user_id = user_id
        self.sync_timeout_ms = max(int(sync_timeout_ms), 1000)
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(max(self.sync_timeout_ms / 1000 + 10, 20))
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def send(self, delivery: Delivery) -> str:
        origin_platform = delivery.source_scope_key.split(":", 1)[0]
        lowered = lower_message(
            delivery.body,
            MATRIX_CAPABILITIES,
            destination_platform="matrix",
            origin_platform=origin_platform,
        )
        text = "\n".join(
            render_fallback_text(chunk) for chunk in lowered.chunks
        ).strip()
        if not text:
            raise BridgePermanentError("Matrix lowering produced no text")
        content: dict[str, Any] = {"msgtype": "m.text", "body": text}
        if delivery.reply_to_native_message_id:
            content["m.relates_to"] = {
                "m.in_reply_to": {"event_id": delivery.reply_to_native_message_id}
            }
        txn_id = hashlib.sha256(
            delivery.idempotency_key.encode("utf-8")
        ).hexdigest()
        room = quote(delivery.target_native_conversation_id, safe="")
        url = (
            f"{self.homeserver}/_matrix/client/v3/rooms/{room}"
            f"/send/m.room.message/{txn_id}"
        )
        try:
            response = await self.client.put(
                url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                json=content,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            # Matrix transaction IDs make repeating this exact PUT idempotent.
            raise BridgeRetryableError(str(exc)) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise BridgeRetryableError(_http_error(response))
        if response.status_code >= 400:
            raise BridgePermanentError(_http_error(response))
        payload = _json_object(response)
        event_id = str(payload.get("event_id") or "").strip()
        if not event_id:
            raise BridgeRetryableError("Matrix response omitted event_id")
        return event_id

    async def sync(self, since: str = "") -> dict[str, Any]:
        params: dict[str, object] = {"timeout": self.sync_timeout_ms}
        if since:
            params["since"] = since
        response = await self.client.get(
            f"{self.homeserver}/_matrix/client/v3/sync",
            headers={"Authorization": f"Bearer {self.access_token}"},
            params=params,
        )
        if response.status_code >= 400:
            raise BridgeRetryableError(_http_error(response))
        return _json_object(response)

    async def backfill(
        self,
        room_id: str,
        from_token: str,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        room = quote(room_id, safe="")
        response = await self.client.get(
            f"{self.homeserver}/_matrix/client/v3/rooms/{room}/messages",
            headers={"Authorization": f"Bearer {self.access_token}"},
            params={"from": from_token, "dir": "b", "limit": limit},
        )
        if response.status_code >= 400:
            raise BridgeRetryableError(_http_error(response))
        return _json_object(response)


class BlueBubblesClient:
    def __init__(
        self,
        base_url: str,
        password: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.password = password
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(45.0))

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def send(self, delivery: Delivery) -> str:
        origin_platform = delivery.source_scope_key.split(":", 1)[0]
        lowered = lower_message(
            delivery.body,
            IMESSAGE_CAPABILITIES,
            destination_platform="imessage",
            origin_platform=origin_platform,
        )
        text = "\n".join(
            render_fallback_text(chunk) for chunk in lowered.chunks
        ).strip()
        if not text:
            raise BridgePermanentError("iMessage lowering produced no text")
        payload: dict[str, Any] = {
            "chatGuid": delivery.target_native_conversation_id,
            "message": text,
            "method": "private-api",
        }
        if delivery.reply_to_native_message_id:
            payload["selectedMessageGuid"] = delivery.reply_to_native_message_id
        try:
            response = await self.client.post(
                f"{self.base_url}/api/v1/message/text",
                params={"password": self.password},
                json=payload,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            # BlueBubbles' POST has no transaction id. The send may have landed.
            raise BridgeOutcomeUnknown(str(exc)) from exc
        if response.status_code >= 500:
            raise BridgeOutcomeUnknown(_http_error(response))
        if response.status_code >= 400:
            raise BridgePermanentError(_http_error(response))
        result = _json_object(response)
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        guid = str(data.get("guid") or result.get("guid") or "").strip()
        # Some BlueBubbles modes accept before chat.db exposes the GUID. The
        # webhook echo will authoritatively confirm the parked delivery.
        if not guid:
            raise BridgeOutcomeUnknown("BlueBubbles accepted send without a GUID")
        return guid


class BridgeManager:
    def __init__(
        self,
        router: MirrorRouter,
        ledger: MessageLedger,
        deliveries: DeliveryStore,
        state: MirrorStateStore,
        *,
        matrix: MatrixClient | None = None,
        imessage: BlueBubblesClient | None = None,
    ) -> None:
        self.router = router
        self.ledger = ledger
        self.deliveries = deliveries
        self.state = state
        self.matrix = matrix
        self.imessage = imessage

    async def close(self) -> None:
        if self.matrix is not None:
            await self.matrix.close()
        if self.imessage is not None:
            await self.imessage.close()

    def ingest(self, event: BridgeEvent) -> BridgeIngestResult:
        if self.state.source_seen(event.scope, event.native_event_id):
            return BridgeIngestResult("duplicate")
        if event.is_from_bot:
            delivery = self.deliveries.reconcile_echo(
                event.scope,
                event.body,
                native_message_id=event.native_event_id,
                reply_to_native_message_id=event.reply_to_native_message_id,
                observed_at=event.occurred_at,
            )
            if delivery is not None:
                self.state.confirm_delivery(
                    delivery.delivery_id,
                    event.native_event_id,
                    confirmed_at=event.occurred_at,
                )
                canonical_id = delivery.source_canonical_message_id
            else:
                canonical_id = 0
            self.state.register_source(
                canonical_id or -1,
                event.scope,
                event.native_event_id,
                occurred_at=event.occurred_at,
            )
            return BridgeIngestResult(
                "echo" if delivery is not None else "unmatched-self",
                canonical_message_id=canonical_id,
            )

        canonical_scope = self.router.canonical_scope(event.scope)
        stored = self.ledger.record_message(
            canonical_scope,
            native_message_id=(
                event.native_event_id
                if canonical_scope.key == event.scope.key
                else _canonical_native_id(event)
            ),
            sender_native_user_id=event.sender_native_user_id,
            sender_display=event.sender_display,
            body=event.body,
            occurred_at=event.occurred_at,
            direction="inbound",
            message_kind=event.message_kind,  # type: ignore[arg-type]
            reply_to_native_message_id=(
                event.reply_to_native_message_id
                if canonical_scope.key == event.scope.key
                else None
            ),
            raw_event={
                "source_platform": event.scope.platform,
                "source_scope": event.scope.key,
                "source_native_event_id": event.native_event_id,
                **(event.raw_event or {}),
            },
            identity_platform=event.scope.platform,
        )
        self.state.register_source(
            stored.canonical_message_id,
            event.scope,
            event.native_event_id,
            occurred_at=event.occurred_at,
        )
        created = 0
        bundle = self.router.bundle_for(event.scope)
        for target in self.router.targets(event.scope):
            target_reply = self.state.resolve_reply(
                event.scope,
                event.reply_to_native_message_id,
                target.scope,
            )
            body = _attributed_body(event, target)
            delivery, was_created = self.deliveries.enqueue(
                idempotency_key=(
                    f"mirror:{bundle.name if bundle else 'standalone'}:"
                    f"{stored.canonical_message_id}:{target.key}"
                ),
                source_scope_key=canonical_scope.key,
                source_canonical_message_id=stored.canonical_message_id,
                target_scope=target.scope,
                body=body,
                reply_to_native_message_id=target_reply,
                now=event.occurred_at,
            )
            self.state.register_delivery(
                delivery.delivery_id,
                stored.canonical_message_id,
                target.scope,
            )
            created += int(was_created)
        return BridgeIngestResult(
            "ingested",
            canonical_message_id=stored.canonical_message_id,
            deliveries_created=created,
        )

    def mirror_local_outgoing(
        self,
        *,
        source_scope: ConversationScope,
        source_native_event_id: str,
        canonical_message_id: int,
        body: MessageBody,
        occurred_at: int,
        reply_to_native_message_id: str | None = None,
    ) -> int:
        """Fan out a locally sent bot message without creating a second ledger row."""
        if self.router.bundle_for(source_scope) is None:
            return 0
        self.state.register_source(
            canonical_message_id,
            source_scope,
            source_native_event_id,
            occurred_at=occurred_at,
        )
        created = 0
        for target in self.router.targets(source_scope):
            target_reply = self.state.resolve_reply(
                source_scope,
                reply_to_native_message_id,
                target.scope,
            )
            delivery, was_created = self.deliveries.enqueue(
                idempotency_key=(
                    f"mirror-out:{canonical_message_id}:{target.key}"
                ),
                source_scope_key=self.router.canonical_scope(source_scope).key,
                source_canonical_message_id=canonical_message_id,
                target_scope=target.scope,
                body=body,
                reply_to_native_message_id=target_reply,
                now=occurred_at,
            )
            self.state.register_delivery(
                delivery.delivery_id,
                canonical_message_id,
                target.scope,
            )
            created += int(was_created)
        return created

    async def deliver(self, delivery: Delivery) -> str:
        if delivery.target_platform == "matrix" and self.matrix is not None:
            native_id = await self.matrix.send(delivery)
        elif delivery.target_platform == "imessage" and self.imessage is not None:
            native_id = await self.imessage.send(delivery)
        else:
            raise BridgeRetryableError(
                f"no transport registered for {delivery.target_platform}"
            )
        self.state.confirm_delivery(delivery.delivery_id, native_id)
        return native_id

    async def sync_matrix_once(self) -> int:
        if self.matrix is None:
            return 0
        cursor_key = f"matrix-sync:{self.matrix.user_id}"
        since = self.state.get_cursor(cursor_key)
        payload = await self.matrix.sync(since)
        next_batch = str(payload.get("next_batch") or "").strip()
        if not next_batch:
            raise BridgeRetryableError("Matrix /sync response omitted next_batch")
        if not since:
            self.state.set_cursor(cursor_key, next_batch)
            return 0
        rooms = payload.get("rooms") if isinstance(payload.get("rooms"), dict) else {}
        joined = rooms.get("join") if isinstance(rooms.get("join"), dict) else {}
        processed = 0
        configured = {endpoint.conversation_id for endpoint in self.router.endpoints("matrix")}
        for room_id, room_data in joined.items():
            if str(room_id) not in configured or not isinstance(room_data, dict):
                continue
            timeline = room_data.get("timeline")
            timeline = timeline if isinstance(timeline, dict) else {}
            raw_events = timeline.get("events")
            events = raw_events if isinstance(raw_events, list) else []
            if bool(timeline.get("limited")) and timeline.get("prev_batch"):
                history = await self._matrix_gap_events(
                    str(room_id), str(timeline["prev_batch"])
                )
                events = history + events
            for raw_event in events:
                event = decode_matrix_event(
                    str(room_id),
                    raw_event,
                    bot_user_id=self.matrix.user_id,
                )
                if event is None:
                    continue
                result = self.ingest(event)
                processed += int(result.status == "ingested")
        self.state.set_cursor(cursor_key, next_batch)
        return processed

    async def _matrix_gap_events(self, room_id: str, token: str) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        current = token
        scope = ConversationScope("matrix", "group", room_id)
        for _page in range(20):
            page = await self.matrix.backfill(room_id, current)  # type: ignore[union-attr]
            chunk = page.get("chunk") if isinstance(page.get("chunk"), list) else []
            found_boundary = False
            for raw_event in chunk:
                if not isinstance(raw_event, dict):
                    continue
                event_id = str(raw_event.get("event_id") or "")
                if event_id and self.state.source_seen(scope, event_id):
                    found_boundary = True
                    break
                collected.append(raw_event)
            if found_boundary or not chunk:
                break
            next_token = str(page.get("end") or "")
            if not next_token or next_token == current:
                break
            current = next_token
        collected.reverse()
        return collected


def decode_matrix_event(
    room_id: str,
    raw_event: Any,
    *,
    bot_user_id: str = "",
) -> BridgeEvent | None:
    if not isinstance(raw_event, dict) or raw_event.get("type") != "m.room.message":
        return None
    content = raw_event.get("content")
    if not isinstance(content, dict):
        return None
    event_id = str(raw_event.get("event_id") or "").strip()
    sender = str(raw_event.get("sender") or "").strip()
    if not event_id or not sender:
        return None
    msgtype = str(content.get("msgtype") or "m.text")
    body_text = str(content.get("body") or "")
    nodes: list[Any] = []
    if msgtype in {"m.text", "m.notice", "m.emote"}:
        nodes.append(TextNode(0, body_text))
    elif msgtype in {"m.image", "m.video", "m.audio", "m.file"}:
        kind = {
            "m.image": "image",
            "m.video": "video",
            "m.audio": "audio",
            "m.file": "file",
        }[msgtype]
        info = content.get("info") if isinstance(content.get("info"), dict) else {}
        nodes.append(
            MediaNode(
                0,
                kind,  # type: ignore[arg-type]
                source=str(content.get("url") or ""),
                name=body_text,
                description=body_text,
                mime=str(info.get("mimetype") or ""),
                source_type=msgtype,
                raw_data=content,
            )
        )
    else:
        return None
    relation = content.get("m.relates_to")
    relation = relation if isinstance(relation, dict) else {}
    reply = relation.get("m.in_reply_to")
    reply = reply if isinstance(reply, dict) else {}
    reply_id = str(reply.get("event_id") or "").strip() or None
    timestamp_ms = _safe_int(raw_event.get("origin_server_ts"), int(time.time()) * 1000)
    return BridgeEvent(
        scope=ConversationScope("matrix", "group", room_id, bot_native_user_id=bot_user_id),
        native_event_id=event_id,
        sender_native_user_id=sender,
        sender_display=str(raw_event.get("sender_display") or "Matrix 用户"),
        body=MessageBody(tuple(nodes)),
        occurred_at=max(timestamp_ms // 1000, 1),
        reply_to_native_message_id=reply_id,
        is_from_bot=bool(bot_user_id and sender == bot_user_id),
        raw_event={"matrix": raw_event},
    )


def decode_bluebubbles_webhook(
    payload: Any,
    *,
    configured_chat_guid: str = "",
    bot_handle: str = "",
) -> BridgeEvent | None:
    if not isinstance(payload, dict):
        return None
    raw_data = payload.get("data")
    data = raw_data if isinstance(raw_data, dict) else payload
    event_type = str(payload.get("type") or payload.get("event") or "").lower()
    if event_type and "message" not in event_type and event_type not in {"new-message", "new_message"}:
        return None
    chats = data.get("chats") if isinstance(data.get("chats"), list) else []
    chat = chats[0] if chats and isinstance(chats[0], dict) else {}
    chat_guid = str(
        data.get("chatGuid")
        or data.get("chat_guid")
        or chat.get("guid")
        or configured_chat_guid
        or ""
    ).strip()
    if not chat_guid or (configured_chat_guid and chat_guid != configured_chat_guid):
        return None
    guid = str(data.get("guid") or data.get("messageGuid") or "").strip()
    if not guid:
        return None
    handle = data.get("handle") if isinstance(data.get("handle"), dict) else {}
    sender = str(
        handle.get("address")
        or data.get("handleId")
        or data.get("sender")
        or (bot_handle if data.get("isFromMe") else "unknown")
    ).strip()
    sender_display = str(
        data.get("senderName")
        or handle.get("displayName")
        or "iMessage 用户"
    ).strip()
    nodes: list[Any] = []
    text = str(data.get("text") or "")
    if text:
        nodes.append(TextNode(len(nodes), text))
    attachments = data.get("attachments")
    if isinstance(attachments, list):
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            mime = str(attachment.get("mimeType") or "")
            kind = "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "audio" if mime.startswith("audio/") else "file"
            name = str(attachment.get("transferName") or attachment.get("guid") or "")
            nodes.append(
                MediaNode(
                    len(nodes),
                    kind,  # type: ignore[arg-type]
                    source=str(attachment.get("guid") or ""),
                    name=name,
                    description=name,
                    mime=mime,
                    source_type="bluebubbles-attachment",
                    raw_data=attachment,
                )
            )
    if not nodes:
        return None
    date_created = _safe_int(
        data.get("dateCreated") or data.get("date_created"), int(time.time())
    )
    if date_created > 10_000_000_000:
        date_created //= 1000
    reply_id = str(
        data.get("threadOriginatorGuid")
        or data.get("thread_originator_guid")
        or ""
    ).strip() or None
    is_from_me = bool(data.get("isFromMe") or data.get("is_from_me"))
    return BridgeEvent(
        scope=ConversationScope(
            "imessage", "group", chat_guid, bot_native_user_id=bot_handle
        ),
        native_event_id=guid,
        sender_native_user_id=sender,
        sender_display=sender_display,
        body=MessageBody(tuple(nodes)),
        occurred_at=max(date_created, 1),
        reply_to_native_message_id=reply_id,
        is_from_bot=is_from_me or bool(bot_handle and sender == bot_handle),
        raw_event={"bluebubbles": payload},
    )


def register_bridge_routes(
    app: Any,
    manager: BridgeManager,
    *,
    matrix_appservice_token: str = "",
    bluebubbles_webhook_token: str = "",
    bluebubbles_chat_guid: str = "",
    bluebubbles_bot_handle: str = "",
    path: str = "/bot-bridge",
) -> None:
    prefix = "/" + path.strip("/")
    router = APIRouter()

    @router.put("/_matrix/app/v1/transactions/{txn_id}")
    async def matrix_transaction(
        txn_id: str,
        request: Request,
        authorization: Optional[str] = Header(default=None),
        access_token: Optional[str] = Query(default=None),
    ) -> dict[str, object]:
        _authorize_token(
            matrix_appservice_token,
            authorization,
            access_token or "",
        )
        payload = await request.json()
        raw_events = payload.get("events") if isinstance(payload, dict) else []
        events = raw_events if isinstance(raw_events, list) else []
        processed = 0
        for raw_event in events:
            room_id = str(raw_event.get("room_id") or "") if isinstance(raw_event, dict) else ""
            event = decode_matrix_event(
                room_id,
                raw_event,
                bot_user_id=(manager.matrix.user_id if manager.matrix else ""),
            )
            if event is None or manager.router.bundle_for(event.scope) is None:
                continue
            result = manager.ingest(event)
            processed += int(result.status == "ingested")
        return {"ok": True, "transaction_id": txn_id, "processed": processed}

    @router.post(f"{prefix}/bluebubbles")
    async def bluebubbles_webhook(
        request: Request,
        authorization: Optional[str] = Header(default=None),
        token: Optional[str] = Query(default=None),
    ) -> dict[str, object]:
        _authorize_token(
            bluebubbles_webhook_token,
            authorization,
            token or "",
        )
        event = decode_bluebubbles_webhook(
            await request.json(),
            configured_chat_guid=bluebubbles_chat_guid,
            bot_handle=bluebubbles_bot_handle,
        )
        if event is None or manager.router.bundle_for(event.scope) is None:
            return {"ok": True, "processed": 0}
        result = manager.ingest(event)
        return {"ok": True, "processed": int(result.status == "ingested")}

    app.include_router(router)


def _authorize_token(expected: str, authorization: str | None, query_token: str) -> None:
    if not expected:
        raise HTTPException(status_code=503, detail="bridge token is not configured")
    supplied = query_token.strip()
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid bridge token")


def _canonical_native_id(event: BridgeEvent) -> str:
    digest = hashlib.sha256(event.scope.key.encode("utf-8")).hexdigest()[:12]
    return f"bridge:{digest}:{event.native_event_id}"


def _attributed_body(event: BridgeEvent, target: BridgeEndpoint) -> MessageBody:
    if event.scope.platform == target.platform:
        return event.body
    label = event.sender_display or event.sender_native_user_id or "群成员"
    prefix = TextNode(0, f"<{label}> ")
    nodes = [prefix]
    for index, node in enumerate(event.body.nodes, start=1):
        if isinstance(node, TextNode):
            nodes.append(TextNode(index, node.text))
        elif isinstance(node, MentionNode):
            nodes.append(
                MentionNode(
                    index,
                    node.native_user_id,
                    node.display,
                    node.principal_id,
                    node.raw_data,
                )
            )
        elif isinstance(node, MediaNode):
            nodes.append(
                MediaNode(
                    index,
                    node.media_kind,
                    node.source,
                    node.name,
                    node.description,
                    node.mime,
                    node.source_type,
                    node.raw_data,
                )
            )
        else:
            nodes.append(node)
    return MessageBody(tuple(nodes))


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise BridgeRetryableError("remote response was not JSON") from exc
    if not isinstance(payload, dict):
        raise BridgeRetryableError("remote response was not a JSON object")
    return payload


def _http_error(response: httpx.Response) -> str:
    body = response.text.replace("\n", " ").strip()[:500]
    return f"HTTP {response.status_code}: {body}"
