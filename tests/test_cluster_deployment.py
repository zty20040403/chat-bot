from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from src.cluster_control.api_deployments import build_deployment_router
from src.cluster_control.auth import CredentialFileAuthenticator
from src.cluster_control.deployment_contracts import (
    DeploymentProposal,
    approval_contract_hash,
)
from src.cluster_control.deployment_service import DeploymentService
from src.cluster_control.deployment_storage import TARGET_TRANSITIONS
from src.cluster_control.execution_contracts import content_hash
from src.cluster_deployer.config import DeployerSettings
from src.cluster_deployer.nix_host import NixHostOperator
from src.cluster_deployer.repository import RepositoryWorkspace
from src.cluster_deployer.runtime import CommandResult
from src.cluster_deployer.service import ClusterDeployer


REVISION = "a" * 40
REMOTE_REVISION = "b" * 40
STORE_OLD = "/nix/store/" + "a" * 32 + "-nixos-system-h610-old"
STORE_NEW = "/nix/store/" + "b" * 32 + "-nixos-system-h610-new"


def repository() -> dict[str, object]:
    return {
        "repository_id": "nix_config",
        "url": "https://github.com/example/nix-config.git",
        "default_branch": "main",
        "resource_version": 3,
        "allowed_changes": ["kennethbot", "system"],
        "targets": [
            {
                "host_id": "h610",
                "flake_host": "h610",
                "ssh_target": "kenneth@h610",
                "verification_units": ["qq-deepseek-bot.service"],
                "use_remote_sudo": True,
                "resource_version": 4,
            },
            {
                "host_id": "tank",
                "flake_host": "tank",
                "ssh_target": "kenneth@tank",
                "verification_units": [],
                "use_remote_sudo": True,
                "resource_version": 2,
            },
        ],
    }


class FakeStore:
    def __init__(self) -> None:
        self.prepared: dict[str, Any] | None = None
        self.claimed: dict[str, Any] | None = None
        self.completed: list[dict[str, Any]] = []

    def prepare(self, record: dict[str, Any], targets: list[dict[str, Any]]) -> dict[str, Any]:
        self.prepared = {**record, "targets": targets}
        return dict(self.prepared)

    def claim(self, _deployer_id: str, *, repository_ids: tuple[str, ...]) -> dict[str, Any] | None:
        if self.claimed and self.claimed["repository_id"] in repository_ids:
            return dict(self.claimed)
        return None

    def complete_phase(self, deployment_id: str, **kwargs: Any) -> dict[str, Any]:
        self.completed.append({"deployment_id": deployment_id, **kwargs})
        return self.completed[-1]


class FakeDeploymentService:
    def __init__(self) -> None:
        self.claimed_by: list[str] = []

    def claim(self, deployer_id: str) -> None:
        self.claimed_by.append(deployer_id)
        return None


class DeploymentContractTests(unittest.TestCase):
    def test_canary_is_first_and_contract_uses_exact_commits(self) -> None:
        proposal = DeploymentProposal.parse(
            {
                "repository_id": "nix_config",
                "source_revision": REVISION,
                "expected_remote_revision": REMOTE_REVISION,
                "target_hosts": ["h610", "tank"],
                "requested_changes": ["kennethbot"],
                "strategy": "canary",
                "canary_host_id": "tank",
                "failure_policy": "pause",
                "deadline_at": int(time.time()) + 300,
                "idempotency_key": "deploy-canary-0001",
            }
        )
        contract = proposal.contract(
            actor_id="admin:kenneth",
            origin_scope="admin-console",
            repository_version=3,
            target_versions={"h610": 4, "tank": 2},
        )
        self.assertEqual(contract["target_hosts"], ["tank", "h610"])
        self.assertEqual(contract["source_revision"], REVISION)
        with self.assertRaises(ValueError):
            DeploymentProposal.parse(
                {
                    "repository_id": "nix_config",
                    "source_revision": "main",
                    "target_hosts": ["h610"],
                    "requested_changes": ["system"],
                    "idempotency_key": "deploy-branch-0001",
                }
            )

    def test_service_rejects_unknown_hosts_and_changes(self) -> None:
        service = DeploymentService(
            FakeStore(),  # type: ignore[arg-type]
            repositories=(repository(),),
            deployer_repositories={"h610-deployer": ("nix_config",)},
        )
        base = {
            "repository_id": "nix_config",
            "source_revision": REVISION,
            "target_hosts": ["h610"],
            "requested_changes": ["kennethbot"],
            "idempotency_key": "deploy-policy-0001",
        }
        with self.assertRaises(PermissionError):
            service.prepare(
                {**base, "target_hosts": ["unknown"]},
                actor_id="admin:kenneth",
                origin_scope="admin-console",
            )
        with self.assertRaises(PermissionError):
            service.prepare(
                {**base, "requested_changes": ["arbitrary-shell"]},
                actor_id="admin:kenneth",
                origin_scope="admin-console",
            )

    def test_policy_version_change_invalidates_a_claim(self) -> None:
        store = FakeStore()
        store.claimed = {
            "deployment_id": "deploy_" + "c" * 32,
            "repository_id": "nix_config",
            "target_hosts": ["h610"],
            "contract": {
                "repository_version": 2,
                "target_versions": {"h610": 4},
            },
            "fence": 1,
        }
        service = DeploymentService(
            store,  # type: ignore[arg-type]
            repositories=(repository(),),
            deployer_repositories={"h610-deployer": ("nix_config",)},
        )
        with self.assertRaises(PermissionError):
            service.claim("h610-deployer")
        self.assertEqual(store.completed[0]["result"]["error_code"], "deployment_policy_changed")

    def test_target_state_machine_cannot_move_backwards(self) -> None:
        self.assertNotIn("pending", TARGET_TRANSITIONS["succeeded"])
        self.assertNotIn("deploying", TARGET_TRANSITIONS["failed"])
        self.assertEqual(TARGET_TRANSITIONS["unknown"], {"unknown", "rollback_pending"})


