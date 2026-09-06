from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Mapping

from src.cluster_control.deployment_contracts import SHA256_RE

from .config import DeployerSettings
from .nix_host import require_store_path
from .runtime import LeaseGuard, ProcessRunner


class RepositoryWorkspace:
    """Resolve one immutable revision in an isolated Git worktree."""

    def __init__(self, settings: DeployerSettings, runner: ProcessRunner) -> None:
        self.settings = settings
        self.runner = runner
        self.mirrors = settings.state_dir / "mirrors"
        self.worktrees = settings.state_dir / "worktrees"
        self.mirrors.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.worktrees.mkdir(parents=True, exist_ok=True, mode=0o700)

    async def checkout(
        self,
        deployment: Mapping[str, Any],
        guard: LeaseGuard,
    ) -> tuple[Path, dict[str, str]]:
        deployment_id = str(deployment["deployment_id"])
        revision = str(deployment["source_revision"])
        expected_remote = str(deployment["expected_remote_revision"])
        phase = str(deployment["phase"])
        fence = int(deployment["fence"])
        mirror = self.mirrors / f"{self.settings.repository_id}.git"
        if not mirror.exists():
            await self.runner.run(
                ["git", "clone", "--mirror", self.settings.repository_url, str(mirror)],
                guard,
                timeout=600,
            )
        actual_url = (
            await self.runner.run(
                ["git", "--git-dir", str(mirror), "remote", "get-url", "origin"],
                guard,
                timeout=30,
            )
        ).stdout.strip()
        if actual_url != self.settings.repository_url:
            raise PermissionError("local Git mirror origin differs from configured repository")
        await self.runner.run(
            [
                "git",
                "--git-dir",
                str(mirror),
                "fetch",
                "--prune",
                "origin",
                f"+refs/heads/{self.settings.default_branch}:refs/remotes/origin/{self.settings.default_branch}",
            ],
            guard,
            timeout=600,
        )
        resolved = (
            await self.runner.run(
                ["git", "--git-dir", str(mirror), "rev-parse", f"{revision}^{{commit}}"],
                guard,
                timeout=30,
            )
        ).stdout.strip()
        remote = (
            await self.runner.run(
                [
                    "git",
                    "--git-dir",
                    str(mirror),
                    "rev-parse",
                    f"refs/remotes/origin/{self.settings.default_branch}",
                ],
                guard,
                timeout=30,
            )
        ).stdout.strip()
        if resolved != revision or remote != expected_remote:
            raise ValueError("Git revision changed or remote branch does not match proposal")

        worktree = self.worktrees / f"{deployment_id}-{fence}-{phase}"
        if worktree.exists():
            shutil.rmtree(worktree)
        await self.runner.run(
            ["git", "--git-dir", str(mirror), "worktree", "prune"],
            guard,
            timeout=30,
        )
        await self.runner.run(
            [
                "git",
                "--git-dir",
                str(mirror),
                "worktree",
                "add",
                "--detach",
                str(worktree),
                revision,
            ],
            guard,
            timeout=120,
        )
        status = await self.runner.run(
            ["git", "-C", str(worktree), "status", "--porcelain"],
            guard,
            timeout=30,
        )
        if status.stdout.strip():
            raise ValueError("isolated deployment checkout is unexpectedly dirty")
        lock_file = worktree / "flake.lock"
        if not lock_file.is_file():
            raise ValueError("deployment repository has no flake.lock")
        lock_sha = hashlib.sha256(lock_file.read_bytes()).hexdigest()
        if SHA256_RE.fullmatch(lock_sha) is None:
            raise ValueError("flake.lock checksum failed")
        return worktree, {
            "resolved_revision": resolved,
            "remote_revision": remote,
            "flake_lock_sha256": lock_sha,
        }

    @staticmethod
    def cleanup(worktree: Path) -> None:
        shutil.rmtree(worktree, ignore_errors=True)

    async def build_target(
        self,
        worktree: Path,
        target: Mapping[str, Any],
        guard: LeaseGuard,
    ) -> str:
        result = await self.runner.run(
            [
                "nix",
                "build",
                "--no-link",
                "--print-out-paths",
                "--no-write-lock-file",
                f"{worktree}#nixosConfigurations.{target['flake_host']}.config.system.build.toplevel",
            ],
            guard,
        )
        paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(paths) != 1:
            raise ValueError("Nix preflight returned an unexpected number of outputs")
        return require_store_path(paths[0])

    async def closure_diff(
        self,
        target: Mapping[str, Any],
        current: str,
        desired: str,
        guard: LeaseGuard,
    ) -> str:
        if current == desired:
            return "系统闭包没有变化"
        await self.runner.run(
            ["nix", "copy", "--from", f"ssh-ng://{target['ssh_target']}", current],
            guard,
        )
        result = await self.runner.run(
            ["nix", "store", "diff-closures", current, desired],
            guard,
            timeout=120,
        )
        preview = (result.stdout or result.stderr).strip()
        return preview[-12_000:] or "闭包路径已变化，但 Nix 未报告软件包差异"
