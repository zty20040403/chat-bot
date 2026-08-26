from __future__ import annotations

import asyncio
import inspect
import os
import socket
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, TypeAlias

from ..storage.jobs import DurableJob, DurableJobStore


DurableJobHandler: TypeAlias = Callable[
    [DurableJob],
    Awaitable[Mapping[str, Any] | None] | Mapping[str, Any] | None,
]


class WorkerLogger(Protocol):
    def error(self, message: object, *args: object, **kwargs: object) -> object: ...

    def warning(self, message: object, *args: object, **kwargs: object) -> object: ...

    def info(self, message: object, *args: object, **kwargs: object) -> object: ...


class DurableJobWorker:
    def __init__(
        self,
        store: DurableJobStore,
        *,
        logger: WorkerLogger,
        poll_seconds: float = 2.0,
        concurrency: int = 2,
        worker_id: str | None = None,
    ) -> None:
        self.store = store
        self.logger = logger
        self.poll_seconds = max(float(poll_seconds), 0.1)
        self.concurrency = min(max(int(concurrency), 1), 32)
        self.worker_id = worker_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self._handlers: dict[str, DurableJobHandler] = {}

    def register(self, kind: str, handler: DurableJobHandler) -> None:
        clean_kind = " ".join(str(kind).split())
        if not clean_kind:
            raise ValueError("job handler kind must not be empty")
        if clean_kind in self._handlers:
            raise ValueError(f"job handler already registered: {clean_kind}")
        self._handlers[clean_kind] = handler

    @property
    def registered_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    async def run_once(self) -> int:
        jobs = await asyncio.to_thread(
            self.store.claim_due,
            self.worker_id,
            limit=self.concurrency,
        )
        if not jobs:
            return 0
        await asyncio.gather(*(self._execute(job) for job in jobs))
        return len(jobs)

    async def run_forever(self) -> None:
        while True:
            processed = await self.run_once()
            if processed == 0:
                await asyncio.sleep(self.poll_seconds)
            else:
                await asyncio.sleep(0)

    async def _execute(self, job: DurableJob) -> None:
        handler = self._handlers.get(job.kind)
        if handler is None:
            await asyncio.to_thread(
                self.store.mark_failed,
                job.job_id,
                self.worker_id,
                f"no handler registered for {job.kind}",
                retryable=False,
            )
            self.logger.error(f"Durable job {job.handle} has no handler: {job.kind}")
            return
        heartbeat = asyncio.create_task(
            self._heartbeat(job),
            name=f"durable-job-heartbeat:{job.job_id}",
        )
        try:
            result = handler(job)
            if inspect.isawaitable(result):
                result = await result
            safe_result = dict(result or {})
            changed = await asyncio.to_thread(
                self.store.mark_succeeded,
                job.job_id,
                self.worker_id,
                result=safe_result,
            )
            if not changed:
                self.logger.warning(
                    f"Durable job {job.handle} finished after losing its lease."
                )
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self.store.mark_failed,
                job.job_id,
                self.worker_id,
                "worker shutdown interrupted the task",
                retryable=True,
                retry_delay_seconds=0,
            )
            raise
        except Exception as exc:
            changed = await asyncio.to_thread(
                self.store.mark_failed,
                job.job_id,
                self.worker_id,
                str(exc),
                retryable=True,
                retry_delay_seconds=min(10 * (2 ** max(job.attempts - 1, 0)), 300),
            )
            if changed:
                self.logger.warning(
                    f"Durable job {job.handle} failed on attempt {job.attempts}: {exc}"
                )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    async def _heartbeat(self, job: DurableJob) -> None:
        interval = max(self.store.lease_seconds / 3, 1.0)
        while True:
            await asyncio.sleep(interval)
            renewed = await asyncio.to_thread(
                self.store.renew_lease,
                job.job_id,
                self.worker_id,
            )
            if not renewed:
                return