class FakeClient:
    def __init__(self) -> None:
        self.updates: list[tuple[str, str, dict[str, Any]]] = []

    async def update_target(self, deployment_id: str, host_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.updates.append((deployment_id, host_id, payload))
        return {}

    async def close(self) -> None:
        return None


class FakeGuard:
    def check(self) -> None:
        return None


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    async def run(
        self,
        command: list[str],
        _guard: Any,
        *,
        timeout: int | None = None,
    ) -> CommandResult:
        self.commands.append(list(command))
        return CommandResult(stdout=STORE_NEW + "\n", stderr="", returncode=0)


class FakeRepositoryWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def checkout(
        self,
        _deployment: dict[str, Any],
        _guard: Any,
    ) -> tuple[Path, dict[str, str]]:
        return self.root, {
            "resolved_revision": REVISION,
            "remote_revision": REMOTE_REVISION,
            "flake_lock_sha256": "d" * 64,
        }

    async def build_target(
        self,
        _worktree: Path,
        _target: dict[str, Any],
        _guard: Any,
    ) -> str:
        return STORE_NEW

    def cleanup(self, _worktree: Path) -> None:
        return None


class UnknownActivationHosts:
    def __init__(self) -> None:
        self.rollback_calls: list[str] = []

    async def current_toplevel(self, _target: dict[str, Any], _guard: Any) -> str:
        return STORE_OLD

    async def copy_closure(
        self,
        _target: dict[str, Any],
        _desired: str,
        _guard: Any,
    ) -> None:
        return None

    async def activate(
        self,
        _target: dict[str, Any],
        _desired: str,
        _guard: Any,
    ) -> None:
        raise RuntimeError("remote activation receipt timed out")

    async def rollback(
        self,
        _target: dict[str, Any],
        previous: str,
        _guard: Any,
    ) -> dict[str, Any]:
        self.rollback_calls.append(previous)
        return {"actual_toplevel": previous, "units": {}}

    async def ssh(
        self,
        _ssh_target: str,
        remote_command: list[str],
        _guard: Any,
        *,
        timeout: int = 90,
    ) -> CommandResult:
        self.commands.append(["ssh", *remote_command])
        return CommandResult(stdout=STORE_NEW + "\n", stderr="", returncode=0)


class DeployerSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        for name in ("token", "identity", "known_hosts"):
            (root / name).write_text("x" * 48, encoding="ascii")
        self.settings = DeployerSettings(
            deployer_id="h610-deployer",
            token_file=root / "token",
            control_url="http://127.0.0.1:8091",
            state_dir=root / "state",
            repository_id="nix_config",
            repository_url="https://github.com/example/nix-config.git",
            default_branch="main",
            targets=(repository()["targets"][0],),  # type: ignore[index]
            ssh_identity_file=root / "identity",
            ssh_known_hosts_file=root / "known_hosts",
            poll_seconds=5,
            command_timeout_seconds=300,
            executor_host_id="runner",
            allow_self_deployment=False,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _claim(self, phase: str) -> dict[str, Any]:
        contract = {
            "repository_id": "nix_config",
            "target_hosts": ["h610"],
            "deadline_at": int(time.time()) + 300,
        }
        preflight = {
            "resolved_revision": REVISION,
            "remote_revision": REMOTE_REVISION,
            "flake_lock_sha256": "d" * 64,
            "targets": {"h610": {"current_toplevel": STORE_OLD, "target_toplevel": STORE_NEW}},
        }
        preflight["preflight_hash"] = content_hash(preflight)
        return {
            "deployment_id": "deploy_" + "e" * 32,
            "phase": phase,
            "fence": 1,
            "repository_id": "nix_config",
            "source_revision": REVISION,
            "expected_remote_revision": REMOTE_REVISION,
            "target_hosts": ["h610"],
            "contract": contract,
            "preflight": preflight if phase == "apply" else {},
            "contract_hash": (
                approval_contract_hash(contract, preflight)
                if phase == "apply"
                else content_hash(contract)
            ),
            "repository": {
                "repository_id": "nix_config",
                "url": self.settings.repository_url,
                "default_branch": "main",
            },
            "target_configs": [dict(self.settings.targets[0])],
        }

    def test_local_allowlist_mismatch_rejects_claim(self) -> None:
        deployer = ClusterDeployer(self.settings, client=FakeClient())  # type: ignore[arg-type]
        claim = self._claim("preflight")
        claim["target_configs"][0]["ssh_target"] = "root@evil"
        with self.assertRaises(PermissionError):
            deployer._validate_claim(claim)

    def test_self_deployment_is_rejected_by_default(self) -> None:
        settings = replace(self.settings, executor_host_id="h610")
        deployer = ClusterDeployer(settings, client=FakeClient())  # type: ignore[arg-type]
        with self.assertRaises(PermissionError):
            deployer._validate_claim(self._claim("preflight"))

    async def test_nix_build_is_a_fixed_argument_vector(self) -> None:
        runner = RecordingRunner()
        repository_workspace = RepositoryWorkspace(
            self.settings,
            runner,  # type: ignore[arg-type]
        )
        result = await repository_workspace.build_target(
            self.settings.state_dir,
            dict(self.settings.targets[0]),
            FakeGuard(),  # type: ignore[arg-type]
        )
        self.assertEqual(result, STORE_NEW)
        self.assertEqual(runner.commands[0][0:4], ["nix", "build", "--no-link", "--print-out-paths"])
        self.assertNotIn("sh", runner.commands[0])

    async def test_rollback_rejects_non_store_paths_before_remote_calls(self) -> None:
        runner = RecordingRunner()
        host_operator = NixHostOperator(runner)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            await host_operator.rollback(
                dict(self.settings.targets[0]),
                "/tmp/not-approved",
                FakeGuard(),  # type: ignore[arg-type]
            )
        self.assertEqual(runner.commands, [])

    async def test_unknown_activation_is_not_automatically_rolled_back(self) -> None:
        client = FakeClient()
        deployer = ClusterDeployer(self.settings, client=client)  # type: ignore[arg-type]
        hosts = UnknownActivationHosts()
        deployer.repository = FakeRepositoryWorkspace(
            self.settings.state_dir
        )  # type: ignore[assignment]
        deployer.hosts = hosts  # type: ignore[assignment]
        deployment = self._claim("apply")
        deployment["failure_policy"] = "rollback_deployed"
        deployment["contract"]["failure_policy"] = "rollback_deployed"
        result = await deployer._apply(
            deployment,
            FakeGuard(),  # type: ignore[arg-type]
        )
        self.assertEqual(result["unknown_hosts"], ["h610"])
        self.assertEqual(result["rollback_results"], {})
        self.assertEqual(hosts.rollback_calls, [])
        self.assertTrue(
            any(payload[2].get("status") == "unknown" for payload in client.updates)
        )


class DeployerApiAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_bearer_credential_selects_server_owned_deployer_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "deployer"
            token_file.write_text("d" * 48, encoding="ascii")
            authenticator = CredentialFileAuthenticator(
                {"external-deployer": token_file}
            )
            service = FakeDeploymentService()
            app = FastAPI()
            app.include_router(
                build_deployment_router(
                    auth=[],
                    signed_principal=lambda: ("admin:test", "test"),
                    deployment_service=lambda: service,  # type: ignore[arg-type]
                    deployer_authenticator=authenticator,
                )
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://control.test",
            ) as client:
                self.assertEqual(
                    (await client.post("/v1/deployer/claim")).status_code,
                    401,
                )
                response = await client.post(
                    "/v1/deployer/claim",
                    headers={"Authorization": "Bearer " + "d" * 48},
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(service.claimed_by, ["external-deployer"])
