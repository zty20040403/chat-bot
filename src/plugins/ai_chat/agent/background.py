"""Reconstruct authorized tool environments from durable tasks, never Python closures."""
from __future__ import annotations

import asyncio
import time
from nonebot import get_bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from .control import JobFence, LeaseLost, active_job_fence
from ..onebot_codec import scope_from_event, decode_onebot_message
from ..deepseek import DeepSeekTrace
from ..workers.durable_jobs import DurableJobWorker


class SubAgentDispatcher:
    kind = "subagent.workflow"

    def __init__(self, services):
        self.services = services
        self.context = services.context
        self.store = self.context.subagent_store
        self.jobs = self.context.job_store
        self.coordinator = self.context.subagent_coordinator
        self.worker = DurableJobWorker(self.jobs, logger=self.context.logger, concurrency=2, per_scope_limit=1)
        self.worker.register(self.kind, self.execute, compensator=self.settle_failed_dispatch)

    async def settle_failed_dispatch(self, job, reason: str):
        if reason != "failed":
            return
        task_id = int(job.payload["task_id"])
        task = self.store.get(task_id)
        if (task is None or task.scope_key != job.scope_key
                or self.store.control(task_id)["revision"] != int(job.payload["revision"])
                or task.status in {"completed", "partial", "failed", "cancelled"}):
            return
        def owns_lease():
            current = self.jobs.get(job.job_id)
            return bool(current and current.status == "running" and current.lease_owner == job.lease_owner
                        and current.attempts == job.attempts and (current.lease_until or 0) > time.time())
        token = active_job_fence.set(JobFence(job.job_id, job.lease_owner, job.attempts, owns_lease))
        try:
            error = "后台任务多次执行失败，已停止；修复连接或权限后可重新提交或修订任务。"
            self.store.settle_unfinished_runs(task_id, running_status="failed", pending_status="skipped", error=error)
            self.store.set_task_state(task_id, "failed", error=error)
        except LeaseLost:
            pass
        finally:
            active_job_fence.reset(token)

    def enqueue(self, task_id: int):
        task = self.store.get(task_id)
        control = self.store.control(task_id)
        return self.jobs.enqueue(kind=self.kind, idempotency_key=f"subagent:{task_id}:revision:{control['revision']}",
            scope_key=task.scope_key, payload={"task_id": task_id, "revision": control["revision"]}, max_attempts=5)

    async def run_forever(self):
        async def reconcile_queue():
            while True:
                for task_id in await asyncio.to_thread(self.store.dispatchable_tasks):
                    await asyncio.to_thread(self.enqueue, task_id)
                for task_id in await asyncio.to_thread(self.store.uncertain_deliveries):
                    try:
                        await self.reconcile(task_id)
                    except Exception as exc:
                        self.context.logger.warning("Sub-Agent delivery reconciliation failed for task#%s: %s", task_id, type(exc).__name__)
                await asyncio.sleep(10)
        async with asyncio.TaskGroup() as group:
            group.create_task(reconcile_queue())
            group.create_task(self.worker.run_forever())

    async def reconcile(self, task_id: int):
        control = self.store.control(task_id)
        dispatch = control["dispatch"]
        if not dispatch:
            return {"matched": 0}
        event = GroupMessageEvent.model_validate(dispatch["event"])
        task = self.store.get(task_id)
        if scope_from_event(event).key != task.scope_key or event.user_id != task.requester_user_id:
            raise ValueError("Invalid dispatch scope")
        bot = get_bot(str(dispatch["bot_id"]))
        response = await bot.call_api("get_group_root_files", group_id=event.group_id)
        files = response.get("files", []) if isinstance(response, dict) else []
        matched = 0
        for delivery in self.store.deliveries(task_id):
            if delivery["state"] not in {"sending", "unknown"}:
                continue
            payload = delivery["payload"]
            found = next((f for f in files if f.get("file_name") == payload.get("filename")
                and int(f.get("file_size", -1)) == int(payload.get("size", -2))
                and str(f.get("uploader")) == str(bot.self_id)), None)
            updated = {**payload, "ok": bool(found), "reconciled": bool(found)}
            if found:
                updated["file_id"] = found.get("file_id")
                matched += 1
            self.store.finish_delivery(task_id, delivery["key"], "acknowledged" if found else "unknown", updated,
                                       revision=delivery["revision"])
        return {"matched": matched}

    async def execute(self, job):
        task_id = int(job.payload["task_id"])
        task = self.store.get(task_id)
        control = self.store.control(task_id)
        if task is None or job.scope_key != task.scope_key or control["revision"] != int(job.payload["revision"]):
            return {"state": "obsolete"}
        if task.cancel_requested or task.status == "cancelled":
            return {"state": "cancelled"}
        dispatch = control["dispatch"]
        event = GroupMessageEvent.model_validate(dispatch["event"])
        if scope_from_event(event).key != task.scope_key or event.user_id != task.requester_user_id:
            raise ValueError("Task dispatch envelope does not match its owner")
        bot = get_bot(str(dispatch["bot_id"]))
        if not self.services.group_enabled(event.group_id):
            raise ValueError("Group disabled; task execution is suspended")

        def owns_lease():
            current = self.jobs.get(job.job_id)
            return bool(current and current.status == "running" and current.lease_owner == job.lease_owner
                        and current.attempts == job.attempts and (current.lease_until or 0) > time.time())

        fence = JobFence(job.job_id, job.lease_owner, job.attempts, owns_lease)
        token = active_job_fence.set(fence)
        try:
            fence.assert_owned()
            if task.status not in {"completed", "partial", "failed"}:
                if task.status not in {"queued", "interrupted"}:
                    self.store.interrupt_task(task_id)
                trace = DeepSeekTrace(trace_id=task.trace_id)
                remaining = float(dispatch.get("deadline", time.time() + self.coordinator.timeout_seconds)) - time.time()
                try:
                    if remaining <= 0:
                        raise TimeoutError
                    async with asyncio.timeout(remaining):
                        await self.services.tools._ask_ai(bot, event, task.objective,
                            selected_model_override=dispatch.get("profile"), turn_trace=trace,
                            resume_task_id=task_id)
                except TimeoutError:
                    self.store.settle_unfinished_runs(task_id, running_status="failed", pending_status="skipped", error="任务总时限已到")
                    self.store.set_task_state(task_id, "failed", error="任务总时限已到；停止执行并保留已完成结果。")
                task = self.store.get(task_id)
                if task.status not in {"completed", "partial", "failed", "cancelled"}:
                    raise RuntimeError("Task did not reach a settled state; retaining it for takeover")
            fence.assert_owned()
            text = str(task.result.get("answer") or task.result.get("result", {}).get("summary") or task.last_error or task.status)
            if self.context.delivery_store is None:
                raise RuntimeError("Durable delivery outbox is required for background tasks")
            body = decode_onebot_message(Message(MessageSegment.text(f"{task.handle}\n{text}"))).body
            delivery, _ = self.context.delivery_store.enqueue(
                idempotency_key=f"subagent-final:{task_id}:{control['revision']}",
                source_scope_key=task.scope_key, source_canonical_message_id=task.trigger_message_id,
                target_scope=scope_from_event(event), body=body, reply_to_native_message_id=str(event.message_id))
            self.store.append_event(task_id, "task.final_delivery_queued", {"delivery_id": delivery.delivery_id})
            return {"task_id": task_id, "status": task.status, "delivery_id": delivery.delivery_id}
        except LeaseLost:
            raise asyncio.CancelledError("worker lease lost")
        finally:
            active_job_fence.reset(token)
