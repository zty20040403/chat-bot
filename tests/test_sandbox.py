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
