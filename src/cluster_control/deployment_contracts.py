from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .execution_contracts import HOST_RE, ID_RE, content_hash


REVISION_RE = re.compile(r"[a-f0-9]{40}")
SHA256_RE = re.compile(r"[a-f0-9]{64}")
REPOSITORY_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
CHANGE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,159}")
DEPLOYMENT_STRATEGIES = frozenset({"serial", "canary"})
FAILURE_POLICIES = frozenset({"pause", "rollback_deployed"})


@dataclass(frozen=True)
class DeploymentProposal:
    repository_id: str
    source_revision: str
    expected_remote_revision: str
    target_hosts: tuple[str, ...]
    requested_changes: tuple[str, ...]
    strategy: str
    canary_host_id: str
    failure_policy: str
    deadline_at: int
    idempotency_key: str

    @classmethod
    def parse(
        cls,
        raw: Mapping[str, Any],
        *,
        now: int | None = None,
    ) -> "DeploymentProposal":
        timestamp = int(time.time() if now is None else now)
        repository_id = str(raw.get("repository_id") or "").strip()
        source_revision = str(raw.get("source_revision") or "").strip().lower()
        expected_remote_revision = str(
            raw.get("expected_remote_revision") or source_revision
        ).strip().lower()
        target_hosts_raw = raw.get("target_hosts")
        changes_raw = raw.get("requested_changes")
        strategy = str(raw.get("strategy") or "serial").strip().lower()
        failure_policy = str(raw.get("failure_policy") or "pause").strip().lower()
        canary = str(raw.get("canary_host_id") or "").strip()
        idempotency_key = str(raw.get("idempotency_key") or "").strip()
        if REPOSITORY_RE.fullmatch(repository_id) is None:
            raise ValueError("invalid repository_id")
        if REVISION_RE.fullmatch(source_revision) is None:
            raise ValueError("source_revision must be an exact 40-character Git commit")
        if REVISION_RE.fullmatch(expected_remote_revision) is None:
            raise ValueError("expected_remote_revision must be an exact Git commit")
        if not isinstance(target_hosts_raw, list) or not 1 <= len(target_hosts_raw) <= 8:
            raise ValueError("target_hosts must contain between one and eight hosts")
        target_hosts = tuple(str(item).strip() for item in target_hosts_raw)
        if (
            len(set(target_hosts)) != len(target_hosts)
            or any(HOST_RE.fullmatch(item) is None for item in target_hosts)
        ):
            raise ValueError("target_hosts contains an invalid or duplicate host")
        if not isinstance(changes_raw, list) or not 1 <= len(changes_raw) <= 32:
            raise ValueError("requested_changes must contain between one and 32 entries")
        requested_changes = tuple(str(item).strip() for item in changes_raw)
        if (
            len(set(requested_changes)) != len(requested_changes)
            or any(CHANGE_RE.fullmatch(item) is None for item in requested_changes)
        ):
            raise ValueError("requested_changes contains an invalid or duplicate entry")
        if strategy not in DEPLOYMENT_STRATEGIES:
            raise ValueError("unsupported deployment strategy")
        if failure_policy not in FAILURE_POLICIES:
            raise ValueError("unsupported deployment failure policy")
        if strategy == "canary":
            if canary not in target_hosts:
                raise ValueError("canary_host_id must be one of target_hosts")
        elif canary:
            raise ValueError("canary_host_id is only valid for canary deployments")
        deadline_at = int(raw.get("deadline_at") or timestamp + 3600)
        if deadline_at <= timestamp or deadline_at > timestamp + 86_400:
            raise ValueError("deployment deadline must be within the next day")
        if (
            not 8 <= len(idempotency_key) <= 160
            or any(ch.isspace() for ch in idempotency_key)
            or ID_RE.fullmatch(idempotency_key) is None
        ):
            raise ValueError("invalid idempotency_key")
        return cls(
            repository_id=repository_id,
            source_revision=source_revision,
            expected_remote_revision=expected_remote_revision,
            target_hosts=target_hosts,
            requested_changes=requested_changes,
            strategy=strategy,
            canary_host_id=canary,
            failure_policy=failure_policy,
            deadline_at=deadline_at,
            idempotency_key=idempotency_key,
        )

    def contract(
        self,
        *,
        actor_id: str,
        origin_scope: str,
        repository_version: int,
        target_versions: Mapping[str, int],
    ) -> dict[str, Any]:
        targets = list(self.target_hosts)
        if self.strategy == "canary":
            targets.remove(self.canary_host_id)
            targets.insert(0, self.canary_host_id)
        return {
            "contract_version": 1,
            "actor_id": actor_id,
            "origin_scope": origin_scope,
            "repository_id": self.repository_id,
            "repository_version": int(repository_version),
            "source_revision": self.source_revision,
            "expected_remote_revision": self.expected_remote_revision,
            "target_hosts": targets,
            "target_versions": {
                host: int(target_versions[host]) for host in targets
            },
            "requested_changes": list(self.requested_changes),
            "strategy": self.strategy,
            "canary_host_id": self.canary_host_id,
            "failure_policy": self.failure_policy,
            "deadline_at": self.deadline_at,
            "idempotency_key": self.idempotency_key,
        }


def approval_contract_hash(
    contract: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> str:
    return content_hash(
        {
            "deployment_contract": dict(contract),
            "preflight": dict(preflight),
        }
    )
