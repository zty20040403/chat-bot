from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from .deepseek import ChatMessage


@dataclass(frozen=True)
class GroupContextMessage:
    sender: str
    content: str


class ConversationMemory:
    def __init__(self, max_turns: int) -> None:
        self._histories: defaultdict[str, deque[ChatMessage]] = defaultdict(
            lambda: deque(maxlen=max_turns * 2)
        )

    def get(self, conversation_id: str) -> list[ChatMessage]:
        return list(self._histories[conversation_id])

    def append_turn(self, conversation_id: str, user_text: str, assistant_text: str) -> None:
        history = self._histories[conversation_id]
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": assistant_text})

    def clear(self, conversation_id: str) -> None:
        self._histories.pop(conversation_id, None)


class GroupContextMemory:
    def __init__(self, max_messages: int, max_chars: int) -> None:
        self._messages: defaultdict[int, deque[GroupContextMessage]] = defaultdict(
            lambda: deque(maxlen=max_messages)
        )
        self._max_chars = max_chars

    def append(self, group_id: int, sender: str, content: str) -> None:
        content = " ".join(content.split())
        if not content:
            return
        self._messages[group_id].append(GroupContextMessage(sender=sender, content=content))

    def render(self, group_id: int) -> str:
        lines = [
            f"{message.sender}: {message.content}"
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

    def clear(self, group_id: int) -> None:
        self._messages.pop(group_id, None)
