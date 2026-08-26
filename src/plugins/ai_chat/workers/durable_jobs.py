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
DurableJobCompensator: TypeAlias = Callable[
    [DurableJob, str],
    Awaitable[None] | None,
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
        self._timeouts: dict[str, float] = {}
        self._compensators: dict[str, DurableJobCompensator] = {}
        self._running: dict[int, asyncio.Task[Mapping[str, Any] | None]] = {}

    def register(
        self,
        kind: str,
        handler: DurableJobHandler,
        *,
        timeout_seconds: float | None = None,
        compensator: DurableJobCompensator | None = None,
    ) -> None:
        clean_kind = " ".join(str(kind).split())
        if not clean_kind:
            raise ValueError("job handler kind must not be empty")
        if clean_kind in self._handlers:
            raise ValueError(f"job handler already registered: {clean_kind}")
        self._handlers[clean_kind] = handler
        if timeout_seconds is not None:
            self._timeouts[clean_kind] = max(float(timeout_seconds), 0.1)
        if compensator is not None:
            self._compensators[clean_kind] = compensator

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

    def cancel(self, job_id: int) -> bool:
        changed = self.store.cancel(job_id)
        task = self._running.get(int(job_id))
        if task is not None and not task.done():
            task.cancel()
        return changed

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
        handler_task = asyncio.create_task(
            self._invoke(handler, job),
            name=f"durable-job-handler:{job.job_id}",
        )
        self._running[job.job_id] = handler_task
        heartbeat = asyncio.create_task(
            self._heartbeat(job, handler_task),
            name=f"durable-job-heartbeat:{job.job_id}",
        )
        try:
            timeout = self._timeouts.get(job.kind)
            result = (
                await asyncio.wait_for(handler_task, timeout=timeout)
                if timeout is not None
                else await handler_task
            )
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
        except TimeoutError:
            await self._compensate(job, "timeout")
            changed = await asyncio.to_thread(
                self.store.mark_failed,
                job.job_id,
                self.worker_id,
                f"job exceeded {self._timeouts[job.kind]:g} seconds",
                retryable=False,
            )
            if changed:
                self.logger.warning(f"Durable job {job.handle} timed out.")
        except asyncio.CancelledError:
            current = await asyncio.to_thread(self.store.get, job.job_id)
            if current is not None and current.status == "cancelled":
                await self._compensate(job, "cancelled")
                self.logger.info(f"Durable job {job.handle} was cancelled.")
                return
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
            retryable = job.attempts < job.max_attempts
            if not retryable:
                await self._compensate(job, "failed")
            changed = await asyncio.to_thread(
                self.store.mark_failed,
                job.job_id,
                self.worker_id,
                str(exc),
                retryable=retryable,
                retry_delay_seconds=min(10 * (2 ** max(job.attempts - 1, 0)), 300),
            )
            if changed:
                self.logger.warning(
                    f"Durable job {job.handle} failed on attempt {job.attempts}: {exc}"
                )
        finally:
            self._running.pop(job.job_id, None)
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    async def _invoke(
        self,
        handler: DurableJobHandler,
        job: DurableJob,
    ) -> Mapping[str, Any] | None:
        result = handler(job)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _compensate(self, job: DurableJob, reason: str) -> None:
        compensator = self._compensators.get(job.kind)
        if compensator is None:
            return
        try:
            result = compensator(job, reason)
            if inspect.isawaitable(result):
                await asyncio.shield(result)
        except Exception as exc:
            self.logger.error(
                f"Durable job {job.handle} compensation failed: {exc}"
            )

    async def _heartbeat(
        self,
        job: DurableJob,
        handler_task: asyncio.Task[Mapping[str, Any] | None],
    ) -> None:
        interval = min(max(self.store.lease_seconds / 3, 1.0), 2.0)
        while True:
            await asyncio.sleep(interval)
            renewed = await asyncio.to_thread(
                self.store.renew_lease,
                job.job_id,
                self.worker_id,
            )
            if not renewed:
                if not handler_task.done():
                    handler_task.cancel()
                return
