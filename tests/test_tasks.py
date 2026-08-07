from __future__ import annotations

import asyncio
import unittest

from src.plugins.ai_chat.tasks import RunningTaskRegistry


class RunningTaskRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_lists_and_cancels_running_task(self) -> None:
        registry = RunningTaskRegistry()
        info_queue: asyncio.Queue[object] = asyncio.Queue()

        async def worker() -> None:
            info = registry.register_current(
                conversation_id="group:1:user:2",
                user_id=2,
                group_id=1,
                message_id=10,
                summary="创建项目并测试",
            )
            await info_queue.put(info)
            try:
                await asyncio.Event().wait()
            finally:
                registry.finish(info.task_id)

        task = asyncio.create_task(worker())
        info = await info_queue.get()
        listed = registry.list_for("group:1:user:2")
        self.assertEqual([item.task_id for item in listed], [info.task_id])
        self.assertEqual(registry.list_for("group:1:user:3"), [])

        stopped = registry.cancel("group:1:user:2", info.task_id)
        self.assertIsNotNone(stopped)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(registry.list_for("group:1:user:2"), [])

    async def test_cancel_without_id_targets_latest_task(self) -> None:
        registry = RunningTaskRegistry()
        ready: asyncio.Queue[object] = asyncio.Queue()

        async def worker(summary: str) -> None:
            info = registry.register_current(
                conversation_id="private:2",
                user_id=2,
                group_id=None,
                message_id=1,
                summary=summary,
            )
            await ready.put(info)
            try:
                await asyncio.Event().wait()
            finally:
                registry.finish(info.task_id)

        first_task = asyncio.create_task(worker("first"))
        first = await ready.get()
        second_task = asyncio.create_task(worker("second"))
        second = await ready.get()

        stopped = registry.cancel("private:2")
        self.assertEqual(stopped.task_id, second.task_id)
        with self.assertRaises(asyncio.CancelledError):
            await second_task
        self.assertEqual(
            [item.task_id for item in registry.list_for("private:2")],
            [first.task_id],
        )
        first_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first_task


if __name__ == "__main__":
    unittest.main()
