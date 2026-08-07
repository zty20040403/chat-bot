from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import shlex
import time
from dataclasses import dataclass
from pathlib import PurePosixPath


SANDBOX_ID_PATTERN = re.compile(r"^s[0-9a-f]{6}$")
RUNTIME_IMAGES = {
    "python": "python:3.12-slim",
    "node": "node:22-slim",
    "debian": "debian:bookworm-slim",
}


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxResult:
    stdout: str
    stderr: str
    returncode: int
    manifest: "SandboxObservedManifest | None" = None


@dataclass(frozen=True)
class SandboxObservedManifest:
    command: str
    duration_ms: int
    stdout_sha256: str
    stdout_bytes: int
    stderr_sha256: str
    stderr_bytes: int
    changed_workspace_paths: tuple[str, ...]
    container_diff: tuple[str, ...]
    network_mode: str


class DockerSandboxManager:
    def __init__(
        self,
        *,
        max_per_owner: int = 2,
        max_total: int = 8,
        default_timeout_seconds: int = 120,
        max_output_chars: int = 12000,
        max_file_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self.max_per_owner = max(1, max_per_owner)
        self.max_total = max(1, max_total)
        self.default_timeout_seconds = max(5, default_timeout_seconds)
        self.max_output_chars = max(1000, max_output_chars)
        self.max_file_bytes = max(1024, max_file_bytes)

    async def create(self, owner: str, runtime: str = "python") -> dict[str, str]:
        image = RUNTIME_IMAGES.get(runtime)
        if image is None:
            raise SandboxError("不支持的运行环境。")

        owner_hash = self._owner_hash(owner)
        owner_sandboxes = await self.list(owner)
        if len(owner_sandboxes) >= self.max_per_owner:
            raise SandboxError(
                f"你最多同时创建 {self.max_per_owner} 个沙盒，请先销毁旧沙盒。"
            )
        all_sandboxes = await self._list_by_label("qqbot.sandbox=true")
        if len(all_sandboxes) >= self.max_total:
            raise SandboxError("机器人沙盒总数已达到上限。")

        sandbox_id = "s" + secrets.token_hex(3)
        name = self._container_name(sandbox_id)
        command = [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--label",
            "qqbot.sandbox=true",
            "--label",
            f"qqbot.owner={owner_hash}",
            "--label",
            f"qqbot.id={sandbox_id}",
            "--label",
            f"qqbot.runtime={runtime}",
            "--memory",
            "1g",
            "--memory-swap",
            "1g",
            "--cpus",
            "1.5",
            "--pids-limit",
            "128",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=128m",
            "--workdir",
            "/workspace",
            "--restart",
            "no",
            image,
            "sh",
            "-lc",
            "mkdir -p /workspace && exec sleep infinity",
        ]
        result = await self._run(*command, timeout=300)
        if result.returncode != 0:
            detail = result.stderr or result.stdout
            raise SandboxError(self._docker_error(detail))
        return {
            "sandbox_id": sandbox_id,
            "runtime": runtime,
            "image": image,
            "status": "running",
        }

    async def list(self, owner: str) -> list[dict[str, str]]:
        return await self._list_by_label(
            f"qqbot.owner={self._owner_hash(owner)}"
        )

    async def destroy(self, owner: str, sandbox_id: str) -> None:
        name = await self._owned_container(owner, sandbox_id)
        result = await self._run(
            "docker",
            "rm",
            "-f",
            name,
            timeout=30,
        )
        if result.returncode != 0:
            raise SandboxError(self._docker_error(result.stderr))

    async def exec(
        self,
        owner: str,
        sandbox_id: str,
        command: str,
        timeout_seconds: int | None = None,
    ) -> SandboxResult:
        if not command.strip():
            raise SandboxError("命令不能为空。")
        name = await self._owned_container(owner, sandbox_id)
        timeout = min(
            max(timeout_seconds or self.default_timeout_seconds, 1),
            300,
        )
        marker = f"/tmp/qqbot-observe-{secrets.token_hex(8)}"
        network_mode = await self._network_mode(name)
        marker_result = await self._run(
            "docker", "exec", name, "touch", marker, timeout=10
        )
        marker_ready = marker_result.returncode == 0
        started = time.monotonic()
        try:
            stdout_bytes, stderr_bytes, returncode = await self._run_bytes(
                "docker",
                "exec",
                name,
                "sh",
                "-lc",
                command,
                timeout=timeout,
            )
        except asyncio.CancelledError:
            if marker_ready:
                await self._remove_observation_marker(name, marker)
            raise
        except Exception:
            if marker_ready:
                await self._remove_observation_marker(name, marker)
            raise
        duration_ms = max(int((time.monotonic() - started) * 1000), 0)
        changed_paths = (
            await self._changed_workspace_paths(name, marker)
            if marker_ready
            else ()
        )
        container_diff = await self._container_diff(name, marker)
        if marker_ready:
            await self._remove_observation_marker(name, marker)
        return SandboxResult(
            stdout=self._trim_output(
                stdout_bytes.decode("utf-8", errors="replace")
            ),
            stderr=self._trim_output(
                stderr_bytes.decode("utf-8", errors="replace")
            ),
            returncode=returncode,
            manifest=SandboxObservedManifest(
                command=command,
                duration_ms=duration_ms,
                stdout_sha256=hashlib.sha256(stdout_bytes).hexdigest(),
                stdout_bytes=len(stdout_bytes),
                stderr_sha256=hashlib.sha256(stderr_bytes).hexdigest(),
                stderr_bytes=len(stderr_bytes),
                changed_workspace_paths=changed_paths,
                container_diff=container_diff,
                network_mode=network_mode,
            ),
        )

    async def write_file(
        self,
        owner: str,
        sandbox_id: str,
        path: str,
        content: bytes,
        *,
        allow_large: bool = False,
    ) -> int:
        limit = self.max_file_bytes if allow_large else min(
            self.max_file_bytes, 512 * 1024
        )
        if len(content) > limit:
            raise SandboxError(f"单次写入文件不能超过 {limit} 字节。")
        name = await self._owned_container(owner, sandbox_id)
        container_path = self._workspace_path(path)
        parent = str(PurePosixPath(container_path).parent)
        mkdir_result = await self._run(
            "docker",
            "exec",
            name,
            "mkdir",
            "-p",
            "--",
            parent,
            timeout=20,
        )
        if mkdir_result.returncode != 0:
            raise SandboxError(mkdir_result.stderr or "创建目录失败。")
        result = await self._run(
            "docker",
            "exec",
            "-i",
            name,
            "tee",
            container_path,
            input_bytes=content,
            timeout=30,
        )
        if result.returncode != 0:
            raise SandboxError(result.stderr or "写入文件失败。")
        return len(content)

    async def read_file(
        self,
        owner: str,
        sandbox_id: str,
        path: str,
        max_bytes: int = 64 * 1024,
    ) -> bytes:
        name = await self._owned_container(owner, sandbox_id)
        container_path = self._workspace_path(path)
        size_result = await self._run(
            "docker",
            "exec",
            name,
            "sh",
            "-lc",
            f"wc -c < {shlex.quote(container_path)}",
            timeout=20,
        )
        if size_result.returncode != 0:
            raise SandboxError("文件不存在或无法读取。")
        try:
            size = int(size_result.stdout.strip())
        except ValueError as exc:
            raise SandboxError("无法读取文件大小。") from exc
        limit = min(max(1, max_bytes), self.max_file_bytes)
        if size > limit:
            raise SandboxError(f"文件大小 {size} 字节，超过读取上限 {limit}。")
        result = await self._run_bytes(
            "docker",
            "exec",
            name,
            "cat",
            "--",
            container_path,
            timeout=60,
        )
        if result[2] != 0:
            raise SandboxError(
                result[1].decode("utf-8", errors="replace") or "读取文件失败。"
            )
        return result[0]

    async def _owned_container(self, owner: str, sandbox_id: str) -> str:
        if not SANDBOX_ID_PATTERN.fullmatch(sandbox_id):
            raise SandboxError("沙盒 ID 格式错误。")
        name = self._container_name(sandbox_id)
        result = await self._run(
            "docker",
            "inspect",
            "--format",
            '{{ index .Config.Labels "qqbot.owner" }}|{{ .State.Running }}',
            name,
            timeout=20,
        )
        if result.returncode != 0:
            raise SandboxError("沙盒不存在。")
        owner_hash, _, running = result.stdout.strip().partition("|")
        if owner_hash != self._owner_hash(owner):
            raise SandboxError("你无权访问这个沙盒。")
        if running.lower() != "true":
            raise SandboxError("沙盒没有运行。")
        return name

    async def _network_mode(self, name: str) -> str:
        result = await self._run(
            "docker",
            "inspect",
            "--format",
            "{{ .HostConfig.NetworkMode }}",
            name,
            timeout=10,
        )
        return result.stdout.strip()[:80] if result.returncode == 0 else "unknown"

    async def _changed_workspace_paths(
        self,
        name: str,
        marker: str,
    ) -> tuple[str, ...]:
        command = (
            "find /workspace -xdev -type f -newer "
            f"{shlex.quote(marker)} -print 2>/dev/null"
        )
        result = await self._run(
            "docker",
            "exec",
            name,
            "sh",
            "-lc",
            command,
            timeout=20,
        )
        if result.returncode != 0:
            return ()
        paths = []
        for raw_path in result.stdout.splitlines():
            path = raw_path.strip()
            if not path.startswith("/workspace/"):
                continue
            relative = path.removeprefix("/workspace/")
            if relative and relative not in paths:
                paths.append(relative[:500])
            if len(paths) >= 200:
                break
        return tuple(paths)

    async def _container_diff(
        self,
        name: str,
        marker: str,
    ) -> tuple[str, ...]:
        result = await self._run("docker", "diff", name, timeout=20)
        if result.returncode != 0:
            return ()
        entries = []
        for line in result.stdout.splitlines():
            normalized = " ".join(line.split())[:600]
            if not normalized or marker in normalized:
                continue
            if normalized not in entries:
                entries.append(normalized)
            if len(entries) >= 200:
                break
        return tuple(entries)

    async def _remove_observation_marker(self, name: str, marker: str) -> None:
        try:
            await self._run(
                "docker",
                "exec",
                name,
                "rm",
                "-f",
                marker,
                timeout=10,
            )
        except SandboxError:
            return

    async def _list_by_label(self, label: str) -> list[dict[str, str]]:
        result = await self._run(
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label={label}",
            "--format",
            '{{.Label "qqbot.id"}}|{{.Label "qqbot.runtime"}}|{{.Status}}',
            timeout=20,
        )
        if result.returncode != 0:
            raise SandboxError(self._docker_error(result.stderr))
        sandboxes: list[dict[str, str]] = []
        for line in result.stdout.splitlines():
            sandbox_id, runtime, status = (line.split("|", 2) + ["", ""])[:3]
            if SANDBOX_ID_PATTERN.fullmatch(sandbox_id):
                sandboxes.append(
                    {
                        "sandbox_id": sandbox_id,
                        "runtime": runtime,
                        "status": status,
                    }
                )
        return sandboxes

    async def _run(
        self,
        *command: str,
        input_bytes: bytes | None = None,
        timeout: int,
    ) -> SandboxResult:
        stdout, stderr, returncode = await self._run_bytes(
            *command,
            input_bytes=input_bytes,
            timeout=timeout,
        )
        return SandboxResult(
            stdout=self._trim_output(stdout.decode("utf-8", errors="replace")),
            stderr=self._trim_output(stderr.decode("utf-8", errors="replace")),
            returncode=returncode,
        )

    async def _run_bytes(
        self,
        *command: str,
        input_bytes: bytes | None = None,
        timeout: int,
    ) -> tuple[bytes, bytes, int]:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=(
                    asyncio.subprocess.PIPE
                    if input_bytes is not None
                    else asyncio.subprocess.DEVNULL
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise SandboxError("没有安装 Docker 命令行工具。") from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input_bytes),
                timeout=timeout,
            )
        except asyncio.CancelledError:
            process.kill()
            await process.communicate()
            raise
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise SandboxError(f"沙盒操作超过 {timeout} 秒，已终止等待。") from exc
        return stdout, stderr, process.returncode or 0

    def _workspace_path(self, path: str) -> str:
        candidate = PurePosixPath(path.strip())
        if (
            not path.strip()
            or candidate.is_absolute()
            or ".." in candidate.parts
            or str(candidate) in {".", ""}
        ):
            raise SandboxError("文件路径必须是 /workspace 下的安全相对路径。")
        return str(PurePosixPath("/workspace") / candidate)

    def _trim_output(self, text: str) -> str:
        if len(text) <= self.max_output_chars:
            return text
        return text[: self.max_output_chars].rstrip() + "\n[输出过长，已截断]"

    @staticmethod
    def _container_name(sandbox_id: str) -> str:
        return f"qqbot-{sandbox_id}"

    @staticmethod
    def _owner_hash(owner: str) -> str:
        return hashlib.sha256(owner.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def _docker_error(detail: str) -> str:
        lowered = detail.lower()
        if (
            "cannot connect to the docker daemon" in lowered
            or "failed to connect to the docker api" in lowered
            or "is the docker daemon running" in lowered
        ):
            return "Docker 服务没有启动。请先打开 Docker Desktop。"
        if "permission denied" in lowered and "docker" in lowered:
            return "机器人没有访问 Docker 的权限。"
        return detail.strip()[:500] or "Docker 操作失败。"
