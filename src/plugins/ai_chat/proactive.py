from __future__ import annotations

import random
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


def is_candidate_message(text: str) -> bool:
    normalized = " ".join(str(text).split())
    return bool(normalized) and not normalized.startswith("/")


def should_use_proactive_voice(
    percent: int,
    *,
    random_value: Callable[[], float] = random.random,
) -> bool:
    chance = min(max(int(percent), 0), 100) / 100
    return random_value() < chance
