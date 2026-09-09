from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.cluster_control.execution_contracts import WorkerJobProposal, new_handle
from src.cluster_control.execution_storage import ClusterExecutionStore
from src.cluster_control.resource_policy import ResourcePolicyStore


@unittest.skipUnless(os.getenv("TEST_OPS_POSTGRES_DSN"), "isolated PostgreSQL test not configured")
class WorkerQueuePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from alembic import command
        from alembic.config import Config
        from src.bot_storage.database import PostgresDatabase

        cls.dsn = os.environ["TEST_OPS_POSTGRES_DSN"]
        cls.schema = "gaoji_queue_test_" + uuid.uuid4().hex[:12]
        cls.database = None
        cls.directory = tempfile.TemporaryDirectory()

        def clean():
            if cls.database is not None:
                cls.database.close()
            with psycopg.connect(cls.dsn) as conn:
                conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(cls.schema)))
            cls.directory.cleanup()

        cls.addClassCleanup(clean)
        with patch.dict(os.environ, AI_POSTGRES_DSN=cls.dsn, AI_POSTGRES_SCHEMA=cls.schema):
            command.upgrade(Config("alembic.ini"), "head")
        cls.database = PostgresDatabase(cls.dsn, schema=cls.schema, min_size=1, max_size=8)
        cls.store = ClusterExecutionStore(cls.database, Path(cls.directory.name))
        cls.policies = ResourcePolicyStore(cls.database)

    def setUp(self):
        connection = self.database.store_connection()
        try:
            connection.execute("TRUNCATE TABLE fleet_worker_jobs, fleet_workers CASCADE")
            connection.commit()
        finally:
            connection.close()
        for host, cpu in (("h310", 2000), ("h610", 2000), ("tank", 4000)):
            capacity = {"cpu_millis": cpu, "memory_bytes": cpu // 1000 * 1024**3, "gpu_slots": 0}
            self.store.upsert_worker(host + "-worker", {
                "boot_id": uuid.uuid4().hex, "protocol_version": 1, "availability": "available",
                "capabilities": ["probe.http"], "runtime": {"system": "linux", "machine": "x86_64"},
                "capacity": capacity,
            }, host_id=host)
            self.policies.ensure_worker_policy(host + "-worker", owner_actor_id="admin:kenneth", capacity=capacity)

    def submit(self, host="h610", **constraints):
        now = int(time.time())
        proposal = WorkerJobProposal.parse({
            "kind": "probe.http", "payload": {"target_id": host + "-worker"},
            "constraints": {"worker_id": host + "-worker", "cpu_millis": 50,
                "memory_bytes": 16 * 1024**2, **constraints},
            "idempotency_key": uuid.uuid4().hex,
        }, now=now)
        return self.store.submit_job({
            "job_id": new_handle("job"), "actor_id": "admin:kenneth", "origin_scope": "queue-test",
            "kind": proposal.kind, "payload": proposal.payload, "constraints": proposal.constraints,
            "deadline_at": proposal.deadline_at, "idempotency_key": proposal.idempotency_key,
            "created_at": now,
        })

    def claim(self, host="h610"):
        return self.store.claim_job(host + "-worker", host={"gpu_compute": False})

    def test_oversized_and_gpu_jobs_do_not_block_valid_work_on_any_host(self):
        for host, cpu in (("h310", 2000), ("h610", 2000), ("tank", 4000)):
            with self.subTest(host=host):
                blocked = [
                    (self.submit(host, cpu_millis=cpu + 1, priority="interactive"), "worker_cpu_millis_capacity"),
                    (self.submit(host, memory_bytes=cpu // 1000 * 1024**3 + 1,
                        priority="interactive"), "worker_memory_bytes_capacity"),
                    (self.submit(host, gpu_slots=1, priority="interactive"), "gpu_not_authorized"),
                ]
                small = self.submit(host)
                self.assertEqual(self.claim(host)["job_id"], small["job_id"])
                for job, reason in blocked:
                    current = self.store.get_job(job["job_id"])
                    self.assertEqual(current["attempt"], 0)
                    self.assertEqual(current["scheduler_reason"], reason)

    def test_owner_limit_does_not_block_a_smaller_job(self):
        self.policies.configure_worker_capacity("h610-worker", actor_id="admin:kenneth",
            allow_gpu=False, cpu_limit_millis=1000, memory_limit_bytes=2 * 1024**3,
            gpu_limit_slots=0, expected_version=1)
        big = self.submit(cpu_millis=1500, priority="interactive")
        small = self.submit(cpu_millis=500)
        self.assertEqual(self.claim()["job_id"], small["job_id"])
        self.assertEqual(self.store.get_job(big["job_id"])["scheduler_reason"], "owner_cpu_millis_capacity")

    def test_existing_reservations_are_counted_before_selecting_each_job(self):
        running = self.submit(cpu_millis=1500)
        self.assertEqual(self.claim()["job_id"], running["job_id"])
        big = self.submit(cpu_millis=600, priority="interactive")
        small = self.submit(cpu_millis=500)
        self.assertEqual(self.claim()["job_id"], small["job_id"])
        self.assertIsNone(self.claim())
        self.assertEqual(self.store.get_job(big["job_id"])["attempt"], 0)

    def test_candidates_beyond_first_page_are_not_starved(self):
        blocked = [self.submit(cpu_millis=2001, priority="interactive") for _ in range(51)]
        small = self.submit()
        self.assertEqual(self.claim()["job_id"], small["job_id"])
        for job in blocked:
            self.assertEqual(self.store.get_job(job["job_id"])["scheduler_reason"], "worker_cpu_millis_capacity")

    def test_other_worker_claim_does_not_overwrite_a_queue_reason(self):
        big = self.submit("h310", cpu_millis=2001)
        self.assertIsNone(self.claim("h310"))
        own = self.submit("h610")
        self.assertEqual(self.claim("h610")["job_id"], own["job_id"])
        self.assertEqual(self.store.get_job(big["job_id"])["scheduler_reason"], "worker_cpu_millis_capacity")

    def test_concurrent_claims_do_not_overreserve(self):
        for _ in range(3):
            self.submit(cpu_millis=1000)
        with ThreadPoolExecutor(max_workers=3) as pool:
            claims = list(pool.map(lambda _: self.claim(), range(3)))
        claimed = [job["job_id"] for job in claims if job is not None]
        self.assertEqual(len(claimed), 2)
        self.assertEqual(len(set(claimed)), 2)

    def test_completed_checkpoint_moves_across_three_hosts_without_reexecution(self):
        from src.cluster_worker.service import ClusterWorker

        now = int(time.time())
        job = self.submit("h310", worker_id="")
        first = self.store.claim_job("h310-worker", lease_seconds=15)
        result = {"ok": True, "status_code": 200, "target_id": "h310-worker"}
        checkpoint = self.store.save_checkpoint(job["job_id"], worker_id="h310-worker",
            fence=first["fence"], phase="completed", format_version=1,
            executor_version="worker-v2", state={"result": result})
        worker = object.__new__(ClusterWorker)
        worker._probe_http = AsyncMock(side_effect=AssertionError("must reuse committed result"))
        previous = first
        for index, host in enumerate(("h610", "tank"), start=1):
            with self.subTest(host=host), patch("src.cluster_control.execution_storage.time.time",
                    return_value=now + 16 * index):
                claimed = self.store.claim_job(host + "-worker", lease_seconds=15)
                self.assertEqual(claimed["job_id"], job["job_id"])
                self.assertEqual(claimed["fence"], previous["fence"] + 1)
                self.assertEqual(claimed["resume_checkpoint"]["checkpoint_id"], checkpoint["checkpoint_id"])
                self.assertEqual(asyncio.run(worker._execute(claimed)), result)
                with self.assertRaisesRegex(PermissionError, "stale worker receipt"):
                    self.store.complete_job(job["job_id"], worker_id=previous["worker_id"],
                        fence=previous["fence"], ok=True, result={"wrong": True})
                previous = claimed
        worker._probe_http.assert_not_called()
        completed = self.store.complete_job(job["job_id"], worker_id="tank-worker",
            fence=previous["fence"], ok=True, result=result)
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["result"], result)
