from __future__ import annotations

import math
import time


class RateLimiter:
    def __init__(self, interval_seconds: int) -> None:
        self._interval_seconds = max(0, interval_seconds)
        self._last_seen: dict[str, float] = {}

    def check(self, key: str) -> tuple[bool, int]:
        if self._interval_seconds <= 0:
            return True, 0

        now = time.monotonic()
        last = self._last_seen.get(key)

        if last is None or now - last >= self._interval_seconds:
            self._last_seen[key] = now
            return True, 0

        wait_seconds = math.ceil(self._interval_seconds - (now - last))
        return False, wait_seconds
