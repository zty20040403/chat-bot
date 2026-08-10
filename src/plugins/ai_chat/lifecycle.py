from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol


class LifecycleLogger(Protocol):
    def error(self, message: object, *args: object, **kwargs: object) -> object: ...


TaskFactory = Callable[[], Awaitable[None]]


class BackgroundTaskSupervisor:
    """Owns long-running plugin tasks and gives shutdown one drain point."""

    def __init__(self, logger: LifecycleLogger) -> None:
        self._logger = logger
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._failures: dict[str, str] = {}

    def start(self, name: str, factory: TaskFactory) -> bool:
        current = self._tasks.get(name)
        if current is not None and not current.done():
            return False

        self._failures.pop(name, None)
        task = asyncio.create_task(factory(), name=f"ai-chat:{name}")
        self._tasks[name] = task
        task.add_done_callback(
            lambda completed, task_name=name: self._on_done(
                task_name,
                completed,
            )
        )
        return True

    def running(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, task in self._tasks.items()
            if not task.done()
        )

    def failures(self) -> dict[str, str]:
        return dict(self._failures)

    async def stop_all(self) -> int:
        active = [task for task in self._tasks.values() if not task.done()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        self._tasks.clear()
        return len(active)

    def _on_done(self, name: str, task: asyncio.Task[None]) -> None:
        owns_slot = self._tasks.get(name) is task
        if owns_slot:
            self._tasks.pop(name, None)
        if task.cancelled():
            return
        try:
            failure = task.exception()
        except asyncio.CancelledError:
            return
        if failure is not None and owns_slot:
            self._failures[name] = str(failure)
            self._logger.error(
                f"Background task {name!r} stopped unexpectedly: {failure}"
            )
