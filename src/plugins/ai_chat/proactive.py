from __future__ import annotations

import random
import re
import time
from collections import defaultdict
from collections.abc import Callable

from src.bot_storage import StateSource, open_json_state


class ProactiveChatScheduler:
    def __init__(
        self,
        min_messages: int,
        cooldown_seconds: int,
        chance_percent: int,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        self._min_messages = max(1, min_messages)
        self._cooldown_seconds = max(0, cooldown_seconds)
        self._chance = min(max(chance_percent, 0), 100) / 100
        self._random_value = random_value
        self._message_counts: defaultdict[int, int] = defaultdict(int)
        self._last_triggered_at: dict[int, float] = {}

    def should_trigger(
        self, group_id: int, text: str, now: float | None = None
    ) -> bool:
        if not _is_candidate_message(text):
            return False

        self._message_counts[group_id] += 1
        if self._message_counts[group_id] < self._min_messages:
            return False

        current_time = time.monotonic() if now is None else now
        last_triggered_at = self._last_triggered_at.get(group_id)
        if (
            last_triggered_at is not None
            and current_time - last_triggered_at < self._cooldown_seconds
        ):
            return False

        if self._random_value() >= self._chance:
            return False

        self._message_counts[group_id] = 0
        self._last_triggered_at[group_id] = current_time
        return True

    def reset(self, group_id: int) -> None:
        self._message_counts.pop(group_id, None)
        self._last_triggered_at.pop(group_id, None)


class IdleWarmupScheduler:
    def __init__(
        self,
        idle_seconds: int,
        cooldown_seconds: int,
        daily_limit: int,
        state_path: StateSource = None,
    ) -> None:
        self._idle_seconds = max(1, idle_seconds)
        self._cooldown_seconds = max(0, cooldown_seconds)
        self._daily_limit = max(0, daily_limit)
        self._state = open_json_state(state_path, "warmup_state")
        self._last_human_activity: dict[int, float] = {}
        self._last_warmup_at: dict[int, float] = {}
        self._daily_counts = self._load_daily_counts()

    def record_human_activity(
        self, group_id: int, now: float | None = None
    ) -> None:
        self._last_human_activity[group_id] = time.monotonic() if now is None else now

    def due_groups(
        self, day: str, now: float | None = None
    ) -> list[int]:
        if self._daily_limit == 0:
            return []

        current_time = time.monotonic() if now is None else now
        due: list[int] = []
        for group_id, last_activity in self._last_human_activity.items():
            if current_time - last_activity < self._idle_seconds:
                continue

            last_warmup = self._last_warmup_at.get(group_id)
            if (
                last_warmup is not None
                and current_time - last_warmup < self._cooldown_seconds
            ):
                continue

            count_day, count = self._daily_counts.get(group_id, ("", 0))
            if count_day == day and count >= self._daily_limit:
                continue
            due.append(group_id)
        return due

    def is_still_idle(self, group_id: int, now: float | None = None) -> bool:
        last_activity = self._last_human_activity.get(group_id)
        if last_activity is None:
            return False
        current_time = time.monotonic() if now is None else now
        return current_time - last_activity >= self._idle_seconds

    def mark_warmup(
        self, group_id: int, day: str, now: float | None = None
    ) -> None:
        current_time = time.monotonic() if now is None else now
        count_day, count = self._daily_counts.get(group_id, ("", 0))
        if count_day != day:
            count = 0

        self._last_warmup_at[group_id] = current_time
        self._daily_counts[group_id] = (day, count + 1)
        self._save_daily_counts()

    def _load_daily_counts(self) -> dict[int, tuple[str, int]]:
        raw = self._state.load()

        if not isinstance(raw, dict):
            return {}

        counts: dict[int, tuple[str, int]] = {}
        for raw_group_id, value in raw.items():
            if not isinstance(value, dict):
                continue
            try:
                group_id = int(raw_group_id)
                day = str(value["day"])
                count = max(0, int(value["count"]))
            except (KeyError, TypeError, ValueError):
                continue
            counts[group_id] = (day, count)
        return counts

    def _save_daily_counts(self) -> None:
        data = {
            str(group_id): {"day": day, "count": count}
            for group_id, (day, count) in self._daily_counts.items()
        }
        self._state.save(data)


def _is_candidate_message(text: str) -> bool:
    normalized = " ".join(text.split())
    if len(normalized) < 3 or len(normalized) > 500:
        return False
    if normalized.startswith("/"):
        return False
    return re.search(r"[\w\u4e00-\u9fff]", normalized) is not None
