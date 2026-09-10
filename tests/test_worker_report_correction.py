from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.test_subagent_v2 import profile
from src.plugins.ai_chat.agent import DEFAULT_AGENT_REGISTRY
from src.plugins.ai_chat.agent import ContextPacket
from src.plugins.ai_chat.agent.control import LeaseLost
from src.plugins.ai_chat.agent.worker_report import (
    checked_report, retain_execution_facts, separate_cluster_artifacts,
)
from src.plugins.ai_chat.deepseek import DeepSeekTrace
from src.plugins.ai_chat.model_catalog import ModelCatalog
from src.plugins.ai_chat.subagents import SubAgentCoordinator, SubAgentStore, TaskStep


def report(ref="evidence#old"):
    return {"status": "success", "summary": "inspected", "facts": [],
            "findings": [{"description": "host is online", "evidence_refs": [ref]}],
            "completed": [], "authorization": [], "next_verification": [],
            "warnings": [], "unresolved": [], "handoff": [],
            "artifacts": [{"handle": "s123abc:/workspace/report.md", "name": "report.md"}]}


class ReportCorrectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = SubAgentStore(":memory:")
        self.profile = profile()
        self.coordinator = SubAgentCoordinator(self.store,
            ModelCatalog({"test": self.profile}, default_profile="test"), logger=Mock())
        self.task = self.store.create_task(scope_key="group:1", conversation_id="group:1:user:2",
            requester_user_id=2, trigger_message_id=3, objective="inspect", max_parallelism=1, max_steps=4)
        self.store.set_task_state(self.task.task_id, "running", plan={"contract": {"version": 2}})
        self.run = self.store.create_run(self.task.task_id,
            TaskStep("inspect", "operator", "inspect", "report"), allowed_tools=[], model_profile="test")
        self.ref = self.store.record_evidence(self.task.task_id, self.run.run_id, "host_inspect",
            {"host_id": "h610"}, {"ok": True, "observed_at": 1000, "host": "h610"})["ref"]
        self.saved = Mock()

    def tearDown(self):
        self.store.close()

    async def correct(self, original=None):
        return await self.coordinator._correct_worker_report(self.task, self.run, original or report(),
            evidence=self.store.task_evidence(self.task.task_id),
            spec=DEFAULT_AGENT_REGISTRY.worker("operator"), profile=self.profile,
            trace=DeepSeekTrace(), save_transcript=self.saved, event_sink=AsyncMock())

    async def valid_correction(self, text, history, tools, execute, **kwargs):
        await execute("read_task_evidence", {"ref": self.ref})
        return json.dumps(report(self.ref))

    async def test_correction_reads_current_evidence_only_and_preserves_transcript(self):
        history = [{"role": "user", "content": "previous independent work"}]
        self.store.save_agent_session(self.task.task_id, self.run.run_id, history,
            scope_key=self.task.scope_key, requester_user_id=2, model_profile="test", expected_version=0)

        async def model(text, history, tools, execute, **kw):
            self.assertEqual(history, [])
            self.assertEqual([t["function"]["name"] for t in tools], ["read_task_evidence"])
            self.assertIn(self.ref, text)
            for name in ("ops_call", "sandbox_exec", "send_file_from_sandbox"):
                self.assertFalse(json.loads(await execute(name, {}))["ok"])
            self.assertFalse(json.loads(await execute("read_task_evidence", {"ref": "evidence#foreign"}))["ok"])
            self.assertTrue(json.loads(await execute("read_task_evidence", {"ref": self.ref}))["ok"])
            kw["transcript_sink"]([{"role": "assistant", "content": "corrected"}])
            return json.dumps(report(self.ref))

        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools", side_effect=model) as call:
            result = await self.correct()
        self.assertEqual(result["report_validation"]["status"], "passed")
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.saved.call_args.args[0][0], history[0])
        self.assertEqual(call.call_count, 1)

    async def test_valid_report_costs_no_extra_model_call(self):
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools") as call:
            result = await self.correct(report(self.ref))
        call.assert_not_called()
        self.assertEqual(result["report_validation"]["status"], "passed")

    async def test_step_corrects_claims_then_captures_real_file(self):
        upload = "artifact_" + "a" * 32
        self.store.record_evidence(self.task.task_id, self.run.run_id, "cluster_artifact_upload", {},
            {"artifact_id": upload, "status": "staged", "sha256": "b" * 64, "size_bytes": 12})
        original = report()
        original["artifacts"].append({"handle": upload, "kind": "cluster_artifact"})
        capture = AsyncMock(side_effect=lambda _task, artifacts: [
            {**item, "snapshot": "c" * 64, "size": 12} for item in artifacts])
        hooks = SimpleNamespace(workspaces=SimpleNamespace(capture=capture), approval_checker=None,
            handoff_tool=None, compensate_tool=None)
        async def worker(text, history, tools, execute, **kwargs):
            if "[只读交付纠错]" in text:
                return await self.valid_correction(text, history, tools, execute, **kwargs)
            return json.dumps(original)

        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools", side_effect=worker) as call:
            result = await self.coordinator._run_step(self.task,
                TaskStep("inspect", "operator", "inspect", "report"), self.run,
                context=ContextPacket(self.task.scope_key, self.task.conversation_id, 2, 3, "inspect"),
                upstream={}, selected_profile=self.profile, tools_by_name={},
                execute_tool=AsyncMock(), hooks=hooks)
        self.assertEqual(call.await_count, 2)
        self.assertEqual(result.state, "success")
        self.assertEqual(len(capture.call_args.args[1]), 1)
        self.assertEqual(result.result["artifacts"][0]["snapshot"], "c" * 64)
        self.assertEqual(result.result["metadata"]["cluster_artifact_refs"][0]["handle"], upload)
        self.assertEqual(self.store.runs(self.task.task_id)[0].status, "succeeded")

    async def test_completed_correction_is_reused_after_resume(self):
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools",
                   side_effect=self.valid_correction) as call:
            first = await self.correct()
            second = await self.correct()
        self.assertEqual(call.await_count, 1)
        self.assertEqual(first, second)

    async def test_real_step_resume_never_reopens_execution_after_correction_checkpoint(self):
        for status in ("started", "completed", "failed"):
            with self.subTest(status=status):
                with self.store._transaction() as cursor:
                    cursor.execute("DELETE FROM subagent_checkpoints WHERE task_id=?", (self.task.task_id,))
                with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools",
                           side_effect=self.valid_correction):
                    await self.correct()
                if status != "completed":
                    self.store.append_checkpoint(self.task.task_id, "report_correction",
                        {"status": status, "fingerprint": "prior", "original": report()}, run_id=self.run.run_id)
                execute = AsyncMock()
                with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools") as call:
                    outcome = await self.coordinator._run_step(self.task,
                        TaskStep("inspect", "operator", "inspect", "report"), self.run,
                        context=ContextPacket(self.task.scope_key, self.task.conversation_id, 2, 3, "inspect"),
                        upstream={}, selected_profile=self.profile, tools_by_name={}, execute_tool=execute)
                call.assert_not_called()
                execute.assert_not_called()
                self.assertEqual(outcome.result["artifacts"], report()["artifacts"])
                self.assertEqual(outcome.state, "success" if status == "completed" else "partial")

    async def test_replacing_reference_without_reading_cannot_pass(self):
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools",
                   new=AsyncMock(return_value=json.dumps(report(self.ref)))):
            result = await self.correct()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["report_validation"]["status"], "incomplete")

    async def test_partial_or_out_of_order_evidence_read_cannot_pass(self):
        large_ref = self.store.record_evidence(self.task.task_id, self.run.run_id,
            "host_inspect", {}, {"content": "x" * 15000})["ref"]
        async def model(text, history, tools, execute, **kwargs):
            first = json.loads(await execute("read_task_evidence", {"ref": large_ref}))
            self.assertIsNotNone(first["next_offset"])
            await execute("read_task_evidence", {"ref": large_ref, "offset": 15010})
            return json.dumps(report(large_ref))
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools", side_effect=model):
            result = await self.correct()
        self.assertEqual(result["report_validation"]["status"], "incomplete")

    async def test_complete_paginated_evidence_read_passes(self):
        large_ref = self.store.record_evidence(self.task.task_id, self.run.run_id,
            "host_inspect", {}, {"content": "x" * 15000})["ref"]
        async def model(text, history, tools, execute, **kwargs):
            offset = 0
            while offset is not None:
                payload = json.loads(await execute("read_task_evidence", {"ref": large_ref, "offset": offset}))
                offset = payload["next_offset"]
            return json.dumps(report(large_ref))
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools", side_effect=model):
            result = await self.correct()
        self.assertEqual(result["report_validation"]["status"], "passed")

    async def test_failed_correction_remains_partial_without_retry_loop(self):
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools",
                   new=AsyncMock(side_effect=TimeoutError("unavailable"))) as call:
            first = await self.correct()
            second = await self.correct()
        self.assertEqual(call.await_count, 1)
        self.assertEqual(first["status"], "partial")
        self.assertEqual(second["report_validation"]["status"], "incomplete")

    async def test_still_invalid_correction_does_not_pass_after_resume(self):
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools",
                   new=AsyncMock(return_value=json.dumps(report("evidence#still-invalid")))) as call:
            first = await self.correct()
            second = await self.correct()
        self.assertEqual(call.await_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(second["report_validation"]["status"], "incomplete")

    async def test_started_correction_does_not_restart_on_unknown_outcome(self):
        self.store.append_checkpoint(self.task.task_id, "report_correction",
            {"status": "started", "fingerprint": "prior"}, run_id=self.run.run_id)
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools") as call:
            result = await self.correct()
        call.assert_not_called()
        self.assertEqual(result["status"], "partial")

    async def test_revision_change_cannot_publish_correction(self):
        async def model(*args, **kw):
            self.store.update_control(self.task.task_id, expected_version=0, revision=2)
            return json.dumps(report(self.ref))
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools", side_effect=model):
            with self.assertRaises(LeaseLost):
                await self.correct()
        self.assertEqual(self.store.latest_run_checkpoint(self.task.task_id, self.run.run_id,
            "report_correction")["status"], "started")

    async def test_cancellation_escapes_correction(self):
        async def model(*args, **kw):
            raise asyncio.CancelledError
        with patch("src.plugins.ai_chat.subagents.ask_deepseek_with_tools", side_effect=model):
            with self.assertRaises(asyncio.CancelledError):
                await self.correct()

    def test_readonly_correction_cannot_hide_execution_gaps_or_change_files(self):
        original = report()
        original.update(status="partial", unresolved=["scan timed out"], warnings=["limited data"])
        corrected = report(self.ref)
        corrected["artifacts"] = [{"handle": "sffff00:/workspace/invented.pdf"}]
        result = retain_execution_facts(original, corrected)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["unresolved"], ["scan timed out"])
        self.assertEqual(result["artifacts"], original["artifacts"])

    def test_correction_cannot_silently_clear_all_claims(self):
        corrected = report(self.ref)
        corrected["findings"] = []
        result = checked_report(retain_execution_facts(report(), corrected),
            self.store.task_evidence(self.task.task_id), required=True)
        self.assertEqual(result["status"], "partial")

    def test_only_proven_cluster_upload_is_separated_from_files(self):
        handle = "artifact_" + "a" * 32
        payload = {"artifact_id": handle, "status": "staged", "sha256": "b" * 64,
                   "name": "report.md", "size_bytes": 10}
        self.store.record_evidence(self.task.task_id, self.run.run_id,
            "cluster_artifact_upload", {}, payload)
        original = report(self.ref)
        original["artifacts"].append({"handle": handle, "kind": "cluster_artifact"})
        pristine = deepcopy(original)
        result = separate_cluster_artifacts(original, self.store.task_evidence(self.task.task_id))
        self.assertEqual(original, pristine)
        self.assertEqual(len(result["artifacts"]), 1)
        refs = result["metadata"]["cluster_artifact_refs"]
        self.assertEqual(refs[0]["handle"], handle)
        self.assertEqual(refs[0]["delivery"], "not_a_qq_receipt")
        self.assertEqual(len(separate_cluster_artifacts(original, [])["artifacts"]), 2)

    def test_forged_or_failed_upload_cannot_remove_artifact_errors(self):
        original = report()
        original["artifacts"].append({"handle": "artifact_" + "c" * 32})
        original["metadata"] = {"cluster_artifact_refs": [{"handle": "forged"}]}
        self.store.record_evidence(self.task.task_id, self.run.run_id, "cluster_artifact_upload", {},
            {"ok": False, "status": "staged", "artifact_id": "artifact_" + "c" * 32, "sha256": "f" * 64})
        result = separate_cluster_artifacts(original, self.store.task_evidence(self.task.task_id))
        self.assertEqual(len(result["artifacts"]), 2)
        self.assertNotIn("cluster_artifact_refs", result["metadata"])


if __name__ == "__main__":
    unittest.main()
