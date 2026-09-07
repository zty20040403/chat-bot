from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Mapping, Protocol

import httpx

from .execution_contracts import OPERATION_ACTIONS, bounded_object


logger = logging.getLogger(__name__)


class GuardianStore(Protocol):
    def claim_due_guardians(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    def finish_guardian_check(self, guardian_id: str, **kwargs: Any) -> dict[str, Any]: ...

    def observe_incident(self, **kwargs: Any) -> dict[str, Any]: ...

    def recover_incident(self, **kwargs: Any) -> dict[str, Any] | None: ...


def resolve_guardian_target(
    raw: Mapping[str, Any],
    known_targets: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str]:
    """Resolve immutable guardian ownership from the registered target catalog."""
    target_id = str(raw.get("target_id") or "").strip()
    target = known_targets.get(target_id)
    if target is None:
        raise PermissionError("guardian target is not registered")
    host_id = str(target.get("host_id") or target.get("observer_host") or "").strip()
    service_ref = str(target.get("service_ref") or "").strip()
    if not host_id:
        raise ValueError("guardian target has no registered host")
    requested_host_id = str(raw.get("host_id") or "").strip()
    requested_service_ref = str(raw.get("service_ref") or "").strip()
    if requested_host_id and requested_host_id != host_id:
        raise PermissionError("guardian host does not match the registered target")
    if requested_service_ref and requested_service_ref != service_ref:
        raise PermissionError("guardian service does not match the registered target")
    return host_id, service_ref


def guardian_target_snapshot(target: Mapping[str, Any]) -> dict[str, str]:
    return {
        key: str(target.get(key) or "")
        for key in (
            "target_id", "kind", "url", "observer_host", "host_id", "service_ref"
        )
    }


def guardian_status_after_check(
    *,
    outcome: str,
    failures: int,
    failure_threshold: int,
    mode: str,
    actions_used: int,
    max_actions: int,
    expired: bool,
    requires_attention: bool,
) -> str:
    if expired:
        return "completed"
    if outcome == "passed":
        return "active"
    if outcome == "unknown" or requires_attention:
        return "needs_attention"
    if failures >= failure_threshold and (
        mode == "observe" or actions_used >= max_actions
    ):
        return "needs_attention"
    return "active"


def require_guardian_mode_capability(
    mode: str, *, remediation_available: bool
) -> None:
    if mode == "remediate" and not remediation_available:
        raise PermissionError(
            "guardian remediation is unavailable until the approved write backend is active"
        )


def validate_guardian_action(
    raw: Any,
    *,
    mode: str,
    max_actions: int,
    host_id: str,
    service_ref: str,
) -> dict[str, Any]:
    action = bounded_object(raw or {}, field="authorized_action")
    if mode == "observe":
        if max_actions or action:
            raise ValueError("observe-only guardians cannot authorize actions")
        return {}
    if max_actions < 1:
        raise ValueError("remediation guardians require a positive action limit")
    if not service_ref:
        raise ValueError("remediation guardians require a registered service target")
    allowed = {
        "host_id", "resource_ref", "operation", "arguments",
        "expected_state", "verification", "compensation",
    }
    unknown = set(action) - allowed
    if unknown:
        raise ValueError("authorized action contains runtime-managed fields")
    if str(action.get("host_id") or "") != host_id:
        raise PermissionError("guardian action host does not match its target")
    if str(action.get("resource_ref") or "") != service_ref:
        raise PermissionError("guardian action service does not match its target")
    operation = str(action.get("operation") or "")
    if operation not in OPERATION_ACTIONS:
        raise ValueError("guardian action is not a supported service operation")
    return {
        "host_id": host_id,
        "resource_ref": service_ref,
        "operation": operation,
        **{
            field: bounded_object(action.get(field, {}), field=field)
            for field in (
                "arguments", "expected_state", "verification", "compensation"
            )
        },
    }


def materialize_guardian_action(
    template: Mapping[str, Any], guardian: Mapping[str, Any], *, now: int
) -> dict[str, Any]:
    expires_at = int(guardian.get("expires_at") or now + 300)
    deadline_at = min(now + 300, expires_at)
    if deadline_at <= now:
        raise ValueError("guardian authorization has expired")
    action_number = int(guardian.get("actions_used") or 0) + 1
    guardian_id = str(guardian["guardian_id"])
    return {
        **dict(template),
        "host_id": str(guardian["host_id"]),
        "resource_ref": str(guardian["service_ref"]),
        "deadline_at": deadline_at,
        "idempotency_key": f"guardian:{guardian_id}:{action_number}",
        "task_ref": guardian_id,
        "step_ref": f"action-{action_number}",
    }


class GuardianService:
    """Run deterministic probes without involving an LLM on the healthy path."""

    def __init__(
        self,
        store: GuardianStore,
        targets: tuple[dict[str, str], ...],
        *,
        operation_factory: Callable[[Mapping[str, Any], str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.store = store
        self.targets = {str(item["target_id"]): dict(item) for item in targets}
        self.operation_factory = operation_factory
        self.owner = f"guardian-{uuid.uuid4().hex}"
        self.stop_event = asyncio.Event()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0), follow_redirects=False, trust_env=False
        )

    async def close(self) -> None:
        self.stop_event.set()
        await self._client.aclose()

    async def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                if await self.tick():
                    continue
                await asyncio.wait_for(self.stop_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            except Exception:
                logger.exception("Guardian control loop failed")
                await asyncio.sleep(5)

    async def tick(self) -> int:
        guardians = await asyncio.to_thread(
            self.store.claim_due_guardians, owner=self.owner, limit=1, lease_seconds=180
        )
        for guardian in guardians:
            await self._check(guardian)
        return len(guardians)

    async def _check(self, guardian: Mapping[str, Any]) -> None:
        target = self.targets.get(str(guardian["target_id"]))
        if target is None:
            await asyncio.to_thread(
                self.store.finish_guardian_check,
                str(guardian["guardian_id"]), owner=self.owner, outcome="unknown",
                facts={"error": "target_removed"},
            )
            return
        probe_policy = guardian.get("probe_policy")
        expected_target = (
            probe_policy.get("registered_target")
            if isinstance(probe_policy, Mapping)
            else None
        )
        if isinstance(expected_target, Mapping) and guardian_target_snapshot(
            target
        ) != dict(expected_target):
            await asyncio.to_thread(
                self.store.finish_guardian_check,
                str(guardian["guardian_id"]), owner=self.owner, outcome="unknown",
                facts={"error": "target_definition_changed"},
            )
            return
        started = time.monotonic()
        try:
            response = await self._client.get(
                str(target["url"]), headers={"Accept": "application/json,text/plain"}
            )
            ok = 200 <= response.status_code < 300
            facts: dict[str, Any] = {
                "status_code": response.status_code,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "target_id": target["target_id"],
            }
            outcome = "passed" if ok else "failed"
        except httpx.HTTPError as exc:
            facts = {
                "error": type(exc).__name__,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "target_id": target["target_id"],
            }
            outcome = "failed"
        failures = int(guardian["consecutive_failures"]) + (
            1 if outcome == "failed" else 0
        )
        incident_id = ""
        operation_id = ""
        action_used = False
        requires_attention = False
        if (
            outcome == "passed"
            and int(guardian["consecutive_failures"])
            >= int(guardian["failure_threshold"])
        ):
            recovered = await asyncio.to_thread(
                self.store.recover_incident,
                incident_key=f"guardian:{guardian['target_id']}",
                source_ref=str(guardian["guardian_id"]),
                payload=facts,
            )
            if recovered is not None:
                incident_id = str(recovered["incident_id"])
        if outcome == "failed" and failures >= int(guardian["failure_threshold"]):
            incident = await asyncio.to_thread(
                self.store.observe_incident,
                incident_key=f"guardian:{guardian['target_id']}",
                host_id=str(guardian["host_id"]),
                service_ref=str(guardian["service_ref"]),
                severity=(
                    "critical"
                    if failures > int(guardian["failure_threshold"])
                    else "warning"
                ),
                summary=f"守护目标 {guardian['target_id']} 连续检查失败",
                event_type="guardian_probe_failed",
                source_ref=str(guardian["guardian_id"]),
                confidence="confirmed",
                payload=facts,
            )
            incident_id = str(incident["incident_id"])
            can_act = (
                guardian["mode"] == "remediate"
                and int(guardian["actions_used"]) < int(guardian["max_actions"])
                and self.operation_factory is not None
            )
            if can_act:
                try:
                    operation = await self.operation_factory(guardian, self.owner)
                    operation_id = str(operation.get("operation_id") or "")
                    action_used = str(operation.get("status") or "") in {
                        "queued", "running", "verifying", "succeeded",
                    }
                    if not action_used:
                        requires_attention = True
                        facts["remediation_status"] = str(
                            operation.get("status") or "not_started"
                        )
                    if operation.get("action_reserved"):
                        facts["action_reserved"] = True
                        action_used = False
                except Exception as exc:
                    requires_attention = True
                    facts["remediation_error"] = type(exc).__name__
        await asyncio.to_thread(
            self.store.finish_guardian_check,
            str(guardian["guardian_id"]), owner=self.owner, outcome=outcome,
            facts=facts, incident_id=incident_id, operation_id=operation_id,
            action_used=action_used, requires_attention=requires_attention,
        )
