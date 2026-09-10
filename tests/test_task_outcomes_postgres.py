from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import os
import unittest
import uuid
from unittest.mock import patch

import nonebot

nonebot.init()

from src.plugins.ai_chat.subagents import SubAgentStore, TaskStep


@unittest.skipUnless(os.getenv("TEST_POSTGRES_DSN"), "isolated PostgreSQL is not configured")
class TaskOutcomePostgresTests(unittest.TestCase):
    def test_migration_evidence_and_concurrent_file_claim_survive_reopening(self):
        import psycopg
        from psycopg import sql
        from alembic import command
        from alembic.config import Config
        from src.bot_storage.database import PostgresDatabase

        dsn = os.environ["TEST_POSTGRES_DSN"]
        schema = "gaoji_outcome_test_" + uuid.uuid4().hex[:12]
        database = None
        try:
            with patch.dict(os.environ, AI_POSTGRES_DSN=dsn, AI_POSTGRES_SCHEMA=schema):
                command.upgrade(Config("alembic.ini"), "head")
            database = PostgresDatabase(dsn, schema=schema, min_size=1, max_size=8)
            store = SubAgentStore(database)
            task = store.create_task(scope_key="group:1", conversation_id="group:1:user:2",
                requester_user_id=2, trigger_message_id=None, objective="inspect", max_parallelism=1, max_steps=2)
            run = store.create_run(task.task_id, TaskStep("inspect", "operator", "inspect", "report"),
                                   allowed_tools=[], model_profile="test")
            receipt = store.record_evidence(task.task_id, run.run_id, "host_inspect", {"host_id": "h610"},
                                             {"ok": True, "hosts": []}, call_id="read-1")
            artifact = {"snapshot": "a" * 64, "size": 4, "handle": "sandbox#test/result.txt"}
            row = store.queue_file(task.task_id, artifact, "result.txt")
            with ThreadPoolExecutor(max_workers=6) as workers:
                claims = list(workers.map(lambda _: store.claim_file(task.task_id, row), range(6)))
            self.assertEqual(sum(claim is not None for claim in claims), 1)
            store.close()
            store = SubAgentStore(database)
            self.assertEqual(store.task_evidence(task.task_id)[0]["evidence_id"], receipt["ref"])
            self.assertEqual(store.deliveries(task.task_id)[0]["state"], "sending")
            self.assertIsNone(store.claim_file(task.task_id, row))
            claimed = next(value for value in claims if value is not None)
            store.settle_file_attempt(task.task_id, row, claimed, {"ok": True, "file_id": "verified-file"})
            self.assertEqual(store.deliveries(task.task_id)[0]["state"], "acknowledged")

            from tests.test_subagent_v2 import profile
            from src.plugins.ai_chat.agent import ContextPacket, DEFAULT_AGENT_REGISTRY
            from src.plugins.ai_chat.model_catalog import ModelCatalog
            from src.plugins.ai_chat.subagents import SubAgentCoordinator
            model = profile()
            coordinator = SubAgentCoordinator(store, ModelCatalog({"test": model}, default_profile="test"),
                logger=logging.getLogger("test-outcome"))
            store.update_control(task.task_id, expected_version=0, dispatch={"bot_id": "123", "profile": "test"})
            history = [{"role": "user", "content": "old correction instructions"}]
            context = ContextPacket("group:1", "group:1:user:2", 2, None, "inspect").for_agent(
                DEFAULT_AGENT_REGISTRY.worker("operator"), upstream={})
            store.save_run_context(task.task_id, run.run_id, context)
            store.save_agent_session(task.task_id, run.run_id, history,
                scope_key="group:1", requester_user_id=2, model_profile="test", expected_version=0)
            store.set_task_state(task.task_id, "partial")
            coordinator.revise(task.task_id, scope_key="group:1", requester_user_id=2,
                instruction="new inspection", step_keys=["inspect"], expected_version=1)
            store.close()
            store = SubAgentStore(database)
            session = store.agent_session(task.task_id, run.run_id, scope_key="group:1", requester_user_id=2)
            self.assertEqual(session["messages"], [])
            self.assertEqual(session["version"], 2)
            self.assertIsNone(store.run_context(run.run_id))
            archived = store.revision_checkpoints(task.task_id)[-1]["state"]["previous_sessions"][0]
            self.assertEqual(archived["session"]["messages"], history)
            self.assertEqual(archived["context"], context.as_payload())
            self.assertEqual(store.deliveries(task.task_id)[0]["state"], "acknowledged")
            with self.assertRaises(RuntimeError):
                store.save_agent_session(task.task_id, run.run_id, history,
                    scope_key="group:1", requester_user_id=2, model_profile="test", expected_version=1)
            store.close()
        finally:
            if database:
                database.close()
            with psycopg.connect(dsn) as connection:
                connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
