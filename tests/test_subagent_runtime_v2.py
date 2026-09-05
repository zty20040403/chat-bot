from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from tests.test_subagent_v2 import decision, profile
from src.plugins.ai_chat.agent import ContextPacket
from src.plugins.ai_chat.agent.control import JobFence, LeaseLost, active_job_fence, active_model_policy
from src.plugins.ai_chat.agent.execution import EntryDecision, active_agent_step
from src.plugins.ai_chat.agent.model_routing import choose_agent_profile, model_scope_for_role, validate_model_policy
from src.plugins.ai_chat.agent.scheduling import SpecialistScheduler
from src.plugins.ai_chat.agent.workspaces import ArtifactCaptureError, StepWorkspaces
from src.plugins.ai_chat.agent_tools import AgentToolExecutor
from src.plugins.ai_chat.llm_gateway import LLMGateway
from src.plugins.ai_chat.model_catalog import ModelCatalog
from src.plugins.ai_chat.storage.jobs import DurableJobStore
from src.plugins.ai_chat.subagents import (
    AgentExecutionHooks,
    SubAgentCoordinator,
    SubAgentStore,
    TaskStep,
    StepOutcome,
    _delivery_outcomes,
    _only_deferred_delivery_unresolved,
    _settled_task_status,
)
from src.plugins.ai_chat.deepseek import DeepSeekTrace


