from __future__ import annotations

import time
from typing import Any, Mapping

from .deployment_contracts import DeploymentProposal, SHA256_RE
from .deployment_storage import DeploymentStore
from .execution_contracts import bounded_object, content_hash, new_handle


class DeploymentService:
    """Validate deployment intent against server-owned repository and host policy."""

    def __init__(
        self,
        store: DeploymentStore,
        *,
        repositories: tuple[dict[str, object], ...],
        deployer_repositories: Mapping[str, tuple[str, ...]],
    ) -> None:
        self.store = store
        self.repositories = {
            str(item["repository_id"]): dict(item) for item in repositories
        }
        self.deployer_repositories = {
            str(key): tuple(value) for key, value in deployer_repositories.items()
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": bool(self.repositories and self.deployer_repositories),
            "two_phase": True,
            "arbitrary_commands": False,
            "strategies": ["serial", "canary"],
            "failure_policies": ["pause", "rollback_deployed"],
            "repositories": [
                {
                    "repository_id": repository_id,
                    "resource_version": int(repository["resource_version"]),
                    "allowed_changes": list(repository["allowed_changes"]),
                    "targets": [
                        {
                            "host_id": target["host_id"],
                            "resource_version": target["resource_version"],
                            "verification_units": target["verification_units"],
                        }
                        for target in repository["targets"]
                    ],
                }
                for repository_id, repository in sorted(self.repositories.items())
            ],
        }

    def _repository(self, repository_id: str) -> dict[str, object]:
        repository = self.repositories.get(repository_id)
        if repository is None:
            raise PermissionError("deployment repository is not configured")
        return repository

    def prepare(
        self,
        raw: Mapping[str, Any],
        *,
        actor_id: str,
        origin_scope: str,
    ) -> dict[str, Any]:
        if not actor_id.startswith("admin:"):
            raise PermissionError("only an administrator can prepare deployments")
        now = int(time.time())
        proposal = DeploymentProposal.parse(raw, now=now)
        repository = self._repository(proposal.repository_id)
        targets = {
            str(item["host_id"]): item for item in repository["targets"]
        }
        unknown_hosts = set(proposal.target_hosts) - set(targets)
        unknown_changes = set(proposal.requested_changes) - set(
            repository["allowed_changes"]
        )
        if unknown_hosts:
            raise PermissionError("one or more deployment targets are not allowed")
        if unknown_changes:
            raise PermissionError("one or more requested changes are not allowed")
        contract = proposal.contract(
            actor_id=actor_id,
            origin_scope=origin_scope,
            repository_version=int(repository["resource_version"]),
            target_versions={
                host: int(targets[host]["resource_version"])
                for host in proposal.target_hosts
            },
        )
        record = {
            **contract,
            "deployment_id": new_handle("deploy"),
            "contract": contract,
            "contract_hash": content_hash(contract),
            "created_at": now,
        }
        ordered_targets = [targets[host] for host in contract["target_hosts"]]
        return self.store.prepare(record, ordered_targets)

    def visible(
        self,
        deployment_id: str,
        *,
        actor_id: str,
        origin_scope: str,
    ) -> dict[str, Any] | None:
        item = self.store.get(deployment_id)
        if item is None or actor_id.startswith("admin:"):
            return item
        if item["actor_id"] != actor_id or item["origin_scope"] != origin_scope:
            raise PermissionError("deployment belongs to another scope")
        return item

    def approve(
        self,
        deployment_id: str,
        *,
        actor_id: str,
        contract_hash: str,
        resource_version: int,
    ) -> dict[str, Any]:
        return self.store.approve(
            deployment_id,
            actor_id=actor_id,
            contract_hash=contract_hash,
            resource_version=resource_version,
            expires_at=int(time.time()) + 600,
        )

    def claim(self, deployer_id: str) -> dict[str, Any] | None:
        allowed = self.deployer_repositories.get(deployer_id, ())
        item = self.store.claim(deployer_id, repository_ids=allowed)
        if item is None:
            return None
        repository = self._repository(str(item["repository_id"]))
        contract = item["contract"]
        targets = {
            str(target["host_id"]): target for target in repository["targets"]
        }
        current_versions = {
            host: int(targets[host]["resource_version"])
            for host in item["target_hosts"]
            if host in targets
        }
        if (
            int(contract.get("repository_version") or 0)
            != int(repository["resource_version"])
            or current_versions != contract.get("target_versions")
        ):
            self.store.complete_phase(
                str(item["deployment_id"]),
                deployer_id=deployer_id,
                fence=int(item["fence"]),
                ok=False,
                result={"error_code": "deployment_policy_changed"},
            )
            raise PermissionError("deployment policy changed after preparation")
        return {
            **item,
            "repository": {
                "repository_id": repository["repository_id"],
                "url": repository["url"],
                "default_branch": repository["default_branch"],
            },
            "target_configs": [targets[host] for host in item["target_hosts"]],
        }

    def complete(
        self,
        deployment_id: str,
        *,
        deployer_id: str,
        fence: int,
        ok: bool,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        current = self.store.get(deployment_id)
        if current is None:
            raise LookupError("deployment not found")
        clean_result = bounded_object(result, field="deployment result", max_bytes=64_000)
        if current["phase"] == "preflight" and ok:
            if (
                clean_result.get("resolved_revision") != current["source_revision"]
                or clean_result.get("remote_revision")
                != current["expected_remote_revision"]
                or SHA256_RE.fullmatch(
                    str(clean_result.get("flake_lock_sha256") or "")
                )
                is None
            ):
                ok = False
                clean_result = {
                    **clean_result,
                    "error_code": "preflight_revision_mismatch",
                }
            else:
                clean_result["preflight_hash"] = content_hash(clean_result)
        return self.store.complete_phase(
            deployment_id,
            deployer_id=deployer_id,
            fence=fence,
            ok=ok,
            result=clean_result,
        )

    def update_target(
        self,
        deployment_id: str,
        host_id: str,
        *,
        deployer_id: str,
        fence: int,
        status: str,
        step: str,
        current_toplevel: str = "",
        target_toplevel: str = "",
        actual_toplevel: str = "",
        verification: Mapping[str, Any] | None = None,
        error_code: str = "",
    ) -> dict[str, Any]:
        current = self.store.get(deployment_id)
        if current is None:
            raise LookupError("deployment not found")
        if host_id not in current["target_hosts"]:
            raise PermissionError("host is not part of this deployment")
        for name, value in (
            ("current_toplevel", current_toplevel),
            ("target_toplevel", target_toplevel),
            ("actual_toplevel", actual_toplevel),
        ):
            if value and (
                not value.startswith("/nix/store/")
                or len(value) > 500
                or any(ch in value for ch in ("\n", "\r", "\x00"))
            ):
                raise ValueError(f"invalid {name}")
        return self.store.update_target(
            deployment_id,
            host_id,
            deployer_id=deployer_id,
            fence=fence,
            status=status,
            step=step,
            current_toplevel=current_toplevel,
            target_toplevel=target_toplevel,
            actual_toplevel=actual_toplevel,
            verification=bounded_object(
                verification or {},
                field="deployment verification",
                max_bytes=32_000,
            ),
            error_code=error_code,
        )
