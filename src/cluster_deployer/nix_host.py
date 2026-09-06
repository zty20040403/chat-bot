from __future__ import annotations

import re
from typing import Any, Mapping

from .runtime import LeaseGuard, ProcessRunner


STORE_PATH_RE = re.compile(r"/nix/store/[a-z0-9]{32}-[^\s\x00\r\n]+")


def require_store_path(path: str) -> str:
    if STORE_PATH_RE.fullmatch(path) is None or len(path) > 500:
        raise ValueError("command did not return one valid Nix store path")
    return path


class NixHostOperator:
    """Perform the small, fixed set of allowed operations on a NixOS target."""

    def __init__(self, runner: ProcessRunner) -> None:
        self.runner = runner

    async def current_toplevel(
        self,
        target: Mapping[str, Any],
        guard: LeaseGuard,
    ) -> str:
        result = await self.runner.ssh(
            str(target["ssh_target"]),
            ["readlink", "-f", "/run/current-system"],
            guard,
            timeout=45,
        )
        return require_store_path(result.stdout.strip())

    async def verify(
        self,
        target: Mapping[str, Any],
        expected: str,
        guard: LeaseGuard,
    ) -> dict[str, Any]:
        actual = await self.current_toplevel(target, guard)
        if actual != expected:
            raise ValueError("running system closure differs from the approved target")
        unit_states: dict[str, str] = {}
        for unit in target["verification_units"]:
            result = await self.runner.ssh(
                str(target["ssh_target"]),
                ["systemctl", "is-active", str(unit)],
                guard,
                timeout=45,
            )
            state = result.stdout.strip()
            if state != "active":
                raise ValueError(f"verification unit is not active: {unit}")
            unit_states[str(unit)] = state
        return {"actual_toplevel": actual, "units": unit_states}

    async def copy_closure(
        self,
        target: Mapping[str, Any],
        desired: str,
        guard: LeaseGuard,
    ) -> None:
        require_store_path(desired)
        await self.runner.run(
            ["nix", "copy", "--to", f"ssh-ng://{target['ssh_target']}", desired],
            guard,
        )

    async def activate(
        self,
        target: Mapping[str, Any],
        desired: str,
        guard: LeaseGuard,
    ) -> None:
        require_store_path(desired)
        command = [f"{desired}/bin/switch-to-configuration", "switch"]
        if bool(target["use_remote_sudo"]):
            command = ["sudo", "--", *command]
        await self.runner.ssh(
            str(target["ssh_target"]),
            command,
            guard,
            timeout=600,
        )

    async def rollback(
        self,
        target: Mapping[str, Any],
        previous: str,
        guard: LeaseGuard,
    ) -> dict[str, Any]:
        require_store_path(previous)
        prefix = ["sudo", "--"] if bool(target["use_remote_sudo"]) else []
        await self.runner.ssh(
            str(target["ssh_target"]),
            [*prefix, "test", "-x", f"{previous}/bin/switch-to-configuration"],
            guard,
            timeout=45,
        )
        await self.runner.ssh(
            str(target["ssh_target"]),
            [
                *prefix,
                "nix-env",
                "--profile",
                "/nix/var/nix/profiles/system",
                "--set",
                previous,
            ],
            guard,
            timeout=120,
        )
        await self.runner.ssh(
            str(target["ssh_target"]),
            [*prefix, f"{previous}/bin/switch-to-configuration", "switch"],
            guard,
            timeout=600,
        )
        return await self.verify(target, previous, guard)
