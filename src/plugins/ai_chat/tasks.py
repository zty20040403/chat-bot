from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RunningTaskInfo:
    task_id: str
    conversation_id: str
    user_id: int
    group_id: int | None
    message_id: int
    summary: str
    started_at: float

    @property
    def elapsed_seconds(self) -> int:
        return max(int(time.time() - self.started_at), 0)


class RunningTaskRegistry:
    def __init__(self) -> None:
        self._next_id = 1
        self._tasks: dict[str, tuple[RunningTaskInfo, asyncio.Task[object]]] = {}

    def register_current(
        self,
        *,
        conversation_id: str,
        user_id: int,
        group_id: int | None,
        message_id: int,
        summary: str,
    ) -> RunningTaskInfo:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("No current asyncio task to register.")
        task_id = f"t{self._next_id}"
        self._next_id += 1
        info = RunningTaskInfo(
            task_id=task_id,
            conversation_id=conversation_id,
            user_id=user_id,
            group_id=group_id,
            message_id=message_id,
            summary=" ".join(summary.split())[:80],
            started_at=time.time(),
        )
        self._tasks[task_id] = (info, task)
        return info

    def finish(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)

    def list_for(self, conversation_id: str) -> list[RunningTaskInfo]:
        self._discard_finished()
        return sorted(
            (
                info
                for info, _task in self._tasks.values()
                if info.conversation_id == conversation_id
            ),
            key=lambda info: info.started_at,
        )

    def cancel(
        self,
        conversation_id: str,
        task_id: str | None = None,
    ) -> RunningTaskInfo | None:
        candidates = self.list_for(conversation_id)
        if task_id:
            selected = next(
                (info for info in candidates if info.task_id == task_id),
                None,
            )
        else:
            selected = candidates[-1] if candidates else None
        if selected is None:
            return None

        stored = self._tasks.get(selected.task_id)
        if stored is None:
            return None
        _info, task = stored
        task.cancel()
        return selected

    def cancel_all(self) -> int:
        self._discard_finished()
        count = 0
        for _info, task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
                count += 1
        return count

    def _discard_finished(self) -> None:
        finished_ids = [
            task_id
            for task_id, (_info, task) in self._tasks.items()
            if task.done()
        ]
        for task_id in finished_ids:
            self._tasks.pop(task_id, None)
