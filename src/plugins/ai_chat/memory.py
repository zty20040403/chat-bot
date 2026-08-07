from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .deepseek import ChatMessage


@dataclass(frozen=True)
class GroupContextMessage:
    sender: str
    content: str
    timestamp: int = 0
    message_id: int = 0


class ConversationMemory:
    def __init__(self, max_turns: int, state_path: Path | None = None) -> None:
        self._max_messages = max(max_turns, 1) * 2
        self._state_path = state_path
        self._histories: defaultdict[str, deque[ChatMessage]] = defaultdict(
            self._new_history
        )
        self._load()

    def _new_history(self) -> deque[ChatMessage]:
        return deque(maxlen=self._max_messages)

    def get(self, conversation_id: str) -> list[ChatMessage]:
        return list(self._histories.get(conversation_id, ()))

    def append_turn(
        self,
        conversation_id: str,
        user_text: str,
        assistant_text: str,
    ) -> None:
        history = self._histories[conversation_id]
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": assistant_text})
        self._save()

    def clear(self, conversation_id: str) -> bool:
        removed = self._histories.pop(conversation_id, None) is not None
        if removed:
            self._save()
        return removed

    def _load(self) -> None:
        data = _read_json_object(self._state_path)
        for raw_conversation_id, raw_history in data.items():
            if not isinstance(raw_conversation_id, str) or not isinstance(
                raw_history, list
            ):
                continue
            history = self._new_history()
            for raw_message in raw_history[-self._max_messages :]:
                message = _valid_chat_message(raw_message)
                if message is not None:
                    history.append(message)
            if history:
                self._histories[raw_conversation_id] = history

    def _save(self) -> None:
        payload = {
            conversation_id: list(history)
            for conversation_id, history in self._histories.items()
            if history
        }
        _write_json(self._state_path, payload)


class GroupContextMemory:
    def __init__(
        self,
        max_messages: int,
        max_chars: int,
        state_path: Path | None = None,
    ) -> None:
        self._max_messages = max(max_messages, 1)
        self._messages: defaultdict[int, deque[GroupContextMessage]] = defaultdict(
            self._new_group
        )
        self._max_chars = max(max_chars, 200)
        self._state_path = state_path
        self._load()

    def _new_group(self) -> deque[GroupContextMessage]:
        return deque(maxlen=self._max_messages)

    def append(
        self,
        group_id: int,
        sender: str,
        content: str,
        *,
        timestamp: int | None = None,
        message_id: int = 0,
    ) -> None:
        content = " ".join(content.split())
        sender = " ".join(sender.split())
        if not content or not sender:
            return
        self._messages[group_id].append(
            GroupContextMessage(
                sender=sender[:200],
                content=content[: max(self._max_chars, 1000)],
                timestamp=max(int(timestamp or time.time()), 0),
                message_id=max(int(message_id), 0),
            )
        )
        self._save()

    def render(self, group_id: int) -> str:
        lines = [
            _render_group_message(message)
            for message in self._messages.get(group_id, ())
        ]
        if not lines:
            return ""

        kept: list[str] = []
        total = 0
        for line in reversed(lines):
            line_len = len(line) + 1
            if kept and total + line_len > self._max_chars:
                break
            kept.append(line)
            total += line_len
        return "\n".join(reversed(kept))

    def clear(self, group_id: int) -> int:
        removed = self._messages.pop(group_id, None)
        if removed is not None:
            self._save()
        return len(removed or ())

    def _load(self) -> None:
        data = _read_json_object(self._state_path)
        for raw_group_id, raw_messages in data.items():
            if not isinstance(raw_messages, list):
                continue
            try:
                group_id = int(raw_group_id)
            except (TypeError, ValueError):
                continue
            messages = self._new_group()
            for raw_message in raw_messages[-self._max_messages :]:
                message = _valid_group_message(raw_message)
                if message is not None:
                    messages.append(message)
            if messages:
                self._messages[group_id] = messages

    def _save(self) -> None:
        payload = {
            str(group_id): [asdict(message) for message in messages]
            for group_id, messages in self._messages.items()
            if messages
        }
        _write_json(self._state_path, payload)


def _valid_chat_message(raw_message: Any) -> ChatMessage | None:
    if not isinstance(raw_message, dict):
        return None
    role = raw_message.get("role")
    content = raw_message.get("content")
    if role not in {"user", "assistant"} or not isinstance(content, str):
        return None
    return {"role": role, "content": content[:12000]}


def _valid_group_message(raw_message: Any) -> GroupContextMessage | None:
    if not isinstance(raw_message, dict):
        return None
    sender = raw_message.get("sender")
    content = raw_message.get("content")
    if not isinstance(sender, str) or not isinstance(content, str):
        return None
    try:
        timestamp = max(int(raw_message.get("timestamp") or 0), 0)
        message_id = max(int(raw_message.get("message_id") or 0), 0)
    except (TypeError, ValueError):
        return None
    sender = " ".join(sender.split())
    content = " ".join(content.split())
    if not sender or not content:
        return None
    return GroupContextMessage(
        sender=sender[:200],
        content=content[:12000],
        timestamp=timestamp,
        message_id=message_id,
    )


def _render_group_message(message: GroupContextMessage) -> str:
    metadata: list[str] = []
    if message.timestamp > 0:
        metadata.append(datetime.fromtimestamp(message.timestamp).strftime("%H:%M"))
    if message.message_id > 0:
        metadata.append(f"#{message.message_id}")
    prefix = f"[{' '.join(metadata)}] " if metadata else ""
    return f"{prefix}{message.sender}: {message.content}"


def _read_json_object(state_path: Path | None) -> dict[str, Any]:
    if state_path is None or not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(state_path: Path | None, payload: object) -> None:
    if state_path is None:
        return
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = state_path.with_suffix(state_path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(state_path)
    except OSError:
        return
