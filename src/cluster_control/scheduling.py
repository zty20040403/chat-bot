from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

PRIORITIES = {"background": 20, "normal": 50, "interactive": 80}


def bounded_int(
    value: Any, *, default: int, minimum: int, maximum: int, field: str
) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return parsed


def strict_bool(value: Any, *, default: bool, field: str) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


@dataclass(frozen=True)
class ResourceRequest:
    cpu_millis: int = 500
    memory_bytes: int = 256 * 1024**2
    gpu_slots: int = 0
    priority: int = 50
    worker_id: str = ""
    architecture: str = ""
    system: str = ""
    site: str = ""
    borrow_required: bool = False
    safe_rerun: bool = False
    checkpointable: bool = False
    checkpoint_format: str = "gaoji-result-v1"
    executor_version: str = "worker-v2"
    expected_cost_microunits: int = 0
    max_cost_microunits: int = 0

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> "ResourceRequest":
        priority_raw = raw.get("priority", "normal")
        if isinstance(priority_raw, str):
            if priority_raw not in PRIORITIES:
                raise ValueError("priority must be background, normal, or interactive")
            priority = PRIORITIES[priority_raw]
        else:
            priority = bounded_int(
                priority_raw, default=50, minimum=0, maximum=100, field="priority"
            )
        expected_cost = bounded_int(
            raw.get("expected_cost_microunits"), default=0, minimum=0,
            maximum=10**12, field="expected_cost_microunits",
        )
        max_cost = bounded_int(
            raw.get("max_cost_microunits"), default=expected_cost, minimum=0,
            maximum=10**12, field="max_cost_microunits",
        )
        if expected_cost > max_cost:
            raise ValueError("expected cost exceeds the task cost limit")
        checkpoint_format = str(
            raw.get("checkpoint_format") or "gaoji-result-v1"
        ).strip()
        executor_version = str(raw.get("executor_version") or "worker-v2").strip()
        if not checkpoint_format or len(checkpoint_format) > 80:
            raise ValueError("invalid checkpoint_format")
        if not executor_version or len(executor_version) > 80:
            raise ValueError("invalid executor_version")
        return cls(
            cpu_millis=bounded_int(
                raw.get("cpu_millis"), default=500, minimum=50, maximum=8000,
                field="cpu_millis",
            ),
            memory_bytes=bounded_int(
                raw.get("memory_bytes"), default=256 * 1024**2,
                minimum=16 * 1024**2, maximum=8 * 1024**3,
                field="memory_bytes",
            ),
            gpu_slots=bounded_int(
                raw.get("gpu_slots"), default=0, minimum=0, maximum=8,
                field="gpu_slots",
            ),
            priority=priority,
            worker_id=str(raw.get("worker_id") or "").strip(),
            architecture=str(raw.get("architecture") or "").strip().lower(),
            system=str(raw.get("system") or "").strip().lower(),
            site=str(raw.get("site") or "").strip().lower(),
            borrow_required=strict_bool(
                raw.get("borrow_required"), default=False, field="borrow_required"
            ),
            safe_rerun=strict_bool(
                raw.get("safe_rerun"), default=False, field="safe_rerun"
            ),
            checkpointable=strict_bool(
                raw.get("checkpointable"), default=False, field="checkpointable"
            ),
            checkpoint_format=checkpoint_format,
            executor_version=executor_version,
            expected_cost_microunits=expected_cost,
            max_cost_microunits=max_cost,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "cpu_millis": self.cpu_millis,
            "memory_bytes": self.memory_bytes,
            "gpu_slots": self.gpu_slots,
            "priority": self.priority,
            "worker_id": self.worker_id,
            "architecture": self.architecture,
            "system": self.system,
            "site": self.site,
            "borrow_required": self.borrow_required,
            "safe_rerun": self.safe_rerun,
            "checkpointable": self.checkpointable,
            "checkpoint_format": self.checkpoint_format,
            "executor_version": self.executor_version,
            "expected_cost_microunits": self.expected_cost_microunits,
            "max_cost_microunits": self.max_cost_microunits,
        }


@dataclass(frozen=True)
class CostSettlement:
    reported_microunits: int
    settled_microunits: int
    limit_exceeded: bool
    invalid_report: bool


def settle_reported_cost(
    request: ResourceRequest, reported_value: Any
) -> CostSettlement:
    invalid = isinstance(reported_value, bool)
    try:
        reported = int(reported_value or 0) if not invalid else 0
    except (TypeError, ValueError):
        reported = 0
        invalid = True
    if reported < 0:
        reported = 0
        invalid = True
    maximum = request.max_cost_microunits
    return CostSettlement(
        reported_microunits=reported,
        settled_microunits=min(reported, maximum),
        limit_exceeded=reported > maximum,
        invalid_report=invalid,
    )


def decode_json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback


def eligibility_reason(
    request: ResourceRequest,
    *,
    worker: Mapping[str, Any],
    host: Mapping[str, Any],
    policy: Mapping[str, Any] | None,
    grant: Mapping[str, Any] | None,
    job_kind: str,
    now: int,
    external_borrow: bool = False,
    grant_usage: Mapping[str, Any] | None = None,
) -> str:
    if worker.get("availability") != "available":
        return "worker_not_available"
    if policy is None:
        return "owner_policy_missing"
    if policy.get("desired_availability") != "available":
        return f"owner_{policy.get('desired_availability')}"
    if request.worker_id and request.worker_id != worker.get("worker_id"):
        return "worker_constraint"
    runtime = decode_json(worker.get("runtime_json"), {})
    if request.architecture and request.architecture != str(runtime.get("machine") or "").lower():
        return "architecture_mismatch"
    if request.system and request.system != str(runtime.get("system") or "").lower():
        return "runtime_mismatch"
    if request.site and request.site != str(host.get("site") or "").lower():
        return "data_site_mismatch"
    if request.gpu_slots and not bool(host.get("gpu_compute", False)):
        return "gpu_not_authorized"
    if (
        external_borrow
        or request.borrow_required
        or request.gpu_slots
        or request.max_cost_microunits
    ):
        if grant is None:
            return "borrow_grant_required"
        if grant.get("status") != "available":
            return "borrow_grant_inactive"
        if not int(grant.get("valid_from") or 0) <= now < int(grant.get("valid_until") or 0):
            return "borrow_grant_expired"
        allowed = set(decode_json(grant.get("allowed_kinds_json"), []))
        if job_kind not in allowed:
            return "job_kind_not_granted"
        if request.priority > int(grant.get("max_priority") or 0):
            return "priority_not_granted"
        usage = grant_usage or {}
        if (
            int(usage.get("cpu_millis") or 0) + request.cpu_millis
            > int(grant.get("cpu_limit_millis") or 0)
        ):
            return "grant_cpu_limit"
        if (
            int(usage.get("memory_bytes") or 0) + request.memory_bytes
            > int(grant.get("memory_limit_bytes") or 0)
        ):
            return "grant_memory_limit"
        if (
            int(usage.get("gpu_slots") or 0) + request.gpu_slots
            > int(grant.get("gpu_limit_slots") or 0)
        ):
            return "grant_gpu_limit"
        budget = int(grant.get("budget_limit_microunits") or 0)
        used = int(grant.get("budget_reserved_microunits") or 0) + int(
            grant.get("budget_spent_microunits") or 0
        )
        if request.max_cost_microunits and used + request.max_cost_microunits > budget:
            return "cost_budget_exhausted"
    return ""
