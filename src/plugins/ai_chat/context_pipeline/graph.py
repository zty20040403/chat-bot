from __future__ import annotations

import re
from collections import deque

from ..ledger import CanonicalMessage


_LOW_INFORMATION = re.compile(
    r"^(?:哈+|呵+|啊+|哦+|嗯+|草+|笑死|确实|好吧|行吧|可以|收到|"
    r"[?？!！。.，,~～…]+)$",
    re.IGNORECASE,
)
_DEICTIC_CONTINUATION = re.compile(
    r"^(?:这个|那个|这|那|它|他|她|上面|前面|然后|所以|确实|但是|不过)"
)


def topic_terms(text: str) -> set[str]:
    folded = str(text).casefold()
    terms = {
        match.group(0)
        for match in re.finditer(r"[a-z0-9][a-z0-9_.+#/-]{1,}", folded)
    }
    for run in re.findall(r"[\u3400-\u9fff]+", folded):
        terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms.difference(
        {
            "一个",
            "一下",
            "为什么",
            "什么",
            "可以",
            "怎么",
            "这个",
            "那个",
            "觉得",
            "认为",
            "然后",
            "所以",
        }
    )


class MessageReferenceGraph:
    """A scope-local graph built from immutable reply relationships."""

    def __init__(self, messages: list[CanonicalMessage]) -> None:
        self.messages = {
            message.canonical_message_id: message for message in messages
        }
        self.parents: dict[int, int] = {}
        self.children: dict[int, list[int]] = {}
        for message in messages:
            parent = message.reply_to_canonical_message_id
            if parent is None or parent not in self.messages:
                continue
            self.parents[message.canonical_message_id] = parent
            self.children.setdefault(parent, []).append(
                message.canonical_message_id
            )
        for children in self.children.values():
            children.sort()

    def root(self, message_id: int) -> int:
        current = int(message_id)
        visited: set[int] = set()
        while current in self.parents and current not in visited:
            visited.add(current)
            current = self.parents[current]
        return current

    def ancestors(self, message_id: int, *, limit: int = 6) -> tuple[int, ...]:
        current = int(message_id)
        result: list[int] = []
        visited: set[int] = set()
        while current in self.parents and len(result) < max(int(limit), 0):
            current = self.parents[current]
            if current in visited:
                break
            visited.add(current)
            result.append(current)
        result.reverse()
        return tuple(result)

    def descendants(
        self,
        message_id: int,
        *,
        before_message_id: int,
        limit: int = 12,
    ) -> tuple[int, ...]:
        queue = deque(self.children.get(int(message_id), ()))
        found: list[int] = []
        visited: set[int] = set()
        while queue and len(found) < max(int(limit), 0):
            candidate = queue.popleft()
            if candidate in visited or candidate >= int(before_message_id):
                continue
            visited.add(candidate)
            found.append(candidate)
            queue.extend(self.children.get(candidate, ()))
        return tuple(sorted(found))

    def related_ids(
        self,
        focus_message_id: int,
        current_message_id: int,
        *,
        limit: int = 8,
    ) -> tuple[int, ...]:
        focus = self.messages.get(int(focus_message_id))
        if focus is None:
            return ()
        hard_links = [
            *self.ancestors(focus_message_id, limit=3),
            *self.descendants(
                focus_message_id,
                before_message_id=current_message_id,
                limit=limit,
            ),
        ]
        selected = set(hard_links)
        focus_terms = topic_terms(focus.prompt_text)
        chronological = sorted(
            (
                message
                for message in self.messages.values()
                if focus_message_id
                < message.canonical_message_id
                < current_message_id
            ),
            key=lambda item: item.canonical_message_id,
        )
        last_time = focus.occurred_at
        for message in chronological:
            if message.canonical_message_id in selected:
                focus_terms.update(topic_terms(message.prompt_text))
                last_time = message.occurred_at
                continue
            age = max(message.occurred_at - last_time, 0)
            terms = topic_terms(message.prompt_text)
            overlap = bool(focus_terms.intersection(terms))
            continuation = bool(
                _LOW_INFORMATION.fullmatch(message.prompt_text.strip())
                or _DEICTIC_CONTINUATION.match(message.prompt_text.strip())
            )
            if age <= 300 and (overlap or continuation):
                selected.add(message.canonical_message_id)
                focus_terms.update(terms)
                last_time = message.occurred_at

        ordered = [
            message_id
            for message_id in sorted(selected)
            if message_id != focus_message_id
        ]
        return tuple(ordered[-max(int(limit), 0) :])

    def topic_query(
        self,
        focus_message_id: int,
        related_message_ids: tuple[int, ...],
        *,
        max_chars: int = 900,
    ) -> str:
        ordered_ids = tuple(
            dict.fromkeys((int(focus_message_id), *related_message_ids))
        )
        snippets: list[str] = []
        used = 0
        for message_id in ordered_ids:
            message = self.messages.get(message_id)
            if message is None:
                continue
            text = " ".join(message.prompt_text.split()).strip()
            if not text or _LOW_INFORMATION.fullmatch(text):
                continue
            remaining = max(int(max_chars) - used, 0)
            if remaining <= 0:
                break
            snippets.append(text[:remaining])
            used += len(snippets[-1]) + 1
        return " ".join(snippets)
