from __future__ import annotations

import random
import re
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProactiveDecision:
    interest: int
    reply: str
    voice_suitable: bool

    def should_reply(self, threshold: int) -> bool:
        return bool(self.reply) and self.interest >= min(max(threshold, 0), 100)


def parse_proactive_decision(payload: Mapping[str, Any]) -> ProactiveDecision:
    try:
        interest = int(payload.get("interest", 0))
    except (TypeError, ValueError):
        interest = 0
    interest = min(max(interest, 0), 100)
    reply = str(payload.get("reply") or "").strip()
    voice_suitable = payload.get("voice_suitable") is True
    return ProactiveDecision(interest, reply, voice_suitable)


_LOW_VALUE_PLACEHOLDERS = frozenset(
    {"[图片]", "[表情]", "[语音]", "[视频]", "[文件]", "[json]"}
)
_URL_ONLY = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)


def is_candidate_message(text: str) -> bool:
    normalized = " ".join(str(text).split())
    if not normalized or normalized.startswith("/"):
        return False
    if normalized.casefold() in _LOW_VALUE_PLACEHOLDERS:
        return False
    if _URL_ONLY.fullmatch(normalized):
        return False
    meaningful = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)
    return len(meaningful) >= 6


class ProactiveCheckGate:
    """Bound costly interest checks per group before any LLM call."""

    def __init__(self) -> None:
        self._checks: dict[int, deque[float]] = defaultdict(deque)

    def allows(
        self,
        group_id: int,
        *,
        percent: int,
        max_checks_per_hour: int,
        now: Callable[[], float] = time.monotonic,
        random_value: Callable[[], float] = random.random,
    ) -> bool:
        chance = min(max(int(percent), 0), 100) / 100
        limit = max(int(max_checks_per_hour), 0)
        if chance <= 0 or limit <= 0 or random_value() >= chance:
            return False
        current = now()
        recent = self._checks[int(group_id)]
        cutoff = current - 3600
        while recent and recent[0] <= cutoff:
            recent.popleft()
        if len(recent) >= limit:
            return False
        recent.append(current)
        return True


def should_use_proactive_voice(
    percent: int,
    *,
    random_value: Callable[[], float] = random.random,
) -> bool:
    chance = min(max(int(percent), 0), 100) / 100
    return random_value() < chance
