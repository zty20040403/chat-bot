from __future__ import annotations

import copy
import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone

from blake3 import blake3

from src.cluster_control.adapters.ops import OpsError
from src.cluster_control.deployment_ops_protocol import (
    DeploymentTargetBinding, OpsDeploymentProtocol, read_job_result,
)


COMMIT = "a" * 40
TREE = "b" * 40
OLD = "/nix/store/" + "a" * 32 + "-nixos-system-old"
NEW = "/nix/store/" + "b" * 32 + "-nixos-system-new"
DRV = NEW + ".drv"
LOCK = '{"nodes":{},"version":7}'


class OpsFixture:
    def __init__(self, host):
        self.host = host
        self.calls = []
        self.binding = DeploymentTargetBinding(host, f"nix-config-{host}", f"{host}-system",
            COMMIT, COMMIT, f"nixosConfigurations.{host}.config.system.build.toplevel")
        self.workspace = {"workspace_id": "workspace-test", "repository": self.binding.repository,
            "revision": 2, "tree_hash": TREE, "commit_hash": COMMIT, "base_commit": COMMIT,
            "state": "published"}
        plan = {"change_id": "change-test", "repository": self.binding.repository,
            "target_host": host, "deployment_profile": self.binding.profile, "kind": "system",
            "flake_attribute": self.binding.flake_attribute,
            "source_commit": COMMIT, "source_remote_head": COMMIT, "workspace_id": "workspace-test",
            "workspace_revision": 2, "tree_hash": TREE, "lock_digest": blake3(LOCK.encode()).hexdigest(),
            "drv_path": DRV, "runtime_baseline": {"host": host, "profile": self.binding.profile,
                "running_closure": OLD, "persistent_profile": OLD},
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
        self.change = {"revision": 4, "state": "ready", "plan": plan,
            "artifact": {"source_commit": COMMIT, "tree_hash": TREE, "drv_path": DRV,
                "out_path": NEW, "lock_digest": plan["lock_digest"]},
            "jobs": {"verify": "verify-test", "rollback": "rollback-test"}}
        self.result = {"action": "verify", "status": "verified",
            "observed_running": NEW, "observed_profile": NEW}
        self.job_state = "succeeded"
        self.page_patch = {}
        self.file_patch = {}

    async def read(self, operation, params):
        self.calls.append((operation, copy.deepcopy(params)))
        if operation == "workspace.status":
            return copy.deepcopy(self.workspace)
        if operation == "changes.status":
            return copy.deepcopy(self.change)
        if operation == "workspace.read":
            return {"workspace_id": "workspace-test", "revision": 2, "path": "flake.lock",
                "encoding": "utf-8", "content": LOCK, "digest": blake3(LOCK.encode()).hexdigest(),
                **self.file_patch}
        if operation == "jobs.status":
            return {"handle": {"job_id": params["job_id"], "state": self.job_state}}
        if operation == "jobs.result":
            data = json.dumps(self.result, ensure_ascii=False).encode()
            start = params["offset"]
            end = min(start + params["limit"], len(data))
            while True:
                try:
                    text = data[start:end].decode()
                    break
                except UnicodeDecodeError:
                    end -= 1
            return {"available": True, "job_id": params["job_id"], "pointer": params["pointer"],
                "encoding": "json_utf8", "text": text, "next_offset": end,
                "total_bytes": len(data), "complete": end == len(data), **self.page_patch}
        raise AssertionError(f"Unexpected operation {operation}")


class OpsDeploymentProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_hosts_exact_preflight_activate_and_verify(self):
        for host in ("h310", "h610", "tank"):
            fixture = OpsFixture(host)
            protocol = OpsDeploymentProtocol(fixture.read, fixture.binding)
            self.assertEqual(protocol.create_workspace_params()["expected_remote_head"], COMMIT)
            workspace = await protocol.workspace("workspace-test")
            self.assertEqual(protocol.prepare_params(workspace)["target_host"], host)
            evidence = await protocol.preflight("change-test")
            self.assertEqual(evidence["flake_lock_sha256"], hashlib.sha256(LOCK.encode()).hexdigest())
            self.assertNotEqual(evidence["flake_lock_sha256"], evidence["ops_lock_blake3"])
            params = await protocol.activation_params(evidence)
            self.assertEqual(params, {"change_id": "change-test", "expected_revision": 4, "until": "verified"})
            fixture.change.update(state="succeeded", revision=9)
            self.assertEqual((await protocol.verification(evidence))["actual_toplevel"], NEW)
            fixture.change.update(state="rolled_back", revision=11)
            fixture.result.update(action="rollback", status="rolled_back", observed_running=OLD, observed_profile=OLD)
            self.assertEqual((await protocol.verification(evidence, rollback=True))["actual_toplevel"], OLD)
            self.assertFalse(any(op.startswith("deploy.") for op, _ in fixture.calls))

    async def test_reject_wrong_host_profile_commit_tree_and_runtime_baseline(self):
        for section, key, value in [
            ("plan", "target_host", "b650"), ("plan", "deployment_profile", "other-system"),
            ("plan", "source_commit", "c" * 40), ("plan", "source_remote_head", "d" * 40),
            ("plan", "flake_attribute", "nixosConfigurations.other.config.system.build.toplevel"),
            ("artifact", "tree_hash", "c" * 40), ("artifact", "lock_digest", "d" * 64),
            ("artifact", "out_path", "/tmp/not-a-closure"),
        ]:
            fixture = OpsFixture("h310")
            fixture.change[section][key] = value
            with self.subTest(key=key), self.assertRaises(OpsError):
                await OpsDeploymentProtocol(fixture.read, fixture.binding).preflight("change-test")
        fixture = OpsFixture("tank")
        fixture.change["plan"]["runtime_baseline"]["host"] = "h610"
        with self.assertRaises(OpsError):
            await OpsDeploymentProtocol(fixture.read, fixture.binding).preflight("change-test")

    async def test_file_content_must_match_blake3_not_only_reported_digests(self):
        fixture = OpsFixture("h610")
        fixture.file_patch = {"content": '{"nodes":{"unreviewed":{}}}'}
        with self.assertRaises(OpsError):
            await OpsDeploymentProtocol(fixture.read, fixture.binding).preflight("change-test")

    async def test_changed_or_expired_preflight_cannot_activate(self):
        for mutation in ("revision", "expiry", "evidence"):
            fixture = OpsFixture("h610")
            protocol = OpsDeploymentProtocol(fixture.read, fixture.binding)
            if mutation == "expiry":
                fixture.change["plan"]["expires_at"] = "2000-01-01T00:00:00Z"
            evidence = await protocol.preflight("change-test")
            if mutation == "revision":
                fixture.change["revision"] += 1
            if mutation == "evidence":
                evidence["target_toplevel"] = OLD
            with self.subTest(mutation=mutation), self.assertRaises(OpsError):
                await protocol.activation_params(evidence)

    async def test_success_requires_actual_running_and_boot_profiles(self):
        fixture = OpsFixture("h610")
        protocol = OpsDeploymentProtocol(fixture.read, fixture.binding)
        evidence = await protocol.preflight("change-test")
        fixture.change["state"] = "succeeded"
        fixture.result["observed_profile"] = OLD
        with self.assertRaises(OpsError):
            await protocol.verification(evidence)
        fixture.result["observed_profile"] = NEW
        fixture.job_state = "running"
        with self.assertRaises(OpsError):
            await protocol.verification(evidence)

    async def test_clean_workspace_does_not_forge_a_synthetic_source_commit(self):
        fixture = OpsFixture("tank")
        fixture.workspace.update(state="clean", commit_hash=None)
        protocol = OpsDeploymentProtocol(fixture.read, fixture.binding)
        workspace = await protocol.workspace("workspace-test")
        params = protocol.prepare_params(workspace)
        self.assertEqual(params["workspace_id"], workspace["workspace_id"])
        self.assertEqual(params["expected_revision"], workspace["revision"])
        self.assertEqual([op for op, _ in fixture.calls], ["workspace.status"])
        fixture.workspace.update(state="committed", commit_hash="f" * 40)
        with self.assertRaises(OpsError):
            await protocol.workspace("workspace-test")

    async def test_paged_chinese_result_uses_byte_offsets(self):
        fixture = OpsFixture("h610")
        fixture.result = {"detail": "中文报告" * 18000}
        result = await read_job_result(fixture.read, "job-test")
        self.assertEqual(result, fixture.result)
        offsets = [params["offset"] for op, params in fixture.calls if op == "jobs.result"]
        self.assertGreater(len(offsets), 1)
        self.assertEqual(offsets, sorted(set(offsets)))

    async def test_paged_result_rejects_wrong_identity_offsets_size_and_completion(self):
        for patch in ({"job_id": "other"}, {"pointer": "/other"}, {"next_offset": 0},
                      {"complete": False}, {"total_bytes": 999999999}, {"encoding": "plain"}):
            fixture = OpsFixture("h610")
            fixture.page_patch = patch
            with self.subTest(patch=patch), self.assertRaises(OpsError):
                await read_job_result(fixture.read, "job-test")


if __name__ == "__main__":
    unittest.main()
