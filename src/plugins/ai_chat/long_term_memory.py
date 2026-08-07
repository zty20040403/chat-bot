from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Literal


class LongTermMemoryError(ValueError):
    pass


@dataclass(frozen=True)
class MemoryEntry:
    id: int
    scope_key: str
    scope_type: str
    content: str
    creator_user_id: int
    created_at: int
    version: int = 1
    creator_principal_id: int = 0
    source_message_id: int | None = None
    updated_at: int = 0


MemoryMutationAction = Literal["create", "update", "remove", "clear", "evict"]


@dataclass(frozen=True)
class MemoryMutation:
    mutation_id: int
    memory_id: int
    scope_key: str
    action: MemoryMutationAction
    from_version: int
    to_version: int
    actor_user_id: int
    actor_principal_id: int
    source_message_id: int | None
    reason: str
    created_at: int


class LongTermMemoryStore:
    def __init__(
        self,
        state_path: Path,
        *,
        max_entries_per_scope: int = 30,
        max_content_chars: int = 300,
    ) -> None:
        self._state_path = state_path
        self._max_entries_per_scope = max(max_entries_per_scope, 1)
        self._max_content_chars = max(max_content_chars, 20)
        self._next_id = 1
        self._next_mutation_id = 1
        self._entries: list[MemoryEntry] = []
        self._mutations: list[MemoryMutation] = []
        self._load()

    @property
    def max_content_chars(self) -> int:
        return self._max_content_chars

    def add(
        self,
        scope_key: str,
        scope_type: str,
        content: str,
        *,
        creator_user_id: int,
        creator_principal_id: int = 0,
        source_message_id: int | None = None,
        reason: str = "explicit add",
    ) -> tuple[MemoryEntry, bool]:
        content = " ".join(content.split())
        if not content:
            raise LongTermMemoryError("记忆内容不能为空。")
        if len(content) > self._max_content_chars:
            raise LongTermMemoryError(
                f"单条记忆不能超过 {self._max_content_chars} 个字符。"
            )
        if scope_type not in {"group", "user"}:
            raise LongTermMemoryError("记忆范围无效。")

        folded_content = content.casefold()
        for entry in self._entries:
            if (
                entry.scope_key == scope_key
                and entry.content.casefold() == folded_content
            ):
                return entry, False

        now = int(time.time())
        entry = MemoryEntry(
            id=self._next_id,
            scope_key=scope_key,
            scope_type=scope_type,
            content=content,
            creator_user_id=max(int(creator_user_id), 0),
            created_at=now,
            version=1,
            creator_principal_id=max(int(creator_principal_id), 0),
            source_message_id=(
                max(int(source_message_id), 1)
                if source_message_id is not None
                else None
            ),
            updated_at=now,
        )
        self._next_id += 1
        self._entries.append(entry)
        self._append_mutation(
            entry,
            "create",
            from_version=0,
            to_version=1,
            actor_user_id=creator_user_id,
            actor_principal_id=creator_principal_id,
            source_message_id=source_message_id,
            reason=reason,
            created_at=now,
        )
        self._trim_scope(
            scope_key,
            actor_user_id=creator_user_id,
            actor_principal_id=creator_principal_id,
            source_message_id=source_message_id,
        )
        self._save()
        return entry, True

    def list_entries(self, scope_keys: Iterable[str]) -> list[MemoryEntry]:
        allowed = set(scope_keys)
        return sorted(
            (entry for entry in self._entries if entry.scope_key in allowed),
            key=lambda entry: entry.id,
        )

    def update(
        self,
        entry_id: int,
        content: str,
        scope_keys: Iterable[str],
        *,
        expected_version: int,
        actor_user_id: int = 0,
        actor_principal_id: int = 0,
        source_message_id: int | None = None,
        reason: str = "explicit update",
    ) -> MemoryEntry:
        allowed = set(scope_keys)
        normalized = " ".join(content.split())
        if not normalized:
            raise LongTermMemoryError("记忆内容不能为空。")
        if len(normalized) > self._max_content_chars:
            raise LongTermMemoryError(
                f"单条记忆不能超过 {self._max_content_chars} 个字符。"
            )
        for index, entry in enumerate(self._entries):
            if entry.id != entry_id or entry.scope_key not in allowed:
                continue
            if entry.version != int(expected_version):
                raise LongTermMemoryError(
                    f"记忆版本已变化，当前版本是 {entry.version}。"
                )
            now = int(time.time())
            updated = MemoryEntry(
                **{
                    **asdict(entry),
                    "content": normalized,
                    "version": entry.version + 1,
                    "updated_at": now,
                }
            )
            self._entries[index] = updated
            self._append_mutation(
                updated,
                "update",
                from_version=entry.version,
                to_version=updated.version,
                actor_user_id=actor_user_id,
                actor_principal_id=actor_principal_id,
                source_message_id=source_message_id,
                reason=reason,
                created_at=now,
            )
            self._save()
            return updated
        raise LongTermMemoryError("当前可见范围内找不到这条记忆。")

    def remove(
        self,
        entry_id: int,
        scope_keys: Iterable[str],
        *,
        actor_user_id: int = 0,
        actor_principal_id: int = 0,
        source_message_id: int | None = None,
        reason: str = "explicit remove",
    ) -> bool:
        allowed = set(scope_keys)
        removed_entries = [
            entry
            for entry in self._entries
            if entry.id == entry_id and entry.scope_key in allowed
        ]
        self._entries = [
            entry
            for entry in self._entries
            if not (entry.id == entry_id and entry.scope_key in allowed)
        ]
        if removed_entries:
            now = int(time.time())
            for entry in removed_entries:
                self._append_mutation(
                    entry,
                    "remove",
                    from_version=entry.version,
                    to_version=entry.version,
                    actor_user_id=actor_user_id,
                    actor_principal_id=actor_principal_id,
                    source_message_id=source_message_id,
                    reason=reason,
                    created_at=now,
                )
            self._save()
        return bool(removed_entries)

    def clear(
        self,
        scope_keys: Iterable[str],
        *,
        actor_user_id: int = 0,
        actor_principal_id: int = 0,
        source_message_id: int | None = None,
        reason: str = "explicit clear",
    ) -> int:
        allowed = set(scope_keys)
        removed_entries = [
            entry for entry in self._entries if entry.scope_key in allowed
        ]
        self._entries = [
            entry for entry in self._entries if entry.scope_key not in allowed
        ]
        if removed_entries:
            now = int(time.time())
            for entry in removed_entries:
                self._append_mutation(
                    entry,
                    "clear",
                    from_version=entry.version,
                    to_version=entry.version,
                    actor_user_id=actor_user_id,
                    actor_principal_id=actor_principal_id,
                    source_message_id=source_message_id,
                    reason=reason,
                    created_at=now,
                )
            self._save()
        return len(removed_entries)

    def audit(
        self,
        scope_keys: Iterable[str],
        *,
        limit: int = 100,
    ) -> list[MemoryMutation]:
        allowed = set(scope_keys)
        limit = min(max(int(limit), 1), 500)
        return [
            mutation
            for mutation in reversed(self._mutations)
            if mutation.scope_key in allowed
        ][:limit]

    def render(self, group_scope: str | None, user_scope: str) -> str:
        sections: list[str] = []
        if group_scope is not None:
            group_entries = self.list_entries([group_scope])
            if group_entries:
                sections.append(
                    "[当前群长期记忆]\n"
                    + "\n".join(
                        f"- [#{entry.id}] {entry.content}"
                        for entry in group_entries
                    )
                )

        user_entries = self.list_entries([user_scope])
        if user_entries:
            sections.append(
                "[当前用户长期记忆]\n"
                + "\n".join(
                    f"- [#{entry.id}] {entry.content}"
                    for entry in user_entries
                )
            )
        return "\n\n".join(sections)

    def _trim_scope(
        self,
        scope_key: str,
        *,
        actor_user_id: int,
        actor_principal_id: int,
        source_message_id: int | None,
    ) -> None:
        matching = [
            entry for entry in self._entries if entry.scope_key == scope_key
        ]
        excess = len(matching) - self._max_entries_per_scope
        if excess <= 0:
            return
        removed_entries = matching[:excess]
        removed_ids = {entry.id for entry in removed_entries}
        self._entries = [
            entry for entry in self._entries if entry.id not in removed_ids
        ]
        now = int(time.time())
        for entry in removed_entries:
            self._append_mutation(
                entry,
                "evict",
                from_version=entry.version,
                to_version=entry.version,
                actor_user_id=actor_user_id,
                actor_principal_id=actor_principal_id,
                source_message_id=source_message_id,
                reason="scope entry limit",
                created_at=now,
            )

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return

        raw_entries = payload.get("entries", [])
        if not isinstance(raw_entries, list):
            raw_entries = []
        entries: list[MemoryEntry] = []
        for raw_entry in raw_entries:
            entry = self._valid_entry(raw_entry)
            if entry is not None:
                entries.append(entry)
        self._entries = sorted(entries, key=lambda entry: entry.id)
        raw_mutations = payload.get("mutations", [])
        if not isinstance(raw_mutations, list):
            raw_mutations = []
        self._mutations = [
            mutation
            for item in raw_mutations
            if (mutation := self._valid_mutation(item)) is not None
        ]
        largest_id = max((entry.id for entry in self._entries), default=0)
        try:
            stored_next_id = int(payload.get("next_id") or 1)
        except (TypeError, ValueError):
            stored_next_id = 1
        self._next_id = max(stored_next_id, largest_id + 1, 1)
        largest_mutation_id = max(
            (mutation.mutation_id for mutation in self._mutations),
            default=0,
        )
        try:
            stored_next_mutation_id = int(
                payload.get("next_mutation_id") or 1
            )
        except (TypeError, ValueError):
            stored_next_mutation_id = 1
        self._next_mutation_id = max(
            stored_next_mutation_id,
            largest_mutation_id + 1,
            1,
        )

    def _valid_entry(self, raw_entry: Any) -> MemoryEntry | None:
        if not isinstance(raw_entry, dict):
            return None
        try:
            entry_id = int(raw_entry.get("id"))
            creator_user_id = int(raw_entry.get("creator_user_id") or 0)
            created_at = int(raw_entry.get("created_at") or 0)
            version = int(raw_entry.get("version") or 1)
            creator_principal_id = int(
                raw_entry.get("creator_principal_id") or 0
            )
            updated_at = int(raw_entry.get("updated_at") or created_at)
        except (TypeError, ValueError):
            return None
        raw_source_message_id = raw_entry.get("source_message_id")
        try:
            source_message_id = (
                int(raw_source_message_id)
                if raw_source_message_id is not None
                else None
            )
        except (TypeError, ValueError):
            source_message_id = None
        scope_key = raw_entry.get("scope_key")
        scope_type = raw_entry.get("scope_type")
        content = raw_entry.get("content")
        if (
            entry_id <= 0
            or not isinstance(scope_key, str)
            or scope_type not in {"group", "user"}
            or not isinstance(content, str)
        ):
            return None
        content = " ".join(content.split())
        if not content:
            return None
        return MemoryEntry(
            id=entry_id,
            scope_key=scope_key,
            scope_type=scope_type,
            content=content[: self._max_content_chars],
            creator_user_id=max(creator_user_id, 0),
            created_at=max(created_at, 0),
            version=max(version, 1),
            creator_principal_id=max(creator_principal_id, 0),
            source_message_id=(
                max(source_message_id, 1)
                if source_message_id is not None
                else None
            ),
            updated_at=max(updated_at, created_at, 0),
        )

    def _valid_mutation(self, raw: Any) -> MemoryMutation | None:
        if not isinstance(raw, dict):
            return None
        action = raw.get("action")
        if action not in {"create", "update", "remove", "clear", "evict"}:
            return None
        try:
            return MemoryMutation(
                mutation_id=max(int(raw.get("mutation_id") or 0), 1),
                memory_id=max(int(raw.get("memory_id") or 0), 1),
                scope_key=str(raw.get("scope_key") or ""),
                action=action,
                from_version=max(int(raw.get("from_version") or 0), 0),
                to_version=max(int(raw.get("to_version") or 0), 0),
                actor_user_id=max(int(raw.get("actor_user_id") or 0), 0),
                actor_principal_id=max(
                    int(raw.get("actor_principal_id") or 0),
                    0,
                ),
                source_message_id=(
                    int(raw["source_message_id"])
                    if raw.get("source_message_id") is not None
                    else None
                ),
                reason=str(raw.get("reason") or "")[:200],
                created_at=max(int(raw.get("created_at") or 0), 0),
            )
        except (TypeError, ValueError):
            return None

    def _append_mutation(
        self,
        entry: MemoryEntry,
        action: MemoryMutationAction,
        *,
        from_version: int,
        to_version: int,
        actor_user_id: int,
        actor_principal_id: int,
        source_message_id: int | None,
        reason: str,
        created_at: int,
    ) -> None:
        self._mutations.append(
            MemoryMutation(
                mutation_id=self._next_mutation_id,
                memory_id=entry.id,
                scope_key=entry.scope_key,
                action=action,
                from_version=max(int(from_version), 0),
                to_version=max(int(to_version), 0),
                actor_user_id=max(int(actor_user_id), 0),
                actor_principal_id=max(int(actor_principal_id), 0),
                source_message_id=(
                    max(int(source_message_id), 1)
                    if source_message_id is not None
                    else None
                ),
                reason=" ".join(reason.split())[:200],
                created_at=max(int(created_at), 0),
            )
        )
        self._next_mutation_id += 1

    def _save(self) -> None:
        payload = {
            "next_id": self._next_id,
            "next_mutation_id": self._next_mutation_id,
            "entries": [asdict(entry) for entry in self._entries],
            "mutations": [asdict(mutation) for mutation in self._mutations],
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self._state_path.with_suffix(
                self._state_path.suffix + ".tmp"
            )
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(self._state_path)
        except OSError:
            return
