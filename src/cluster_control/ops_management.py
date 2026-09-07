"""Catalog-backed operations with a separate credential and host-owned approval."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from jsonschema import Draft202012Validator, ValidationError

from .adapters.ops import OpsClient, OpsError
from .execution_contracts import canonical_json, content_hash, new_handle
from .execution_storage import ClusterExecutionStore


class OpsManagementService:
    def __init__(self, client: OpsClient, store: ClusterExecutionStore, *,
                 hosts: tuple[str, ...], actors: tuple[str, ...]) -> None:
        self.client = client
        self.store = store
        self.hosts = frozenset(hosts)
        self.actors = frozenset(actors)
        self.owner = uuid.uuid4().hex
        self.closed = False
        self.guardian_validator: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    def authorize(self, actor: str) -> None:
        if actor not in self.actors:
            raise PermissionError("This identity is not an operations administrator")

    async def definitions(self) -> list[dict[str, Any]]:
        result = await self.client._request("GET", "/v1/operations")
        data = result.data
        if not isinstance(data, dict) or data.get("version") != 2:
            raise OpsError("incompatible_catalog", "Management requires Ops protocol 2")
        definitions = data.get("operations")
        if not isinstance(definitions, list) or any(
            not isinstance(d, dict) or not isinstance(d.get("name"), str)
            or not isinstance(d.get("read_only"), bool)
            or not isinstance(d.get("params_schema"), dict)
            or d.get("idempotency") not in {"none", "required"}
            for d in definitions
        ):
            raise OpsError("invalid_catalog", "Invalid management operation catalog")
        return definitions

    async def catalog(self, actor: str, operation: str = "") -> dict[str, Any]:
        self.authorize(actor)
        definitions = await self.definitions()
        if operation:
            definitions = [d for d in definitions if d["name"] == operation]
            if not definitions:
                raise LookupError("Operation is not granted by the upstream catalog")
        else:
            definitions = [{k: d.get(k) for k in
                ("name", "summary", "read_only", "kind", "idempotency")} for d in definitions]
        return {"version": 2, "hosts": sorted(self.hosts), "operations": definitions,
                "writes_require_approval": True}

    def binding_hash(self, definition: dict[str, Any]) -> str:
        # Credential rotation or a changed schema/scope invalidates an old approval.
        return content_hash({"definition": definition, "hosts": sorted(self.hosts),
            "actors": sorted(self.actors), "url": self.client.base_url,
            "identity": hashlib.sha256(self.client._credential()).hexdigest()})

    async def call(self, operation: str, params: dict[str, Any], *, actor: str,
                   origin: str, idempotency_key: str = "", guardian_id: str = "") -> dict[str, Any]:
        self.authorize(actor)
        definition = next((d for d in await self.definitions() if d["name"] == operation), None)
        if definition is None:
            raise PermissionError("Operation is not granted by the upstream catalog")
        if len(canonical_json(params).encode()) > 256 * 1024:
            raise ValueError("Operation parameters exceed 256 KiB")
        try:
            Draft202012Validator(definition["params_schema"]).validate(params)
        except ValidationError as exc:
            raise ValueError(f"Invalid operation parameters: {exc.message[:500]}") from None
        if params.get("host") and params["host"] not in self.hosts:
            raise PermissionError("Host is outside the management grant")
        if definition["read_only"]:
            response = await self.client._request("POST", "/v1/execute",
                body=canonical_json({"op": operation, "params": params}).encode())
            return {"ok": True, "operation": operation, "result": response.data}
        if not 8 <= len(idempotency_key) <= 160:
            raise ValueError("Writes require a stable 8-160 character idempotency key")
        arguments = {"op": operation, "params": params,
                     "binding_hash": self.binding_hash(definition),
                     "upstream_idempotency": definition["idempotency"]}
        if guardian_id:
            arguments["guardian_id"] = guardian_id
        intent_hash = content_hash({"actor": actor, "origin": origin, "arguments": arguments})
        existing = await asyncio.to_thread(self.store.find_operation, actor, origin, idempotency_key)
        if existing is not None:
            if existing.get("contract_hash") != intent_hash:
                raise ValueError("Idempotency key belongs to a different approved intent")
            return self.proposal_result(existing)
        now = int(time.time())
        record = {"operation_id": new_handle("op"), "task_ref": "", "step_ref": "",
            "actor_id": actor, "origin_scope": origin, "host_id": params.get("host", "scoped-resource"),
            "resource_ref": operation, "operation": "maxops.execute", "operation_version": 1,
            "arguments": arguments, "backend_ref": "ops-management-v2", "backend_binding_version": 1,
            "expected_state": {}, "resource_version": 1, "policy_version": 1,
            "deadline_at": now + 1800, "resource_budget": {"wall_seconds": 1800},
            "idempotency_key": idempotency_key, "verification": {}, "compensation": {},
            "contract_hash": intent_hash, "status": "awaiting_approval", "capability_status": "available",
            "created_at": now}
        saved = await asyncio.to_thread(self.store.prepare_operation, record)
        return self.proposal_result(saved)

    @staticmethod
    def proposal_result(record: dict[str, Any]) -> dict[str, Any]:
        awaiting = record["status"] == "awaiting_approval"
        return {"ok": True, "executed": record["status"] == "succeeded", "approval_required": awaiting, "operation": record,
                "next_action": (
                    "Administrator must review the exact parameters and approve in the console. Do not claim execution."
                    if awaiting else "Inspect this existing operation; do not submit the same effect under a new key."
                )}

    async def approve(self, operation_id: str, *, actor: str, expected_hash: str,
                      expected_version: int) -> dict[str, Any]:
        self.authorize(actor)
        record = await asyncio.to_thread(self.store.get_operation, operation_id)
        if not record or record["operation"] != "maxops.execute":
            raise LookupError("Management proposal not found")
        if record["arguments"].get("guardian_id"):
            raise PermissionError("Guardian actions must consume their bounded guardian authorization")
        await self.validate_binding(record)
        return await asyncio.to_thread(self.store.approve_operation, operation_id, actor_id=actor,
            expected_hash=expected_hash, expected_version=expected_version, expires_at=int(time.time()) + 300)

    async def validate_binding(self, record: dict[str, Any]) -> dict[str, Any]:
        self.authorize(record["actor_id"])
        arguments = record["arguments"]
        definition = next((d for d in await self.definitions() if d["name"] == arguments["op"]), None)
        if definition is None or self.binding_hash(definition) != arguments["binding_hash"]:
            raise PermissionError("Management scope or operation schema changed; prepare a new request")
        return definition

    async def run_once(self) -> bool:
        record = await asyncio.to_thread(self.store.claim_managed_operation, self.owner)
        if record is None:
            return False
        status, result, error, backend_id = "needs_attention", {}, "", record.get("backend_operation_id")
        try:
            definition = await self.validate_binding(record)
            if backend_id:
                response = await self.client._request("POST", "/v1/execute",
                    body=canonical_json({"op": "jobs.status", "params": {"job_id": backend_id}}).encode())
                result = response.data
                handle = result.get("handle", {})
                state = handle.get("state")
                if state not in {"queued", "dispatching", "running", "reconciling", "succeeded", "failed", "cancelled", "timed_out", "outcome_unknown"}:
                    raise ValueError("Upstream returned an unknown job state")
                status = {"succeeded": "succeeded", "failed": "failed", "cancelled": "cancelled",
                          "timed_out": "failed", "outcome_unknown": "needs_attention"}.get(state, "running")
                if record["status"] == "cancelling" and status == "running":
                    cancelled = await self.client._request("POST", "/v1/execute", body=canonical_json({
                        "op": "jobs.cancel", "params": {"job_id": backend_id,
                        "expected_revision": handle["revision"], "reason": "Administrator cancellation"}}).encode())
                    result = cancelled.data
                    status = "cancelling"
            else:
                if record["arguments"].get("guardian_id"):
                    if self.guardian_validator is None:
                        raise PermissionError("Guardian authorization backend is unavailable")
                    await self.guardian_validator(record)
                # Persist the claim before any effect; lost submissions are never blindly replayed.
                response = await self.client._request("POST", "/v1/execute",
                    body=canonical_json({"op": record["arguments"]["op"],
                                         "params": record["arguments"]["params"]}).encode(),
                    idempotency_key=(record["operation_id"] if definition["idempotency"] == "required" else None))
                result = response.data
                if definition.get("kind") == "job_submission":
                    backend_id = result.get("job_id") if isinstance(result, dict) else None
                    if not backend_id:
                        raise ValueError("Upstream accepted a submission without a durable job handle")
                    status = "running"
                else:
                    status = "succeeded"
        except (OpsError, PermissionError, ValueError, KeyError, TypeError) as exc:
            error = getattr(exc, "code", type(exc).__name__)
            result = {"error": str(exc)[:1000], "upstream_idempotency_key": record["operation_id"],
                      "instruction": "Check upstream jobs before retrying; the effect may already have happened."}
            # An unavailable observation must not turn an existing remote job into a failure.
            if backend_id and isinstance(exc, OpsError) and exc.retryable and record["deadline_at"] > int(time.time()):
                status = record["status"] if record["status"] == "cancelling" else "reconciling"
            elif isinstance(exc, PermissionError):
                status = "needs_attention"
        await asyncio.to_thread(self.store.finish_managed_operation, record["operation_id"],
            owner=self.owner, fence=record["fence"], status=status, result=result,
            backend_id=backend_id, error=error)
        return True

    async def run(self) -> None:
        while not self.closed:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger(__name__).exception("Management reconciliation failed")
            await asyncio.sleep(2)

    async def close(self) -> None:
        self.closed = True
        await self.client.close()
