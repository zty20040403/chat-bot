"""Bounded guardian authorizations executed by the same Ops backend as manual writes."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Mapping

from jsonschema import Draft202012Validator, ValidationError

from .execution_contracts import content_hash
from .guardian import (
    guardian_target_snapshot, materialize_guardian_action,
    resolve_guardian_target, validate_guardian_action,
)
from .ops_management import OpsManagementService
from .reliability import ReliabilityStore


def service_operation(action: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    operation = str(action.get("operation") or "")
    if operation not in {"service.start", "service.restart"}:
        raise ValueError("Guardian repair supports only starting or restarting its registered service")
    if any(action.get(field) for field in ("arguments", "expected_state", "verification", "compensation")):
        raise ValueError("Custom action conditions are not supported; the registered HTTP probe verifies recovery")
    return operation.replace("service.", "units."), {
        "host": action["host_id"], "unit": action["resource_ref"],
    }


class GuardianOpsBridge:
    def __init__(self, management: OpsManagementService, store: ReliabilityStore,
                 targets: Mapping[str, Mapping[str, Any]],
                 inventory: tuple[dict[str, object], ...]) -> None:
        self.management = management
        self.store = store
        self.targets = targets
        self.inventory = {str(host["host_id"]): host for host in inventory}
        management.guardian_validator = self.validate_dispatch
        management.guardian_approver = self.approve

    async def _binding(self, action: Mapping[str, Any], actor: str) -> str:
        self.management.authorize(actor)
        operation, params = service_operation(action)
        host = self.inventory.get(str(params["host"]), {})
        if params["host"] not in self.management.hosts or not host.get("operate"):
            raise PermissionError("Guardian host is not granted")
        if params["unit"] not in host.get("operable_units", []):
            raise PermissionError("Guardian service is not granted")
        catalog = await self.management.catalog(actor, operation)
        definition = catalog["operations"][0]
        if definition["read_only"] or definition["idempotency"] != "required":
            raise PermissionError("Guardian repair requires an idempotent Ops submission")
        try:
            Draft202012Validator(definition["params_schema"]).validate(params)
        except ValidationError:
            raise ValueError("Ops no longer accepts the configured guardian action parameters") from None
        return self.management.binding_hash(definition)

    async def create(self, raw: dict[str, Any], *, actor: str, origin: str) -> dict[str, Any]:
        if not actor.startswith("admin:") or raw.pop("confirm_remediation", False) is not True:
            raise PermissionError("An administrator must explicitly confirm the bounded repair policy")
        host, unit = resolve_guardian_target(raw, self.targets)
        target_snapshot = guardian_target_snapshot(self.targets[str(raw["target_id"])])
        if raw.pop("expected_target_hash", "") != content_hash(target_snapshot):
            raise PermissionError("Guardian target changed; refresh and review before authorizing")
        action = validate_guardian_action(raw.get("authorized_action"), mode="remediate",
            max_actions=int(raw.get("max_actions") or 0), host_id=host, service_ref=unit)
        policy = dict(raw.get("probe_policy") or {})
        policy["ops_binding"] = await self._binding(action, actor)
        policy["authority_hash"] = content_hash({
            "target": target_snapshot,
            "action": action, "expires_at": raw["expires_at"], "max_actions": raw["max_actions"],
        })
        return await asyncio.to_thread(self.store.create_guardian,
            {**raw, "mode": "remediate", "probe_policy": policy}, actor_id=actor,
            origin_scope=origin, known_targets=self.targets)

    async def _validate(self, guardian: Mapping[str, Any]) -> None:
        now = int(time.time())
        if (guardian["mode"] != "remediate"
                or guardian["status"] not in {"scheduled", "active", "needs_attention"}
                or not int(guardian["starts_at"]) <= now < int(guardian["expires_at"])):
            raise PermissionError("Guardian authorization is inactive or expired")
        target = self.targets.get(str(guardian["target_id"]))
        policy = guardian["probe_policy"]
        if target is None or policy.get("registered_target") != guardian_target_snapshot(target):
            raise PermissionError("Guardian target changed")
        if policy.get("authority_hash") != content_hash({
            "target": guardian_target_snapshot(target), "action": guardian["authorized_action"],
            "expires_at": guardian["expires_at"], "max_actions": guardian["max_actions"],
        }):
            raise PermissionError("Guardian action limits differ from the reviewed authorization")
        if policy.get("ops_binding") != await self._binding(guardian["authorized_action"], str(guardian["actor_id"])):
            raise PermissionError("Guardian Ops authorization changed; review a new policy")

    async def submit(self, guardian: Mapping[str, Any], lease_owner: str) -> dict[str, Any]:
        if await asyncio.to_thread(self.store.host_under_maintenance, guardian["host_id"]):
            raise PermissionError("Host is in a deployment maintenance window")
        await self._validate(guardian)
        if guardian.get("check_lease_owner") != lease_owner or int(guardian.get("check_lease_until") or 0) <= int(time.time()):
            raise PermissionError("Guardian check lease changed")
        guardian_id = str(guardian["guardian_id"])
        if int(guardian["actions_used"]) > 0:
            previous = await asyncio.to_thread(self.management.store.find_operation,
                str(guardian["actor_id"]), f"guardian:{guardian_id}",
                f"guardian:{guardian_id}:{guardian['actions_used']}")
            if previous is None:
                raise PermissionError("Reserved guardian action has no receipt; manual reconciliation required")
            if previous["status"] != "succeeded" or int(previous["updated_at"]) >= int(time.time()) - int(guardian["interval_seconds"]):
                return {**previous, "action_reserved": True}
        action = materialize_guardian_action(guardian["authorized_action"], guardian, now=int(time.time()))
        operation, params = service_operation(action)
        result = await self.management.call(operation, params,
            actor=str(guardian["actor_id"]), origin=f"guardian:{guardian_id}",
            idempotency_key=action["idempotency_key"], guardian_id=guardian_id)
        record = result["operation"]
        if record["status"] != "awaiting_approval":
            return {**record, "action_reserved": True}
        # A guardian policy only permits proposing a repair. Every concrete repair
        # still waits for its own administrator phone confirmation.
        return {**record, "action_reserved": False}

    async def approve(self, record: dict[str, Any], *, actor: str, expected_hash: str,
                      expected_version: int) -> dict[str, Any]:
        guardian = await asyncio.to_thread(self.store.guardian, record["arguments"]["guardian_id"])
        if guardian is None or guardian["actor_id"] != actor:
            raise PermissionError("Guardian repair must be confirmed by its owner")
        await self._validate(guardian)
        await self.management.validate_binding(record)
        record = await asyncio.to_thread(self.management.store.approve_operation,
            record["operation_id"], actor_id=actor,
            expected_hash=expected_hash, expected_version=expected_version,
            expires_at=min(int(guardian["expires_at"]), int(time.time()) + 300),
            before_approve=lambda cursor, item, now: self._reserve(cursor, item, guardian, None, now))
        return {**record, "action_reserved": True}

    @staticmethod
    def _reserve(cursor: Any, operation: dict[str, Any], expected: Mapping[str, Any], owner: str | None, now: int) -> None:
        row = cursor.execute("SELECT * FROM fleet_guardians WHERE guardian_id=? FOR UPDATE",
            (expected["guardian_id"],)).fetchone()
        if row is None:
            raise PermissionError("Guardian was removed")
        guardian = ReliabilityStore._guardian(row)
        if cursor.execute("SELECT 1 FROM fleet_maintenance_locks WHERE host_id=? AND lease_expires_at>?",
                          (guardian["host_id"], now)).fetchone() is not None:
            raise PermissionError("Host is in a deployment maintenance window")
        if (guardian["status"] not in {"active", "needs_attention"} or guardian["mode"] != "remediate"
                or (owner is not None and (guardian["check_lease_owner"] != owner or int(guardian["check_lease_until"] or 0) <= now))
                or not int(guardian["starts_at"]) <= now < int(guardian["expires_at"])
                or int(guardian["actions_used"]) >= int(guardian["max_actions"])
                or guardian["probe_policy"] != expected["probe_policy"]):
            raise PermissionError("Guardian authorization or lease changed")
        op, params = service_operation(guardian["authorized_action"])
        arguments = json.loads(operation["arguments_json"])
        number = int(guardian["actions_used"]) + 1
        if (operation["origin_scope"] != f"guardian:{guardian['guardian_id']}"
                or operation["actor_id"] != guardian["actor_id"]
                or operation["idempotency_key"] != f"guardian:{guardian['guardian_id']}:{number}"
                or arguments.get("op") != op or arguments.get("params") != params
                or arguments.get("binding_hash") != guardian["probe_policy"].get("ops_binding")
                or arguments.get("guardian_id") != guardian["guardian_id"]):
            raise PermissionError("Operation does not match the approved guardian action")
        previous = cursor.execute("""SELECT status, updated_at FROM fleet_operations
            WHERE origin_scope=? AND operation_id<>? AND approval_ref IS NOT NULL
            ORDER BY created_at DESC, operation_id DESC LIMIT 1""",
            (operation["origin_scope"], operation["operation_id"])).fetchone()
        if previous and (previous["status"] != "succeeded" or int(previous["updated_at"]) >= now - int(guardian["interval_seconds"])):
            raise PermissionError("Previous repair needs a fresh observation before another action")
        cursor.execute("""UPDATE fleet_guardians SET actions_used=actions_used+1,
            resource_version=resource_version+1, updated_at=? WHERE guardian_id=?""",
            (now, guardian["guardian_id"]))

    async def validate_dispatch(self, operation: dict[str, Any]) -> None:
        guardian = await asyncio.to_thread(self.store.guardian, operation["arguments"]["guardian_id"])
        if guardian is None:
            raise PermissionError("Guardian was removed")
        if await asyncio.to_thread(self.store.host_under_maintenance, guardian["host_id"]):
            raise PermissionError("Host is in a deployment maintenance window")
        await self._validate(guardian)
        if not operation.get("approval_ref") or not int(guardian["actions_used"]) > 0:
            raise PermissionError("Guardian action was not reserved")
