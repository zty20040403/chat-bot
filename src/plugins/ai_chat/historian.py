from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Iterator, Sequence

from .context_store import CaptureCandidate, ContextStore
from .conversation_scope import ConversationScope
from .ledger import MessageLedger
from .long_term_memory import LongTermMemoryError, LongTermMemoryStore, MemoryEntry


@dataclass(frozen=True)
class MemoryProposal:
    content: str
    source_message_id: int


@dataclass(frozen=True)
class HistorianResult:
    summary_p1: str
    summary_p2: str
    summary_p3: str
    memories: tuple[MemoryProposal, ...] = ()


@dataclass(frozen=True)
class HistorianRun:
    published: int
    memories_added: int
    failures: tuple[str, ...]


HistorianGenerator = Callable[[CaptureCandidate], Awaitable[HistorianResult]]
ProtectedProvider = Callable[[ConversationScope], Sequence[int]]


class HistorianService:
    def __init__(
        self,
        ledger: MessageLedger,
        context_store: ContextStore,
        memory_store: LongTermMemoryStore,
        generator: HistorianGenerator,
        *,
        protected_provider: ProtectedProvider | None = None,
    ) -> None:
        self.ledger = ledger
        self.context_store = context_store
        self.memory_store = memory_store
        self.generator = generator
        self.protected_provider = protected_provider

    async def run_once(self, *, max_scopes: int = 20) -> HistorianRun:
        published = 0
        memories_added = 0
        failures: list[str] = []
        for scope in self.ledger.list_scopes()[: max(int(max_scopes), 1)]:
            protected = tuple(
                int(item)
                for item in (
                    self.protected_provider(scope)
                    if self.protected_provider is not None
                    else ()
                )
            )
            candidate = self.context_store.capture_candidate(
                self.ledger,
                scope,
                protected_message_ids=protected,
            )
            if candidate is None:
                continue
            try:
                generated = await self.generator(candidate)
                self._validate(candidate, generated)
                self.context_store.publish_generated(
                    candidate,
                    (
                        generated.summary_p1,
                        generated.summary_p2,
                        generated.summary_p3,
                    ),
                )
                published += 1
                if scope.kind == "group":
                    memory_scope = _group_memory_scope(scope)
                    allowed_ids = {
                        item.canonical_message_id for item in candidate.messages
                    }
                    for proposal in generated.memories[:10]:
                        if proposal.source_message_id not in allowed_ids:
                            continue
                        if _looks_like_secret(proposal.content):
                            continue
                        try:
                            _entry, created = self.memory_store.add(
                                memory_scope,
                                "group",
                                proposal.content,
                                creator_user_id=0,
                                creator_principal_id=0,
                                source_message_id=proposal.source_message_id,
                                reason="historian proposal",
                            )
                        except LongTermMemoryError:
                            continue
                        memories_added += int(created)
            except Exception as exc:
                failures.append(f"{scope.key}: {exc}")
        return HistorianRun(published, memories_added, tuple(failures))

    @staticmethod
    def _validate(
        candidate: CaptureCandidate,
        generated: HistorianResult,
    ) -> None:
        summaries = (
            generated.summary_p1.strip(),
            generated.summary_p2.strip(),
            generated.summary_p3.strip(),
        )
        if any(not summary for summary in summaries):
            raise ValueError("historian omitted a required summary tier")
        if len(summaries[0]) > 3200 or len(summaries[1]) > 1600:
            raise ValueError("historian summary exceeds the publication budget")
        allowed = {message.canonical_message_id for message in candidate.messages}
        for proposal in generated.memories:
            if proposal.source_message_id not in allowed:
                raise ValueError("historian memory cites evidence outside its capture")
            if not proposal.content.strip():
                raise ValueError("historian proposed an empty memory")


@dataclass(frozen=True)
class DreamOperation:
    action: str
    memory_id: int
    expected_version: int
    content: str = ""
    reason: str = ""


DreamGenerator = Callable[
    [str, Sequence[MemoryEntry], str],
    Awaitable[Sequence[DreamOperation]],
]


