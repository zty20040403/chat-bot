"""Bounded, work-conserving scheduling across task and group boundaries."""
from __future__ import annotations

import asyncio
from collections import Counter, deque
from contextlib import asynccontextmanager


class SpecialistScheduler:
    def __init__(self, total: int = 6, per_group: int = 3, per_model: int = 2):
        self.total, self.per_group, self.per_model = total, per_group, per_model
        self.condition = asyncio.Condition()
        self.waiters = deque()
        self.groups, self.models = Counter(), Counter()

    @asynccontextmanager
    async def slot(self, group: str, model: str):
        ticket = (object(), group, model)
        acquired = False
        async with self.condition:
            self.waiters.append(ticket)
            try:
                while True:
                    eligible = next((item for item in self.waiters if self.groups[item[1]] < self.per_group
                                     and self.models[item[2]] < self.per_model), None)
                    if sum(self.groups.values()) < self.total and eligible == ticket:
                        self.waiters.remove(ticket)
                        self.groups[group] += 1
                        self.models[model] += 1
                        acquired = True
                        self.condition.notify_all()
                        break
                    await self.condition.wait()
            finally:
                if not acquired:
                    self.waiters.remove(ticket)
                    self.condition.notify_all()
        try:
            yield
        finally:
            async with self.condition:
                self.groups[group] -= 1
                self.models[model] -= 1
                self.condition.notify_all()

    def snapshot(self):
        return {"active": sum(self.groups.values()), "waiting": len(self.waiters),
                "limit": self.total, "per_group": self.per_group, "per_model": self.per_model}
