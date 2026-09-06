from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from typing import Sequence

from .client import DeploymentControlClient
from .config import DeployerSettings


logger = logging.getLogger("kennethbot.cluster_deployer")
MAX_OUTPUT_BYTES = 32_000


class DeploymentCancelled(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


class CommandFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


class LeaseGuard:
    def __init__(
        self,
        client: DeploymentControlClient,
        deployment_id: str,
        fence: int,
    ) -> None:
        self.client = client
        self.deployment_id = deployment_id
        self.fence = fence
        self.stop = asyncio.Event()
        self.cancel = asyncio.Event()
        self.lost = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "LeaseGuard":
        self.task = asyncio.create_task(self._renew_loop())
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.stop.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def _renew_loop(self) -> None:
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=20)
                return
            except asyncio.TimeoutError:
                pass
            try:
                response = await self.client.renew(self.deployment_id, self.fence)
                if bool(response.get("cancel_requested")):
                    self.cancel.set()
                    return
            except Exception as exc:
                logger.error("Deployment %s lost its lease: %s", self.deployment_id, exc)
                self.lost.set()
                self.cancel.set()
                return

    def check(self) -> None:
        if self.lost.is_set():
            raise LeaseLost("deployment lease was lost")
        if self.cancel.is_set():
            raise DeploymentCancelled("deployment was cancelled")


class ProcessRunner:
    """Run fixed argument vectors and stop their process group on cancel or timeout."""

    def __init__(self, settings: DeployerSettings) -> None:
        self.settings = settings

    def ssh_options(self) -> list[str]:
        return [
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={self.settings.ssh_known_hosts_file}",
            "-o", f"IdentityFile={self.settings.ssh_identity_file}",
            "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=2",
        ]

    def environment(self) -> dict[str, str]:
        return {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.settings.state_dir),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
            "NIX_CONFIG": "accept-flake-config = false\nwarn-dirty = false",
            "NIX_SSHOPTS": " ".join(self.ssh_options()),
        }

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            await process.wait()

    async def run(
        self,
        command: Sequence[str],
        guard: LeaseGuard,
        *,
        timeout: int | None = None,
    ) -> CommandResult:
        guard.check()
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.environment(),
            start_new_session=True,
        )
        communicate = asyncio.create_task(process.communicate())
        cancelled = asyncio.create_task(guard.cancel.wait())
        try:
            done, _ = await asyncio.wait(
                {communicate, cancelled},
                timeout=timeout or self.settings.command_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if communicate not in done:
                await self._terminate(process)
                if cancelled in done:
                    guard.check()
                raise CommandFailure("command_timeout", "deployment command timed out")
            stdout_bytes, stderr_bytes = communicate.result()
        finally:
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)
            if not communicate.done():
                communicate.cancel()
                await asyncio.gather(communicate, return_exceptions=True)
        stdout = stdout_bytes[-MAX_OUTPUT_BYTES:].decode("utf-8", errors="replace")
        stderr = stderr_bytes[-MAX_OUTPUT_BYTES:].decode("utf-8", errors="replace")
        result = CommandResult(stdout=stdout, stderr=stderr, returncode=process.returncode or 0)
        if result.returncode != 0:
            detail = (stderr or stdout or "command failed").strip()[-1000:]
            raise CommandFailure("command_failed", detail)
        guard.check()
        return result

    async def ssh(
        self,
        ssh_target: str,
        remote_command: Sequence[str],
        guard: LeaseGuard,
        *,
        timeout: int = 90,
    ) -> CommandResult:
        return await self.run(
            ["ssh", *self.ssh_options(), ssh_target, *remote_command],
            guard,
            timeout=timeout,
        )
