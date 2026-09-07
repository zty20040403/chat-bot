from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx

from src.cluster_control.adapters.ops import OpsClient, OpsError
from src.cluster_control.deployment_service import DeploymentService
from src.cluster_control.deployment_storage import DeploymentStore
from src.cluster_control.execution_storage import ClusterExecutionStore
from src.cluster_control.ops_deployment_runner import OpsDeploymentRunner
from src.cluster_control.ops_management import OpsManagementService
from test_deployment_ops_protocol import COMMIT, NEW, OLD, OpsFixture


HOSTS = ("h310", "h610", "tank")


def repository():
    return {"repository_id": "nix-config", "backend": "ops", "resource_version": 1,
        "url": "https://github.com/example/config.git", "default_branch": "main", "allowed_changes": ["gaoji"],
        "targets": [{"host_id": host, "flake_host": host, "resource_version": 1,
            "ops_repository": f"nix-config-{host}", "ops_profile": f"{host}-system", "verification_units": []}
            for host in HOSTS]}


class SimulatedOps:
    """Deterministic Ops transport, including a switch for the legacy clean-workspace rejection."""
    def __init__(self):
        self.fixtures = {host: OpsFixture(host) for host in HOSTS}
        self.jobs, self.changes, self.workspaces, self.effects = {}, {}, {}, []
        self.fail_host = None
        self.hold_host = None
        self.lose_receipt_host = None
        self.clean_workspace_supported = True

    async def request(self, request):
        if request.method == "GET":
            writes = ("workspace.create", "deploy.prepare", "deploy.run", "deploy.rollback")
            names = writes + ("jobs.status", "jobs.result", "changes.status", "workspace.status", "workspace.read")
            return httpx.Response(200, json={"version": 2, "operations": [
                {"name": name, "read_only": name not in writes,
                 "kind": "job_submission" if name in writes else "job_control",
                 "idempotency": "required" if name in writes else "none", "params_schema": {"type": "object"}}
                for name in names]})
        body = json.loads(request.content)
        op, params = body["op"], body["params"]
        if op == "jobs.status":
            job = self.jobs[params["job_id"]]
            return httpx.Response(200, json={"handle": {"job_id": params["job_id"], "revision": 1, "state": job["state"]}})
        if op == "jobs.result":
            value = self.jobs[params["job_id"]]["result"]
            if params["pointer"]:
                value = value[params["pointer"].removeprefix("/")]
            text = json.dumps(value, ensure_ascii=False)
            return httpx.Response(200, json={"available": True, "job_id": params["job_id"],
                "pointer": params["pointer"], "encoding": "json_utf8", "text": text,
                "next_offset": len(text.encode()), "total_bytes": len(text.encode()), "complete": True})
        if op == "changes.status":
            return httpx.Response(200, json=copy.deepcopy(self.changes[params["change_id"]].change))
        if op in {"workspace.status", "workspace.read"}:
            fixture = self.workspaces[params["workspace_id"]]
            return httpx.Response(200, json=await fixture.read(op, params))

        if op == "workspace.create":
            host = params["repository"].removeprefix("nix-config-")
        elif op == "deploy.prepare":
            host = params["target_host"]
            if not self.clean_workspace_supported:
                return httpx.Response(409, json={"error": "workspace is not committed"})
        else:
            host = self.changes[params["change_id"]].host
        fixture = self.fixtures[host]
        job_id = str(uuid.uuid4())
        self.effects.append((op, host, copy.deepcopy(params), request.headers["Idempotency-Key"]))
        state, result = "succeeded", {}
        if op == "workspace.create":
            fixture = OpsFixture(host)
            self.fixtures[host] = fixture
            workspace_id = f"workspace-{job_id}"
            fixture.workspace.update(workspace_id=workspace_id, state="clean", commit_hash=None,
                revision=1, base_commit=params.get("source_commit", COMMIT))
            fixture.file_patch.update(workspace_id=workspace_id, revision=1)
            self.workspaces[workspace_id] = fixture
            result = {"workspace": copy.deepcopy(fixture.workspace)}
        elif op == "deploy.prepare":
            fixture.change["plan"].update(change_id=job_id, workspace_id=params["workspace_id"],
                workspace_revision=fixture.workspace["revision"], source_commit=fixture.workspace["base_commit"])
            fixture.change.update(state="prepared", revision=1, jobs={})
            self.changes[job_id] = fixture
        elif op == "deploy.run" and params["until"] == "built":
            fixture.change.update(state="ready", revision=4)
        elif op == "deploy.run":
            if host == self.hold_host:
                state = "running"
            elif host == self.fail_host:
                state = "failed"
            else:
                self.verified(fixture, job_id)
        elif op == "deploy.rollback":
            fixture.change.update(state="rolled_back", revision=11)
            fixture.change["jobs"]["rollback"] = job_id
            result = {"deployment": {"action": "rollback", "status": "rolled_back",
                "observed_running": OLD, "observed_profile": OLD}}
        self.jobs[job_id] = {"state": state, "result": result}
        if op == "deploy.run" and params["until"] == "verified" and host == self.lose_receipt_host:
            raise httpx.ReadTimeout("injected lost activation receipt", request=request)
        return httpx.Response(200, json={"job_id": job_id, "revision": 1, "state": state})

    def verified(self, fixture, parent_job):
        fixture.change.update(state="succeeded", revision=9)
        job_id = f"verify-{parent_job}"
        fixture.change["jobs"]["verify"] = job_id
        self.jobs[job_id] = {"state": "succeeded", "result": {"deployment": {
            "action": "verify", "status": "verified", "observed_running": NEW, "observed_profile": NEW}}}