class DreamService:
    def __init__(
        self,
        memory_store: LongTermMemoryStore,
        generator: DreamGenerator,
        *,
        evidence_provider: Callable[[MemoryEntry], str] | None = None,
        min_entries: int = 15,
    ) -> None:
        self.memory_store = memory_store
        self.generator = generator
        self.evidence_provider = evidence_provider
        self.min_entries = max(int(min_entries), 2)

    async def run_once(self) -> dict[str, int]:
        changed = 0
        failed = 0
        for scope_key in self.memory_store.scope_keys():
            entries = self.memory_store.list_entries([scope_key])
            if len(entries) < self.min_entries:
                continue
            evidence = "\n".join(
                self.evidence_provider(entry)
                for entry in entries
                if self.evidence_provider is not None
            )
            try:
                operations = await self.generator(scope_key, entries, evidence)
                changed += self._apply(scope_key, entries, operations)
            except Exception:
                failed += 1
        return {"changed": changed, "failed_scopes": failed}

    def _apply(
        self,
        scope_key: str,
        entries: Sequence[MemoryEntry],
        operations: Sequence[DreamOperation],
    ) -> int:
        visible = {entry.id: entry for entry in entries}
        changed = 0
        for operation in list(operations)[:50]:
            current = visible.get(int(operation.memory_id))
            if current is None or current.version != int(operation.expected_version):
                continue
            reason = "dream consolidation: " + (
                " ".join(operation.reason.split())[:140] or "maintenance"
            )
            if operation.action == "update":
                updated = self.memory_store.update(
                    current.id,
                    operation.content,
                    [scope_key],
                    expected_version=current.version,
                    actor_user_id=0,
                    actor_principal_id=0,
                    source_message_id=current.source_message_id,
                    reason=reason,
                )
                visible[current.id] = updated
                changed += 1
            elif operation.action in {"remove", "archive"}:
                if self.memory_store.remove(
                    current.id,
                    [scope_key],
                    actor_user_id=0,
                    actor_principal_id=0,
                    source_message_id=current.source_message_id,
                    reason=reason,
                ):
                    visible.pop(current.id, None)
                    changed += 1
        return changed


class MaintenanceState:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path) if str(path) != ":memory:" else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(path), timeout=10.0, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        with self._transaction() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS maintenance_state (
                    job_name TEXT PRIMARY KEY,
                    last_success_key TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def completed(self, job_name: str, success_key: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT last_success_key FROM maintenance_state
                WHERE job_name = ?
                """,
                (job_name,),
            ).fetchone()
        return row is not None and str(row[0]) == success_key

    def mark_completed(self, job_name: str, success_key: str) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO maintenance_state (
                    job_name, last_success_key, updated_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(job_name) DO UPDATE SET
                    last_success_key = excluded.last_success_key,
                    updated_at = excluded.updated_at
                """,
                (job_name, success_key, int(time.time())),
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


def parse_historian_payload(payload: dict[str, object]) -> HistorianResult:
    raw_memories = payload.get("memories")
    memories: list[MemoryProposal] = []
    if isinstance(raw_memories, list):
        for raw in raw_memories[:10]:
            if not isinstance(raw, dict):
                continue
            try:
                source_message_id = int(raw.get("source_message_id") or 0)
            except (TypeError, ValueError):
                continue
            content = " ".join(str(raw.get("content") or "").split())
            if source_message_id > 0 and content:
                memories.append(MemoryProposal(content[:300], source_message_id))
    return HistorianResult(
        summary_p1=str(payload.get("summary_p1") or "").strip(),
        summary_p2=str(payload.get("summary_p2") or "").strip(),
        summary_p3=str(payload.get("summary_p3") or "").strip(),
        memories=tuple(memories),
    )


def parse_dream_payload(payload: dict[str, object]) -> list[DreamOperation]:
    raw_operations = payload.get("operations")
    if not isinstance(raw_operations, list):
        return []
    operations: list[DreamOperation] = []
    for raw in raw_operations[:50]:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action") or "").strip().lower()
        if action not in {"update", "remove", "archive"}:
            continue
        try:
            memory_id = int(raw.get("memory_id") or 0)
            expected_version = int(raw.get("expected_version") or 0)
        except (TypeError, ValueError):
            continue
        if memory_id <= 0 or expected_version <= 0:
            continue
        content = " ".join(str(raw.get("content") or "").split())[:300]
        if action == "update" and not content:
            continue
        operations.append(
            DreamOperation(
                action=action,
                memory_id=memory_id,
                expected_version=expected_version,
                content=content,
                reason=" ".join(str(raw.get("reason") or "").split())[:200],
            )
        )
    return operations


def render_capture(candidate: CaptureCandidate) -> str:
    return "\n".join(
        f"msg#{message.canonical_message_id} {message.sender_display}: "
        f"{message.prompt_text}"
        for message in candidate.messages
        if message.prompt_text
    )


def _group_memory_scope(scope: ConversationScope) -> str:
    if scope.platform == "onebot-v11":
        return f"group:{scope.native_conversation_id}"
    return f"group:{scope.platform}:{scope.native_conversation_id}"


def _looks_like_secret(content: str) -> bool:
    return bool(
        re.search(r"\bsk-[A-Za-z0-9_-]{10,}\b", content)
        or re.search(
            r"(?i)(?:api[_ -]?key|access[_ -]?token|password|secret|密码|验证码)"
            r"\s*[:=：]\s*\S+",
            content,
        )
    )
