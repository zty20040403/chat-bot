from __future__ import annotations

import asyncio
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import nonebot

nonebot.init()

from src.plugins.ai_chat.storage.jobs import DurableJobStore
from src.plugins.ai_chat.workers.durable_jobs import DurableJobWorker


class RecordingLogger:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: object, *args, **kwargs) -> None:
        self.errors.append(str(message))

    def warning(self, message: object, *args, **kwargs) -> None:
        self.warnings.append(str(message))

    def info(self, message: object, *args, **kwargs) -> None:
        return None


class DurableJobStoreTests(unittest.TestCase):
    def test_idempotent_enqueue_claim_and_restart_recovery(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobs.sqlite3"
            store = DurableJobStore(path, lease_seconds=10)
            started_at = int(time.time())
            first, created = store.enqueue(
                kind="test.echo",
                idempotency_key="same-operation",
                payload={"value": 7},
                now=started_at,
            )
            duplicate, duplicate_created = store.enqueue(
                kind="test.echo",
                idempotency_key="same-operation",
                payload={"value": 99},
                now=started_at + 1,
            )
            self.assertTrue(created)
            self.assertFalse(duplicate_created)
            self.assertEqual(duplicate.job_id, first.job_id)
            self.assertEqual(duplicate.payload, {"value": 7})

            claimed = store.claim_due("worker-a", now=started_at)
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0].attempts, 1)
            store.close()

            restarted = DurableJobStore(path, lease_seconds=10)
            self.assertEqual(restarted.recovered_jobs, 0)
            self.assertEqual(
                restarted.recover_expired_leases(now=started_at + 10),
                1,
            )
            reclaimed = restarted.claim_due("worker-b", now=started_at + 10)
            self.assertEqual(len(reclaimed), 1)
            self.assertEqual(reclaimed[0].attempts, 2)
            self.assertTrue(
                restarted.mark_succeeded(
                    reclaimed[0].job_id,
                    "worker-b",
                    result={"ok": True},
                    now=started_at + 11,
                )
            )
            self.assertEqual(restarted.stats()["succeeded"], 1)
            restarted.close()

    def test_retry_limit_cancel_and_manual_requeue(self) -> None:
        store = DurableJobStore(":memory:", default_max_attempts=2)
        job, _ = store.enqueue(
            kind="test.fail",
            idempotency_key="failure",
            now=200,
        )
        first = store.claim_due("worker", now=200)[0]
        self.assertTrue(
            store.mark_failed(
                first.job_id,
                "worker",
                "temporary",
                retry_delay_seconds=0,
                now=200,
            )
        )
        second = store.claim_due("worker", now=200)[0]
        self.assertTrue(
            store.mark_failed(
                second.job_id,
                "worker",
                "permanent",
                now=201,
            )
        )
        self.assertEqual(store.stats()["failed"], 1)
        self.assertTrue(store.requeue(job.job_id, now=202))
        self.assertTrue(store.cancel(job.job_id, now=203))
        self.assertEqual(store.stats()["cancelled"], 1)
        store.close()


class DurableJobWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_handler_completes_job(self) -> None:
        store = DurableJobStore(":memory:")
        logger = RecordingLogger()
        worker = DurableJobWorker(
            store,
            logger=logger,
            concurrency=1,
            worker_id="test-worker",
        )
        seen: list[int] = []

        async def handle(job):
            await asyncio.sleep(0)
            seen.append(int(job.payload["value"]))
            return {"processed": True}

        worker.register("test.echo", handle)
        store.enqueue(
            kind="test.echo",
            idempotency_key="worker-operation",
            payload={"value": 42},
        )

        self.assertEqual(await worker.run_once(), 1)
        self.assertEqual(seen, [42])
        self.assertEqual(store.stats()["succeeded"], 1)
        self.assertFalse(logger.errors)
        store.close()

    async def test_running_job_can_be_cancelled_and_compensated(self) -> None:
        store = DurableJobStore(":memory:", lease_seconds=10)
        logger = RecordingLogger()
        worker = DurableJobWorker(
            store,
            logger=logger,
            concurrency=1,
            worker_id="test-worker",
        )
        started = asyncio.Event()
        compensated: list[str] = []

        async def handle(_job):
            started.set()
            await asyncio.Event().wait()

        async def compensate(_job, reason):
            compensated.append(reason)

        worker.register("test.long", handle, compensator=compensate)
        job, _ = store.enqueue(kind="test.long", idempotency_key="long")
        running = asyncio.create_task(worker.run_once())
        await started.wait()
        self.assertTrue(worker.cancel(job.job_id))
        await running

        self.assertEqual(store.get(job.job_id).status, "cancelled")
        self.assertEqual(compensated, ["cancelled"])
        store.close()

    async def test_job_timeout_is_terminal_and_compensated(self) -> None:
        store = DurableJobStore(":memory:")
        logger = RecordingLogger()
        worker = DurableJobWorker(store, logger=logger, worker_id="test-worker")
        compensated: list[str] = []

        async def handle(_job):
            await asyncio.sleep(1)

        worker.register(
            "test.timeout",
            handle,
            timeout_seconds=0.01,
            compensator=lambda _job, reason: compensated.append(reason),
        )
        job, _ = store.enqueue(kind="test.timeout", idempotency_key="timeout")
        await worker.run_once()

        self.assertEqual(store.get(job.job_id).status, "failed")
        self.assertEqual(compensated, ["timeout"])
        store.close()