@unittest.skipUnless(os.getenv("TEST_OPS_POSTGRES_DSN"), "isolated PostgreSQL test not configured")
class OpsDeploymentPostgresTests(unittest.TestCase):
    def test_durable_three_host_rollout_failure_recovery_and_authorization(self):
        from alembic import command
        from alembic.config import Config
        import psycopg
        from psycopg import sql
        from src.bot_storage.database import PostgresDatabase

        dsn = os.environ["TEST_OPS_POSTGRES_DSN"]
        schema = "gaoji_p7_test_" + uuid.uuid4().hex[:12]
        database = None
        with tempfile.TemporaryDirectory() as directory:
            try:
                with patch.dict(os.environ, AI_POSTGRES_DSN=dsn, AI_POSTGRES_SCHEMA=schema):
                    command.upgrade(Config("alembic.ini"), "head")
                database = PostgresDatabase(dsn, schema=schema, min_size=1, max_size=6)
                clock = [time.time()]
                token = Path(directory) / "token"
                token.write_text("fixture-credential-" + "a" * 40)
                remote = SimulatedOps()
                management = OpsManagementService(OpsClient("http://ops.test", token,
                    transport=httpx.MockTransport(remote.request)), ClusterExecutionStore(database, Path(directory)),
                    hosts=HOSTS, actors=("admin:kenneth",))
                store = DeploymentStore(database)
                service = DeploymentService(store, repositories=(repository(),), deployer_repositories={})
                runner = OpsDeploymentRunner(service, management)

                async def exercise():
                    nonlocal runner
                    def prepare(key, failure_policy="pause"):
                        return service.prepare({"repository_id": "nix-config", "source_revision": COMMIT,
                            "target_hosts": list(HOSTS), "requested_changes": ["gaoji"], "strategy": "serial",
                            "failure_policy": failure_policy, "deadline_at": int(clock[0]) + 3600,
                            "idempotency_key": key}, actor_id="admin:kenneth", origin_scope="test")

                    async def tick():
                        clock[0] += 6
                        await runner.run_once()
                        await management.run_once()

                    async def until(item, predicate):
                        for _ in range(160):
                            value = store.get(item["deployment_id"])
                            if predicate(value):
                                return value
                            if value["phase"] == "complete":
                                self.fail(f"Unexpected terminal deployment: {value['status']} {value['result']}")
                            await tick()
                        self.fail(f"Deployment stalled: {store.get(item['deployment_id'])['result']}")

                    def approve(item):
                        return service.approve(item["deployment_id"], actor_id="admin:kenneth",
                            contract_hash=item["contract_hash"], resource_version=item["resource_version"])

                    item = prepare("three-host-success")
                    await until(item, lambda value: any(t["step"] == "prepare" for t in value["targets"]))
                    old_owner = runner.owner
                    clock[0] += 181
                    runner = OpsDeploymentRunner(service, management)
                    self.assertNotEqual(old_owner, runner.owner)
                    ready = await until(item, lambda value: value["status"] == "awaiting_approval")
                    self.assertEqual(len([effect for effect in remote.effects if effect[0] == "workspace.create"]), 3)
                    self.assertFalse(any(effect[2].get("until") == "verified" for effect in remote.effects))
                    with self.assertRaises(ValueError):
                        service.approve(item["deployment_id"], actor_id="admin:kenneth",
                            contract_hash="a" * 64, resource_version=ready["resource_version"])
                    approve(ready)
                    done = await until(item, lambda value: value["phase"] == "complete")
                    self.assertEqual(done["status"], "succeeded")
                    self.assertEqual([effect[1] for effect in remote.effects if effect[2].get("until") == "verified"], list(HOSTS))
                    self.assertTrue(all(t["actual_toplevel"] == NEW for t in done["targets"]))

                    start = len(remote.effects)
                    remote.fail_host = "h610"
                    item = prepare("three-host-rollback", "rollback_deployed")
                    approve(await until(item, lambda value: value["status"] == "awaiting_approval"))
                    done = await until(item, lambda value: value["phase"] == "complete")
                    self.assertEqual(done["status"], "rolled_back")
                    self.assertEqual([target["status"] for target in done["targets"]], ["rolled_back", "failed", "skipped"])
                    self.assertEqual([effect[1] for effect in remote.effects[start:] if effect[0] == "deploy.rollback"], ["h310"])
                    self.assertFalse(any(effect[1] == "tank" and effect[2].get("until") == "verified" for effect in remote.effects[start:]))

                    remote.fail_host = None
                    start = len(remote.effects)
                    item = prepare("validation-outage-before-submission")
                    approve(await until(item, lambda value: value["status"] == "awaiting_approval"))
                    validator = management.deployment_validator
                    async def unavailable_read(record):
                        binding = record["arguments"].get("deployment", {})
                        if binding.get("host_id") == "h610" and binding.get("stage") == "activate":
                            raise OpsError("upstream_error", "workspace.read returned 503", retryable=True)
                        await validator(record)
                    with patch.object(management, "deployment_validator", unavailable_read):
                        done = await until(item, lambda value: value["phase"] == "complete")
                    self.assertEqual(done["status"], "partial")
                    self.assertEqual([t["status"] for t in done["targets"]], ["succeeded", "failed", "skipped"])
                    self.assertFalse(any(effect[1] == "h610" and effect[2].get("until") == "verified"
                                         for effect in remote.effects[start:]))

                    item = prepare("cancel-before-dispatch")
                    approve(await until(item, lambda value: value["status"] == "awaiting_approval"))
                    await runner.run_once()  # Mark the first target deploying.
                    await runner.run_once()  # Approve, but do not dispatch, its child operation.
                    record = management.store.find_operation("admin:kenneth", f"deployment:{item['deployment_id']}",
                        f"{item['deployment_id']}:h310:activate")
                    self.assertEqual(record["status"], "queued")
                    with self.assertRaises(PermissionError):
                        await management.approve(record["operation_id"], actor="admin:kenneth",
                            expected_hash=record["contract_hash"], expected_version=record["resource_version"])
                    self.assertEqual(store.cancel(item["deployment_id"], actor_id="admin:kenneth", origin_scope="test")["status"], "cancelling")
                    self.assertEqual(store.cancel(item["deployment_id"], actor_id="admin:kenneth", origin_scope="test")["status"], "cancelling")
                    count = len(remote.effects)
                    await management.run_once()  # Dispatch must recheck the cancelled parent.
                    await until(item, lambda value: value["phase"] == "complete")
                    self.assertEqual(len(remote.effects), count)

                    remote.hold_host = "h310"
                    start = len(remote.effects)
                    item = prepare("restart-during-activation")
                    approve(await until(item, lambda value: value["status"] == "awaiting_approval"))
                    await until(item, lambda _: any(effect[2].get("until") == "verified" for effect in remote.effects[start:]))
                    record = management.store.find_operation("admin:kenneth", f"deployment:{item['deployment_id']}",
                        f"{item['deployment_id']}:h310:activate")
                    old_owner = runner.owner
                    runner = OpsDeploymentRunner(service, management)
                    self.assertFalse(await runner.run_once())  # The previous process still owns a live lease.
                    clock[0] += 181
                    await tick()
                    self.assertEqual(store.get(item["deployment_id"])["deployer_id"], runner.owner)
                    self.assertNotEqual(old_owner, runner.owner)
                    self.assertEqual(len([effect for effect in remote.effects[start:] if effect[2].get("until") == "verified"]), 1)
                    remote.jobs[record["backend_operation_id"]]["state"] = "succeeded"
                    remote.verified(remote.fixtures["h310"], record["backend_operation_id"])
                    remote.hold_host = None
                    done = await until(item, lambda value: value["phase"] == "complete")
                    self.assertEqual(done["status"], "succeeded")
                    self.assertEqual(len([effect for effect in remote.effects[start:] if effect[2].get("until") == "verified"]), 3)

                    remote.hold_host = "h610"
                    remote.lose_receipt_host = "h610"
                    start = len(remote.effects)
                    item = prepare("lost-receipt-no-blind-rollback", "rollback_deployed")
                    approve(await until(item, lambda value: value["status"] == "awaiting_approval"))
                    done = await until(item, lambda value: value["phase"] == "complete")
                    self.assertEqual(done["status"], "needs_attention")
                    self.assertEqual([t["status"] for t in done["targets"]][:2], ["succeeded", "unknown"])
                    self.assertFalse(any(effect[0] == "deploy.rollback" for effect in remote.effects[start:]))
                    self.assertEqual(len([effect for effect in remote.effects[start:] if effect[2].get("until") == "verified"]), 2)
                    remote.hold_host = remote.lose_receipt_host = None

                    remote.clean_workspace_supported = False
                    item = prepare("legacy-clean-source")
                    done = await until(item, lambda value: value["phase"] == "complete")
                    self.assertEqual(done["status"], "failed")
                    self.assertEqual(done["result"]["error_code"], "FailedStep")
                    await management.close()

                with patch("time.time", side_effect=lambda: clock[0]):
                    asyncio.run(exercise())
            finally:
                if database is not None:
                    database.close()
                with psycopg.connect(dsn, autocommit=True) as connection:
                    connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    unittest.main()
