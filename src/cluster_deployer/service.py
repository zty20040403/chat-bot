from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Iterable, Mapping

import httpx

from src.cluster_control.deployment_contracts import (
    REVISION_RE,
    approval_contract_hash,
)
from src.cluster_control.execution_contracts import content_hash

from .client import DeploymentControlClient
from .config import DeployerSettings
from .nix_host import NixHostOperator, require_store_path
from .repository import RepositoryWorkspace
from .runtime import (
    DeploymentCancelled,
    LeaseGuard,
    LeaseLost,
    ProcessRunner,
)


logger = logging.getLogger("kennethbot.cluster_deployer")
DEPLOYMENT_ID_RE = re.compile(r"deploy_[a-f0-9]{32}")


class TargetFailure(RuntimeError):
    def __init__(self, host_id: str, code: str, *, side_effect_unknown: bool) -> None:
        super().__init__(f"{host_id}: {code}")
        self.host_id = host_id
        self.code = code
        self.side_effect_unknown = side_effect_unknown

class ClusterDeployer:
    """Apply one immutable, preflighted Nix deployment contract at a time."""

    def __init__(
        self,
        settings: DeployerSettings,
        *,
        client: DeploymentControlClient | None = None,
    ) -> None:
        settings.validate()
        self.settings = settings
        self.client = client or DeploymentControlClient(
            settings.control_url,
            settings.token_file,
        )
        self.stop_event = asyncio.Event()
        self.settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.receipts = self.settings.state_dir / "receipts"
        self.receipts.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.targets = {
            str(item["host_id"]): dict(item) for item in self.settings.targets
        }
        self.runner = ProcessRunner(settings)
        self.repository = RepositoryWorkspace(settings, self.runner)
        self.hosts = NixHostOperator(self.runner)

    async def close(self) -> None:
        self.stop_event.set()
        await self.client.close()

    async def run_forever(self) -> None:
        try:
            while not self.stop_event.is_set():
                try:
                    await self._flush_local_receipts()
                    deployment = await self.client.claim()
                    if deployment is not None:
                        await self._run_deployment(deployment)
                        continue
                except Exception as exc:
                    logger.error("Deployment control loop failed: %s", exc)
                try:
                    await asyncio.wait_for(
                        self.stop_event.wait(),
                        timeout=self.settings.poll_seconds,
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.close()

    async def _run_deployment(self, deployment: dict[str, Any]) -> None:
        deployment_id = str(deployment.get("deployment_id") or "")
        fence = int(deployment.get("fence") or 0)
        phase = str(deployment.get("phase") or "")
        try:
            self._validate_claim(deployment)
        except Exception as exc:
            logger.error("Rejected deployment claim %s: %s", deployment_id, exc)
            await self._complete_best_effort(
                deployment_id,
                fence,
                ok=False,
                result={"error_code": "local_policy_mismatch", "message": str(exc)[:500]},
            )
            return

        async with LeaseGuard(self.client, deployment_id, fence) as guard:
            try:
                if phase == "preflight":
                    result = await self._preflight(deployment, guard)
                elif phase == "apply":
                    result = await self._apply(deployment, guard)
                else:
                    raise ValueError("unsupported deployment phase")
                guard.check()
                ok = not bool(result.get("error_code"))
                await self.client.complete(
                    deployment_id,
                    fence,
                    ok=ok,
                    result=result,
                )
            except LeaseLost:
                logger.critical(
                    "Deployment %s outcome is unknown after lease loss; awaiting reconciliation",
                    deployment_id,
                )
            except DeploymentCancelled:
                await self._complete_best_effort(
                    deployment_id,
                    fence,
                    ok=False,
                    result={"error_code": "cancelled"},
                )
            except Exception as exc:
                logger.error("Deployment %s failed in %s: %s", deployment_id, phase, exc)
                await self._complete_best_effort(
                    deployment_id,
                    fence,
                    ok=False,
                    result={
                        "error_code": getattr(exc, "code", type(exc).__name__)[:120],
                        "message": str(exc)[:1000],
                    },
                )

    async def _complete_best_effort(
        self,
        deployment_id: str,
        fence: int,
        *,
        ok: bool,
        result: dict[str, Any],
    ) -> None:
        if not deployment_id or fence < 1:
            return
        try:
            await self.client.complete(
                deployment_id,
                fence,
                ok=ok,
                result=result,
            )
        except Exception as exc:
            logger.error("Could not persist deployment %s receipt: %s", deployment_id, exc)
            self._save_local_receipt(deployment_id, fence, ok, result)

    def _save_local_receipt(
        self,
        deployment_id: str,
        fence: int,
        ok: bool,
        result: Mapping[str, Any],
    ) -> None:
        if DEPLOYMENT_ID_RE.fullmatch(deployment_id) is None:
            return
        path = self.receipts / f"{deployment_id}-{fence}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "deployment_id": deployment_id,
                    "fence": fence,
                    "ok": ok,
                    "result": dict(result),
                    "recorded_at": int(time.time()),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    async def _flush_local_receipts(self) -> None:
        rejected = self.receipts / "rejected"
        for path in sorted(self.receipts.glob("deploy_*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                await self.client.complete(
                    str(payload["deployment_id"]),
                    int(payload["fence"]),
                    ok=bool(payload["ok"]),
                    result=dict(payload.get("result") or {}),
                )
                path.unlink(missing_ok=True)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {404, 409}:
                    return
                rejected.mkdir(exist_ok=True, mode=0o700)
                os.replace(path, rejected / path.name)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                rejected.mkdir(exist_ok=True, mode=0o700)
                os.replace(path, rejected / path.name)
            except Exception:
                return

    def _validate_claim(self, deployment: Mapping[str, Any]) -> None:
        deployment_id = str(deployment.get("deployment_id") or "")
        phase = str(deployment.get("phase") or "")
        repository = deployment.get("repository")
        contract = deployment.get("contract")
        preflight = deployment.get("preflight")
        target_configs = deployment.get("target_configs")
        target_hosts = deployment.get("target_hosts")
        if (
            DEPLOYMENT_ID_RE.fullmatch(deployment_id) is None
            or phase not in {"preflight", "apply"}
            or not isinstance(repository, dict)
            or not isinstance(contract, dict)
            or not isinstance(preflight, dict)
            or not isinstance(target_configs, list)
            or not isinstance(target_hosts, list)
        ):
            raise ValueError("malformed deployment claim")
        if (
            repository.get("repository_id") != self.settings.repository_id
            or repository.get("url") != self.settings.repository_url
            or repository.get("default_branch") != self.settings.default_branch
            or deployment.get("repository_id") != self.settings.repository_id
        ):
            raise PermissionError("repository is outside the local deployer allowlist")
        revision = str(deployment.get("source_revision") or "")
        remote_revision = str(deployment.get("expected_remote_revision") or "")
        if REVISION_RE.fullmatch(revision) is None or REVISION_RE.fullmatch(remote_revision) is None:
            raise ValueError("deployment revisions are not immutable Git commits")
        ordered_hosts = [str(item) for item in target_hosts]
        if ordered_hosts != list(contract.get("target_hosts") or []):
            raise ValueError("target order differs from the approved contract")
        if (
            not self.settings.allow_self_deployment
            and self.settings.executor_host_id in ordered_hosts
        ):
            raise PermissionError(
                "deployer cannot switch its own host without explicit local approval"
            )
        claimed_targets = {str(item.get("host_id")): item for item in target_configs}
        if set(claimed_targets) != set(ordered_hosts):
            raise ValueError("target configuration is incomplete")
        for host_id in ordered_hosts:
            local = self.targets.get(host_id)
            remote = claimed_targets[host_id]
            if local is None or any(
                remote.get(key) != local.get(key)
                for key in (
                    "host_id",
                    "flake_host",
                    "ssh_target",
                    "verification_units",
                    "use_remote_sudo",
                    "resource_version",
                )
            ):
                raise PermissionError(f"target {host_id} differs from local policy")
        expected_hash = (
            content_hash(contract)
            if phase == "preflight"
            else approval_contract_hash(contract, preflight)
        )
        if expected_hash != deployment.get("contract_hash"):
            raise ValueError("deployment approval contract hash does not match")
        if int(contract.get("deadline_at") or 0) <= int(time.time()):
            raise ValueError("deployment deadline expired")

    async def _preflight(
        self,
        deployment: dict[str, Any],
        guard: LeaseGuard,
    ) -> dict[str, Any]:
        worktree, revision_evidence = await self.repository.checkout(deployment, guard)
        targets = {str(item["host_id"]): item for item in deployment["target_configs"]}
        evidence: dict[str, Any] = {}
        built: dict[str, str] = {}
        try:
            for host_id in deployment["target_hosts"]:
                target = targets[str(host_id)]
                await self.client.update_target(
                    str(deployment["deployment_id"]),
                    str(host_id),
                    {"fence": int(deployment["fence"]), "status": "preflighting", "step": "read-current"},
                )
                try:
                    current = await self.hosts.current_toplevel(target, guard)
                    flake_host = str(target["flake_host"])
                    desired = built.get(flake_host)
                    if desired is None:
                        desired = await self.repository.build_target(worktree, target, guard)
                        built[flake_host] = desired
                    change_preview = await self.repository.closure_diff(
                        target, current, desired, guard
                    )
                except (LeaseLost, DeploymentCancelled):
                    raise
                except Exception as exc:
                    await self.client.update_target(
                        str(deployment["deployment_id"]),
                        str(host_id),
                        {
                            "fence": int(deployment["fence"]),
                            "status": "failed",
                            "step": "preflight-failed",
                            "error_code": getattr(exc, "code", type(exc).__name__)[:120],
                        },
                    )
                    raise
                await self.client.update_target(
                    str(deployment["deployment_id"]),
                    str(host_id),
                    {
                        "fence": int(deployment["fence"]),
                        "status": "build_ready",
                        "step": "preflight-complete",
                        "current_toplevel": current,
                        "target_toplevel": desired,
                        "verification": {
                            "isolated_checkout": True,
                            "verification_units": list(target["verification_units"]),
                            "change_preview": change_preview,
                        },
                    },
                )
                evidence[str(host_id)] = {
                    "current_toplevel": current,
                    "target_toplevel": desired,
                    "verification_units": list(target["verification_units"]),
                    "change_preview": change_preview,
                }
            return {
                **revision_evidence,
                "isolated_checkout": True,
                "shared_checkout_modified": False,
                "targets": evidence,
            }
        finally:
            self.repository.cleanup(worktree)

    async def _activate_target(
        self,
        deployment: Mapping[str, Any],
        target: Mapping[str, Any],
        desired: str,
        guard: LeaseGuard,
    ) -> None:
        host_id = str(target["host_id"])
        payload = {"fence": int(deployment["fence"]), "status": "deploying"}
        activation_started = False
        try:
            await self.client.update_target(
                str(deployment["deployment_id"]),
                host_id,
                {**payload, "step": "copy-closure"},
            )
            await self.hosts.copy_closure(target, desired, guard)
            await self.client.update_target(
                str(deployment["deployment_id"]),
                host_id,
                {**payload, "step": "activate"},
            )
            activation_started = True
            await self.hosts.activate(target, desired, guard)
            await self.client.update_target(
                str(deployment["deployment_id"]),
                host_id,
                {"fence": int(deployment["fence"]), "status": "verifying", "step": "verify"},
            )
            verification = await self.hosts.verify(target, desired, guard)
            await self.client.update_target(
                str(deployment["deployment_id"]),
                host_id,
                {
                    "fence": int(deployment["fence"]),
                    "status": "succeeded",
                    "step": "verified",
                    "actual_toplevel": verification["actual_toplevel"],
                    "verification": verification,
                },
            )
        except LeaseLost:
            raise
        except DeploymentCancelled as exc:
            await self.client.update_target(
                str(deployment["deployment_id"]),
                host_id,
                {
                    "fence": int(deployment["fence"]),
                    "status": "unknown" if activation_started else "failed",
                    "step": "cancelled-during-activation" if activation_started else "cancelled-before-activation",
                    "error_code": "cancelled",
                },
            )
            raise TargetFailure(
                host_id,
                "cancelled",
                side_effect_unknown=activation_started,
            ) from exc
        except Exception as exc:
            await self.client.update_target(
                str(deployment["deployment_id"]),
                host_id,
                {
                    "fence": int(deployment["fence"]),
                    "status": "unknown" if activation_started else "failed",
                    "step": (
                        "activation-outcome-unknown"
                        if activation_started
                        else "copy-failed"
                    ),
                    "error_code": getattr(exc, "code", type(exc).__name__)[:120],
                },
            )
            raise TargetFailure(
                host_id,
                getattr(exc, "code", type(exc).__name__)[:120],
                side_effect_unknown=activation_started,
            ) from exc

    async def _rollback_target(
        self,
        deployment: Mapping[str, Any],
        target: Mapping[str, Any],
        previous: str,
        guard: LeaseGuard,
    ) -> bool:
        host_id = str(target["host_id"])
        require_store_path(previous)
        common = {"fence": int(deployment["fence"])}
        try:
            await self.client.update_target(
                str(deployment["deployment_id"]), host_id,
                {**common, "status": "rollback_pending", "step": "rollback-queued"},
            )
            await self.client.update_target(
                str(deployment["deployment_id"]), host_id,
                {**common, "status": "rolling_back", "step": "rollback-activate"},
            )
            verification = await self.hosts.rollback(target, previous, guard)
            await self.client.update_target(
                str(deployment["deployment_id"]), host_id,
                {
                    **common,
                    "status": "rolled_back",
                    "step": "rollback-verified",
                    "actual_toplevel": previous,
                    "verification": verification,
                },
            )
            return True
        except (LeaseLost, DeploymentCancelled):
            raise
        except Exception as exc:
            await self.client.update_target(
                str(deployment["deployment_id"]), host_id,
                {
                    **common,
                    "status": "rollback_failed",
                    "step": "rollback-failed",
                    "error_code": getattr(exc, "code", type(exc).__name__)[:120],
                },
            )
            return False

    async def _skip_remaining(
        self,
        deployment: Mapping[str, Any],
        host_ids: Iterable[str],
    ) -> None:
        for host_id in host_ids:
            try:
                await self.client.update_target(
                    str(deployment["deployment_id"]),
                    host_id,
                    {
                        "fence": int(deployment["fence"]),
                        "status": "skipped",
                        "step": "paused-after-failure",
                    },
                )
            except Exception:
                logger.exception("Could not mark deployment target %s skipped", host_id)

    async def _apply(
        self,
        deployment: dict[str, Any],
        guard: LeaseGuard,
    ) -> dict[str, Any]:
        preflight = deployment["preflight"]
        if preflight.get("preflight_hash") != content_hash(
            {key: value for key, value in preflight.items() if key != "preflight_hash"}
        ):
            raise ValueError("saved preflight evidence checksum does not match")
        worktree, revision_evidence = await self.repository.checkout(deployment, guard)
        if any(
            revision_evidence[key] != preflight.get(key)
            for key in ("resolved_revision", "remote_revision", "flake_lock_sha256")
        ):
            raise ValueError("repository changed after deployment approval")
        targets = {str(item["host_id"]): item for item in deployment["target_configs"]}
        evidence = preflight.get("targets")
        if not isinstance(evidence, dict):
            raise ValueError("preflight target evidence is missing")
        ordered_hosts = [str(item) for item in deployment["target_hosts"]]
        try:
            built: dict[str, str] = {}
            for host_id in ordered_hosts:
                guard.check()
                target = targets[host_id]
                approved = evidence.get(host_id)
                if not isinstance(approved, dict):
                    raise ValueError(f"preflight evidence is missing for {host_id}")
                current = await self.hosts.current_toplevel(target, guard)
                if current != approved.get("current_toplevel"):
                    raise ValueError(f"{host_id} changed after preflight")
                flake_host = str(target["flake_host"])
                desired = built.get(flake_host)
                if desired is None:
                    desired = await self.repository.build_target(worktree, target, guard)
                    built[flake_host] = desired
                if desired != approved.get("target_toplevel"):
                    raise ValueError(f"{host_id} target closure changed after approval")

            deployed: list[str] = []
            unknown_hosts: list[str] = []
            failed_host = ""
            failure_code = ""
            try:
                for index, host_id in enumerate(ordered_hosts):
                    guard.check()
                    desired = str(evidence[host_id]["target_toplevel"])
                    try:
                        await self._activate_target(
                            deployment, targets[host_id], desired, guard
                        )
                        deployed.append(host_id)
                    except TargetFailure as exc:
                        failed_host = exc.host_id
                        failure_code = exc.code
                        if exc.side_effect_unknown:
                            unknown_hosts.append(host_id)
                        await self._skip_remaining(deployment, ordered_hosts[index + 1 :])
                        break
            except DeploymentCancelled:
                remaining = [host for host in ordered_hosts if host not in deployed]
                await self._skip_remaining(deployment, remaining)
                failed_host = "cancelled"
                failure_code = "cancelled"

            rollback_results: dict[str, bool] = {}
            if (
                failed_host
                and failure_code != "cancelled"
                and not unknown_hosts
                and deployment["failure_policy"] == "rollback_deployed"
            ):
                for host_id in reversed(deployed):
                    rollback_results[host_id] = await self._rollback_target(
                        deployment,
                        targets[host_id],
                        str(evidence[host_id]["current_toplevel"]),
                        guard,
                    )
            if failed_host:
                return {
                    "error_code": failure_code,
                    "failed_host": failed_host,
                    "deployed_hosts": deployed,
                    "unknown_hosts": unknown_hosts,
                    "rollback_results": rollback_results,
                }
            return {
                "deployed_hosts": deployed,
                "verified": True,
                "strategy": deployment["strategy"],
            }
        finally:
            self.repository.cleanup(worktree)
