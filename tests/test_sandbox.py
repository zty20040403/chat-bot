from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init()

from src.plugins.ai_chat.sandbox import (
    DockerSandboxManager,
    SandboxError,
    SandboxResult,
)


class DockerSandboxManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = DockerSandboxManager()

    def test_workspace_path_rejects_escape(self) -> None:
        with self.assertRaises(SandboxError):
            self.manager._workspace_path("../secret")
        with self.assertRaises(SandboxError):
            self.manager._workspace_path("/etc/passwd")

    def test_new_docker_connection_error_is_user_friendly(self) -> None:
        detail = (
            "failed to connect to the docker API at unix:///tmp/docker.sock; "
            "check if the daemon is running"
        )
        self.assertEqual(
            self.manager._docker_error(detail),
            "Docker 服务没有启动。请先打开 Docker Desktop。",
        )


class DockerSandboxCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_only_applies_eight_gibibyte_memory_limit(self) -> None:
        manager = DockerSandboxManager()
        manager.list = AsyncMock(return_value=[])  # type: ignore[method-assign]
        manager._list_by_label = AsyncMock(  # type: ignore[method-assign]
            return_value=[]
        )
        manager._run = AsyncMock(  # type: ignore[method-assign]
            return_value=SandboxResult("container-id\n", "", 0)
        )

        await manager.create("owner", "python")

        command = manager._run.await_args.args
        self.assertEqual(command[command.index("--memory") + 1], "8g")
        self.assertEqual(command[command.index("--memory-swap") + 1], "8g")
        self.assertNotIn("--cpus", command)
        self.assertNotIn("--pids-limit", command)
        self.assertIn("qqbot.owner_ref=owner", command)
        self.assertIn("qqbot.purpose=task", command)

    async def test_default_shell_reuses_the_group_sandbox(self) -> None:
        manager = DockerSandboxManager(image="kennethbot-sandbox:latest")
        manager.list = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                {
                    "sandbox_id": "sabc123",
                    "runtime": "debian",
                    "purpose": "shell",
                    "status": "Up 2 minutes",
                }
            ]
        )
        manager.create = AsyncMock()  # type: ignore[method-assign]

        selected = await manager.ensure_default("shell:group:1")

        self.assertEqual(selected["sandbox_id"], "sabc123")
        manager.create.assert_not_awaited()

    async def test_configured_advanced_image_runs_as_sandbox_user(self) -> None:
        manager = DockerSandboxManager(image="kennethbot-sandbox:latest")
        manager.list = AsyncMock(return_value=[])  # type: ignore[method-assign]
        manager._list_by_label = AsyncMock(  # type: ignore[method-assign]
            return_value=[]
        )
        manager._run = AsyncMock(  # type: ignore[method-assign]
            return_value=SandboxResult("container-id\n", "", 0)
        )

        created = await manager.create("owner", "python")

        command = manager._run.await_args.args
        self.assertIn("kennethbot-sandbox:latest", command)
        self.assertEqual(command[command.index("--user") + 1], "1000:1000")
        self.assertEqual(created["toolset"], "advanced")

    async def test_zero_file_limit_allows_large_transfers(self) -> None:
        manager = DockerSandboxManager(max_file_bytes=0)
        manager._owned_container = AsyncMock(  # type: ignore[method-assign]
            return_value="qqbot-sabc123"
        )
        manager._run = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                SandboxResult("", "", 0),
                SandboxResult("", "", 0),
            ]
        )

        self.assertIsNone(manager._file_limit(None))
        self.assertEqual(manager._file_limit(64 * 1024), 64 * 1024)
        content = b"x" * (600 * 1024)
        self.assertEqual(
            await manager.write_file("owner", "sabc123", "large.bin", content),
            len(content),
        )

    async def test_exec_returns_observed_manifest(self) -> None:
        manager = DockerSandboxManager()
        manager._owned_container = AsyncMock(return_value="qqbot-sabc123")  # type: ignore[method-assign]
        manager._run_bytes = AsyncMock(return_value=(b"hello", b"warn", 0))  # type: ignore[method-assign]
        manager._run = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                SandboxResult("bridge\n", "", 0),
                SandboxResult("", "", 0),
                SandboxResult("/workspace/main.py\n", "", 0),
                SandboxResult("C /workspace/main.py\n", "", 0),
                SandboxResult("", "", 0),
            ]
        )

        result = await manager.exec("owner", "sabc123", "python main.py")

        self.assertEqual(result.stdout, "hello")
        self.assertEqual(result.stderr, "warn")
        self.assertIsNotNone(result.manifest)
        self.assertEqual(result.manifest.network_mode, "bridge")  # type: ignore[union-attr]
        self.assertEqual(
            result.manifest.changed_workspace_paths,  # type: ignore[union-attr]
            ("main.py",),
        )
        self.assertEqual(result.manifest.stdout_bytes, 5)  # type: ignore[union-attr]
        self.assertEqual(len(result.manifest.stdout_sha256), 64)  # type: ignore[union-attr]
        self.assertEqual(
            manager._last_execs["sabc123"].command,
            "python main.py",
        )
        self.assertEqual(manager._last_execs["sabc123"].status, "completed")

    async def test_admin_snapshot_reports_resources_and_workspace(self) -> None:
        manager = DockerSandboxManager()
        manager._run = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                SandboxResult(
                    (
                        "qqbot-sabc123|sabc123|python|"
                        "group:1:user:2|ownerhash|task|Up 2 minutes\n"
                    ),
                    "",
                    0,
                ),
                SandboxResult(
                    "qqbot-sabc123|1.25%|64MiB / 8GiB|0.78%\n",
                    "",
                    0,
                ),
                SandboxResult(
                    "__TOTAL__|123\n__COUNT__|2\na.txt|5\ndir/b.bin|118\n",
                    "",
                    0,
                ),
            ]
        )

        snapshot = await manager.admin_snapshot()

        self.assertEqual(snapshot["active_commands"], 0)
        item = snapshot["items"][0]  # type: ignore[index]
        self.assertEqual(item["owner"], "group:1:user:2")
        self.assertEqual(item["purpose"], "task")
        self.assertEqual(item["memory_usage"], "64MiB / 8GiB")
        self.assertEqual(item["workspace_file_count"], 2)
        self.assertEqual(item["workspace_files"][1]["path"], "dir/b.bin")

    async def test_cancelling_exec_kills_child_process(self) -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.killed = False
                self.communicate_calls = 0
                self.returncode = -9

            async def communicate(self, _input=None):
                self.communicate_calls += 1
                if self.communicate_calls == 1:
                    await asyncio.Event().wait()
                return b"", b""

            def kill(self) -> None:
                self.killed = True

        process = FakeProcess()
        manager = DockerSandboxManager()
        with patch(
            "src.plugins.ai_chat.sandbox.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ):
            task = asyncio.create_task(
                manager._run_bytes("docker", "ps", timeout=30)
            )
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertTrue(process.killed)
        self.assertEqual(process.communicate_calls, 2)


if __name__ == "__main__":
    unittest.main()