class RuntimeV2Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SubAgentStore(Path(self.tmp.name) / "agents.sqlite3")
        self.catalog = ModelCatalog({n: profile(n) for n in ("qwen-local", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")}, default_profile="qwen-local")
        self.coordinator = SubAgentCoordinator(self.store, self.catalog, logger=Mock())
        self.packet = ContextPacket("group:1", "group:1:user:2", 2, 3, "实现商城")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def submit(self):
        return self.coordinator.submit(packet=self.packet, decision=EntryDecision.parse(decision("workflow")),
            dispatch={"bot_id": "123", "event": {"user_id": 2}, "profile": "qwen-local"})

    async def test_queued_plan_reuses_entry_and_can_survive_reopen(self):
        task = self.submit()
        self.assertEqual(task.status, "queued")
        self.assertEqual(self.store.dispatchable_tasks(), [task.task_id])
        self.store.close()
        self.store = SubAgentStore(Path(self.tmp.name) / "agents.sqlite3")
        self.coordinator.store = self.store
        with patch.object(self.coordinator, "_execute_workflow", new=AsyncMock(return_value="done")) as execute, patch("src.plugins.ai_chat.subagents.ask_deepseek_json", new=AsyncMock()) as planner:
            await self.coordinator.resume(task.task_id, scope_key="group:1", requester_user_id=2,
                selected_profile=self.catalog.default, tools=[], execute_tool=AsyncMock())
        planner.assert_not_awaited()
        self.assertEqual(len(execute.call_args.kwargs["steps"]), 3)

    async def test_revision_is_scoped_versioned_and_only_invalidates_descendants(self):
        task = self.submit()
        for key, deps in (("frontend", ()), ("backend", ()), ("integration", ("frontend", "backend"))):
            run = self.store.create_run(task.task_id, TaskStep(key, "coder", key, "file", deps), allowed_tools=[], model_profile="qwen-local")
            self.store.finish_run(run.run_id, "succeeded", result={"status": "success", "summary": key})
        self.store.set_task_state(task.task_id, "completed")
        with self.assertRaises(ValueError):
            self.coordinator.revise(task.task_id, scope_key="group:2", requester_user_id=2, instruction="改颜色", step_keys=["frontend"], expected_version=1)
        with self.assertRaises(ValueError):
            self.coordinator.revise(task.task_id, scope_key="group:1", requester_user_id=2, instruction="改颜色", step_keys=["frontend"], expected_version=0)
        self.assertEqual(self.store.get(task.task_id).status, "completed")
        control = self.coordinator.revise(task.task_id, scope_key="group:1", requester_user_id=2, instruction="改颜色", step_keys=["frontend"], expected_version=1)
        self.assertEqual(control["revision"], 2)
        self.assertEqual({r.step_key: r.status for r in self.store.runs(task.task_id)}, {"frontend": "pending", "backend": "succeeded", "integration": "pending"})
        checkpoint = self.store.checkpoints(task.task_id)[-1]["state"]
        self.assertEqual(checkpoint["previous_runs"][0]["result"]["summary"], "frontend")

    async def test_lost_lease_fences_state_writes(self):
        task = self.submit()
        token = active_job_fence.set(JobFence(1, "worker", 1, lambda: False))
        try:
            with self.assertRaises(LeaseLost):
                self.store.set_task_state(task.task_id, "completed")
        finally:
            active_job_fence.reset(token)
        self.assertEqual(self.store.get(task.task_id).status, "queued")

    async def test_resumed_step_sees_latest_upstream_and_own_previous_snapshot(self):
        task = self.submit()
        step = TaskStep("code", "coder", "new color", "code")
        run = self.store.create_run(task.task_id, step, allowed_tools=[], model_profile="qwen-local")
        self.store.save_run_context(task.task_id, run.run_id, self.packet.for_agent(self.coordinator.registry.worker("coder"), upstream={}))
        self.store.save_agent_session(task.task_id, run.run_id, [{"role": "assistant", "content": "old work"}],
            scope_key=task.scope_key, requester_user_id=task.requester_user_id, model_profile="qwen-local", expected_version=0)
        self.store.append_checkpoint(task.task_id, "revision_requested", {"previous_runs": [
            {"run_id": run.run_id, "result": {"summary": "old snapshot", "artifacts": [{"name": "old.zip"}]}}]})
        async def worker(text, history, *_args, **_kwargs):
            self.assertIn("new upstream result", text)
            self.assertIn("previous_version", text)
            self.assertIn("old snapshot", text)
            self.assertIn("旧容器已清理", text)
            self.assertEqual(history[0]["content"], "old work")
            return '{"status":"success","summary":"updated"}'
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools", side_effect=worker):
            outcome = await self.coordinator._run_step(task, step, run, context=self.packet,
                upstream={"research": {"summary": "new upstream result"}}, selected_profile=self.catalog.default,
                tools_by_name={}, execute_tool=AsyncMock())
        self.assertEqual(outcome.state, "success")

    async def test_resume_requires_tool_result_and_covered_event_sequence(self):
        task = self.submit()
        run = self.store.create_run(task.task_id, TaskStep("code", "coder", "write", "code"), allowed_tools=[], model_profile="qwen-local")
        event = {"idempotency": "non-idempotent", "call_id": "call1", "tool_name": "sandbox_create"}
        self.store.append_event(task.task_id, "agent.tool_started", event, run_id=run.run_id)
        self.assertFalse(self.store.run_resume_safe(run.run_id))
        self.store.append_event(task.task_id, "agent.tool_finished", {**event, "state": "succeeded"}, run_id=run.run_id)
        self.assertFalse(self.store.run_resume_safe(run.run_id))
        self.store.save_agent_session(task.task_id, run.run_id, [{"role": "tool", "tool_call_id": "call1", "content": "created"}],
            scope_key=task.scope_key, requester_user_id=task.requester_user_id, model_profile="qwen-local", expected_version=0)
        self.assertTrue(self.store.run_resume_safe(run.run_id))
        self.store.append_event(task.task_id, "agent.tool_started", event, run_id=run.run_id)
        self.store.append_event(task.task_id, "agent.tool_finished", {**event, "state": "succeeded"}, run_id=run.run_id)
        self.assertFalse(self.store.run_resume_safe(run.run_id), "A reused provider call id must not acknowledge a newer execution")

    async def test_committed_conversation_progress_is_safe_to_resume(self):
        task = self.submit()
        run = self.store.create_run(task.task_id, TaskStep("code", "coder", "write", "code"), allowed_tools=[], model_profile="qwen-local")
        event = {"idempotency": "non-idempotent", "call_id": "say1", "tool_name": "say"}
        self.store.append_event(task.task_id, "agent.tool_started", event, run_id=run.run_id)
        self.store.append_event(task.task_id, "agent.tool_finished", {**event, "state": "committed"}, run_id=run.run_id)
        self.store.save_agent_session(task.task_id, run.run_id, [
            {"role": "tool", "tool_call_id": "say1", "content": "sent"},
        ], scope_key=task.scope_key, requester_user_id=task.requester_user_id,
            model_profile="qwen-local", expected_version=0)
        self.assertTrue(self.store.run_resume_safe(run.run_id))

    async def test_cancel_queued_task_does_not_resurrect(self):
        task = self.submit()
        self.assertTrue(self.coordinator.cancel(task.task_id))
        self.assertEqual(self.store.get(task.task_id).status, "cancelled")
        self.assertEqual(self.store.dispatchable_tasks(), [])

    async def test_locked_model_cannot_fallback_even_to_non_sol(self):
        policy = validate_model_policy({"mode": "locked", "profile": "gpt-5.6-luna"}, self.catalog)
        token = active_model_policy.set(policy)
        try:
            chosen = choose_agent_profile("coder", self.catalog.default, self.catalog, {})
            with model_scope_for_role("coder", chosen, self.catalog, {}):
                candidates = LLMGateway(catalog=self.catalog)._completion_candidates(chosen, {})
            self.assertEqual([p.name for p in candidates], ["gpt-5.6-luna"])
        finally:
            active_model_policy.reset(token)
        with self.assertRaises(ValueError):
            validate_model_policy({"mode": "locked", "profile": "invented"}, self.catalog)

    async def test_delivery_unknown_never_blindly_reuploads(self):
        task = self.submit()
        self.store.set_task_state(task.task_id, "running", plan={"contract": {"delivery_required": True}})
        step = TaskStep("files", "document", "write", "pdf")
        run = self.store.create_run(task.task_id, step, allowed_tools=[], model_profile="qwen-local")
        outcome = StepOutcome(step, run, {"status": "success", "artifacts": [{"handle": "s123abc:/workspace/out.pdf", "name": "out.pdf"}]}, DeepSeekTrace(), "success")
        execute = AsyncMock(side_effect=TimeoutError("receipt unknown"))
        for _ in range(2):
            result = await self.coordinator._deliver_requested_artifacts(task, {"files": outcome}, execute_tool=execute, delivered_artifacts=set(), progress=None)
            self.assertFalse(result[0]["ok"])
        self.assertEqual(execute.await_count, 1)
        self.assertEqual(self.store.deliveries(task.task_id)[0]["state"], "unknown")

    async def test_delivery_prefers_final_or_repair_artifact(self):
        task = self.submit()
        design_run = self.store.create_run(task.task_id, TaskStep("design", "analyst", "design", "plan"), allowed_tools=[], model_profile="qwen-local")
        repair_run = self.store.create_run(task.task_id, TaskStep("design__repair_1", "coder", "repair", "zip"), allowed_tools=[], model_profile="qwen-local")
        design = StepOutcome(TaskStep("design", "analyst", "design", "plan"), design_run,
            {"status": "success", "artifacts": [{"handle": "s111111:/workspace/plan.md"}]}, DeepSeekTrace(), "success")
        repair = StepOutcome(TaskStep("design__repair_1", "coder", "repair", "zip"), repair_run,
            {"status": "success", "artifacts": [{"handle": "s222222:/workspace/app.zip"}]}, DeepSeekTrace(), "success")
        selected = _delivery_outcomes(task, {"design": design, "design__repair_1": repair})
        self.assertEqual([item.step.key for item in selected], ["design__repair_1"])

    async def test_delivery_only_acceptance_gap_does_not_block_upload(self):
        self.assertTrue(_only_deferred_delivery_unresolved({
            "unresolved": ["将压缩包发送到当前 QQ 群并确认群文件"],
        }))
        self.assertFalse(_only_deferred_delivery_unresolved({
            "unresolved": ["还没有运行集成测试", "还没有发到群里"],
        }))

    async def test_successful_repair_and_delivery_settle_task_as_completed(self):
        task = self.submit()
        failed_run = self.store.create_run(task.task_id, TaskStep("frontend", "coder", "front", "code"), allowed_tools=[], model_profile="qwen-local")
        repair_run = self.store.create_run(task.task_id, TaskStep("frontend__repair_1", "coder", "repair", "zip"), allowed_tools=[], model_profile="qwen-local")
        failed = StepOutcome(TaskStep("frontend", "coder", "front", "code"), failed_run,
            {"status": "failed"}, DeepSeekTrace(), "failed")
        repair = StepOutcome(TaskStep("frontend__repair_1", "coder", "repair", "zip"), repair_run,
            {"status": "success"}, DeepSeekTrace(), "success")
        status = _settled_task_status(
            [failed, repair], [{"ok": True}], {"status": "passed"},
        )
        self.assertEqual(status, "completed")

    async def test_old_delivery_receipt_does_not_change_new_revision(self):
        task = self.submit()
        self.store.begin_delivery(task.task_id, "file", {"filename": "old.pdf"})
        self.store.update_control(task.task_id, expected_version=1, revision=2)
        self.store.begin_delivery(task.task_id, "file", {"filename": "new.pdf"})
        self.store.finish_delivery(task.task_id, "file", "acknowledged", {"filename": "old.pdf"}, revision=1)
        self.assertEqual({d["revision"]: d["state"] for d in self.store.deliveries(task.task_id)}, {1: "acknowledged", 2: "sending"})

    async def test_exhausted_background_job_settles_task(self):
        from src.plugins.ai_chat.agent.background import SubAgentDispatcher
        task = self.submit()
        jobs = DurableJobStore(Path(self.tmp.name) / "exhausted.sqlite3")
        try:
            services = SimpleNamespace(context=SimpleNamespace(subagent_store=self.store, job_store=jobs,
                subagent_coordinator=self.coordinator, logger=Mock()))
            dispatcher = SubAgentDispatcher(services)
            dispatcher.enqueue(task.task_id)
            job = jobs.claim_due("worker", kinds=(dispatcher.kind,))[0]
            await dispatcher.settle_failed_dispatch(job, "failed")
            self.assertEqual(self.store.get(task.task_id).status, "failed")
            self.assertEqual(self.store.dispatchable_tasks(), [])
        finally:
            jobs.close()

    async def test_kind_filter_does_not_steal_workflows(self):
        jobs = DurableJobStore(Path(self.tmp.name) / "jobs.sqlite3")
        try:
            jobs.enqueue(kind="subagent.workflow", idempotency_key="task1")
            jobs.enqueue(kind="historian", idempotency_key="history1")
            claimed = jobs.claim_due("worker", kinds=("historian",))
            self.assertEqual([job.kind for job in claimed], ["historian"])
            self.assertEqual(jobs.claim_due("other", kinds=()), [])
        finally:
            jobs.close()

    async def test_admin_models_are_versioned_and_dispatch_is_not_exposed(self):
        import httpx
        from fastapi import FastAPI
        from src.plugins.ai_chat.admin import AdminServices, register_admin
        task = self.submit()
        app = FastAPI()
        register_admin(app, AdminServices(version="test", started_at=1, subagent_store=self.store,
            subagent_coordinator=self.coordinator, model_catalog=self.catalog), token="test-token")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": "Bearer test-token", "If-Match": '"0"'}
            response = await client.get(f"/bot-admin/api/subagents/{task.task_id}", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("dispatch", response.json()["control"])
            body = {"expected_version": 1, "policy": {"mode": "locked", "profile": "gpt-5.6-luna"}}
            response = await client.put(f"/bot-admin/api/subagents/{task.task_id}/models", headers=headers, json=body)
            self.assertEqual(response.status_code, 200, response.text)
            stale = await client.put(f"/bot-admin/api/subagents/{task.task_id}/models", headers=headers, json=body)
            self.assertEqual(stale.status_code, 409)

    async def test_background_restores_event_and_enqueues_idempotent_final(self):
        from nonebot.adapters.onebot.v11 import GroupMessageEvent
        from src.plugins.ai_chat.agent.background import SubAgentDispatcher
        from src.plugins.ai_chat.delivery import DeliveryStore
        from src.plugins.ai_chat.onebot_codec import scope_from_event
        event = GroupMessageEvent(time=int(time.time()), self_id=123, post_type="message", message_type="group", sub_type="normal",
            message_id=42, group_id=100, user_id=2, message=[{"type": "text", "data": {"text": "实现商城"}}], raw_message="实现商城", font=0,
            sender={"user_id": 2, "nickname": "Test", "role": "member"})
        packet = ContextPacket(scope_from_event(event).key, "group:100:user:2", 2, 3, "实现商城")
        task = self.coordinator.submit(packet=packet, decision=EntryDecision.parse(decision("workflow")),
            dispatch={"event": event.model_dump(mode="json"), "bot_id": "123", "profile": "qwen-local"})
        jobs = DurableJobStore(Path(self.tmp.name) / "background.sqlite3")
        delivery = DeliveryStore(Path(self.tmp.name) / "delivery.sqlite3")
        try:
            async def execute(_bot, restored, _objective, **kwargs):
                self.assertEqual(restored.group_id, 100)
                self.assertEqual(kwargs['resume_task_id'], task.task_id)
                self.store.set_task_state(task.task_id, "completed", result={"answer": "完成"})
            services = SimpleNamespace(context=SimpleNamespace(subagent_store=self.store, job_store=jobs,
                subagent_coordinator=self.coordinator, logger=Mock(), delivery_store=delivery),
                group_enabled=lambda _: True, tools=SimpleNamespace(_ask_ai=execute))
            dispatcher = SubAgentDispatcher(services)
            dispatcher.enqueue(task.task_id)
            job = jobs.claim_due("worker", kinds=(dispatcher.kind,))[0]
            with patch("src.plugins.ai_chat.agent.background.get_bot", return_value=Mock()):
                first = await dispatcher.execute(job)
                second = await dispatcher.execute(job)
            self.assertEqual(first["delivery_id"], second["delivery_id"])
        finally:
            jobs.close(); delivery.close()

    async def test_snapshot_immutable_and_upstream_scope_enforced(self):
        executor = Mock(owner="owner")
        executor.sandbox_manager.export_artifact = AsyncMock(return_value=(b"artifact-v1", False))
        executor.sandbox_manager.install_readonly_file = AsyncMock()
        workspaces = StepWorkspaces(Path(self.tmp.name), executor)
        artifact = (await workspaces.capture(1, [{"handle": "s123abc:/workspace/code.zip"}]))[0]
        self.assertEqual(executor.sandbox_manager.export_artifact.await_args.args[2], "code.zip")
        await workspaces.import_artifact(1, {"code": {"artifacts": [artifact]}}, {"step_id": "code", "artifact_index": 0, "sandbox_id": "s456abc"})
        self.assertEqual(executor.sandbox_manager.install_readonly_file.await_args.args[2], f"upstream/{artifact['snapshot']}/code.zip")
        with self.assertRaises(ValueError):
            await workspaces.import_artifact(1, {"code": {"artifacts": [artifact]}}, {"step_id": "other", "artifact_index": 0, "sandbox_id": "s456abc"})
        with self.assertRaises(FileNotFoundError):
            await workspaces.import_artifact(2, {"code": {"artifacts": [artifact]}}, {"step_id": "code", "artifact_index": 0, "sandbox_id": "s456abc"})

    async def test_directory_artifact_is_exported_as_zip(self):
        executor = Mock(owner="owner")
        executor.sandbox_manager.export_artifact = AsyncMock(return_value=(b"PK-directory", True))
        workspaces = StepWorkspaces(Path(self.tmp.name), executor)
        artifact = (await workspaces.capture(1, [{
            "handle": "s123abc:/workspace/source", "kind": "directory", "name": "source",
        }]))[0]
        self.assertEqual(artifact["name"], "source.zip")
        self.assertEqual(artifact["kind"], "file")
        self.assertEqual(artifact["source_kind"], "directory")

    async def test_capture_preserves_valid_artifacts_when_another_path_is_bad(self):
        executor = Mock(owner="owner")
        executor.sandbox_manager.export_artifact = AsyncMock(side_effect=[
            (b"valid", False), FileNotFoundError("missing"),
        ])
        workspaces = StepWorkspaces(Path(self.tmp.name), executor)
        with self.assertRaises(ArtifactCaptureError) as raised:
            await workspaces.capture(1, [
                {"handle": "s123abc:/workspace/result.zip", "name": "result.zip"},
                {"handle": "s123abc:/workspace/missing.txt", "name": "missing.txt"},
            ])
        self.assertEqual([item["name"] for item in raised.exception.captured], ["result.zip"])

    async def test_worker_keeps_captured_file_when_sibling_artifact_is_invalid(self):
        task = self.submit()
        step = TaskStep("files", "coder", "build", "zip")
        run = self.store.create_run(task.task_id, step, allowed_tools=[], model_profile="qwen-local")
        captured = [{"handle": "s123abc:/workspace/result.zip", "name": "result.zip", "snapshot": "a" * 64}]
        workspaces = Mock()
        workspaces.capture = AsyncMock(side_effect=ArtifactCaptureError(captured, ["missing.txt: missing"]))
        answer = json.dumps({
            "status": "success", "summary": "built",
            "artifacts": [
                {"handle": "s123abc:/workspace/result.zip", "name": "result.zip"},
                {"handle": "s123abc:/workspace/missing.txt", "name": "missing.txt"},
            ],
        })
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools", new=AsyncMock(return_value=answer)):
            outcome = await self.coordinator._run_step(
                task, step, run, context=self.packet, upstream={},
                selected_profile=self.catalog.default, tools_by_name={},
                execute_tool=AsyncMock(), hooks=AgentExecutionHooks(workspaces=workspaces),
            )
        self.assertEqual(outcome.state, "partial")
        self.assertEqual(outcome.result["artifacts"], captured)
        self.assertIn("missing.txt", outcome.result["warnings"][0])

    async def test_validation_and_delivery_use_manager_relative_paths(self):
        from src.plugins.ai_chat.sandbox import DockerSandboxManager
        executor = Mock(owner="owner")
        manager = executor.sandbox_manager
        manager.create = AsyncMock(return_value={"sandbox_id": "s123abc"})
        manager.destroy = AsyncMock()
        checked = []
        async def write(_owner, _sid, path, _content, **_kwargs):
            DockerSandboxManager()._workspace_path(path)
            checked.append(path)
        manager.write_file = AsyncMock(side_effect=write)
        executor._send_file_from_sandbox = AsyncMock(return_value='{"ok":true}')
        workspaces = StepWorkspaces(Path(self.tmp.name), executor)
        artifact = {"name": "result.txt", "snapshot": workspaces._persist(1, b"test")}
        self.assertTrue((await workspaces.validate(1, artifact))["ok"])
        await workspaces.deliver(1, artifact)
        self.assertEqual(checked, ["acceptance.txt", "result.txt"])
        self.assertEqual(executor._send_file_from_sandbox.await_args.args[0]["path"], "result.txt")

    async def test_scheduler_cancellation_releases_slot_and_avoids_group_head_of_line(self):
        scheduler = SpecialistScheduler(total=2, per_group=1, per_model=2)
        async with scheduler.slot("group:1", "luna"):
            waiting = asyncio.create_task(scheduler.slot("group:1", "luna").__aenter__())
            await asyncio.sleep(0)
            async with asyncio.timeout(1), scheduler.slot("group:2", "luna"):
                self.assertEqual(scheduler.snapshot()["active"], 2)
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
        self.assertEqual(scheduler.snapshot()["active"], 0)
        self.assertEqual(scheduler.snapshot()["waiting"], 0)

    async def test_workspace_owner_is_context_local_not_shared_mutation(self):
        executor = object.__new__(AgentToolExecutor)
        executor._owner = "group:1:user:2"
        async def owner(key):
            token = active_agent_step.set(key)
            try:
                await asyncio.sleep(0)
                return executor.owner
            finally:
                active_agent_step.reset(token)
        values = await asyncio.gather(owner("task#1/front"), owner("task#1/back"))
        self.assertNotEqual(*values)
        self.assertEqual(executor.owner, "group:1:user:2")


if __name__ == "__main__":
    unittest.main()
