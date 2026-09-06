from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Mapping

from .job_kinds import WORKER_JOB_CATALOG, WORKER_JOB_KINDS
from .scheduling import ResourceRequest


HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
UNIT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,119}\.service")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,199}")
HASH_RE = re.compile(r"[a-f0-9]{64}")
OPERATION_ACTIONS = frozenset({"service.start", "service.stop", "service.restart"})
def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def content_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def new_handle(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def bounded_object(value: Any, *, field: str, max_bytes: int = 16_384) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    if len(canonical_json(value).encode("utf-8")) > max_bytes:
        raise ValueError(f"{field} is too large")
    return dict(value)


@dataclass(frozen=True)
class OperationProposal:
    host_id: str
    resource_ref: str
    operation: str
    arguments: dict[str, Any]
    expected_state: dict[str, Any]
    verification: dict[str, Any]
    compensation: dict[str, Any]
    deadline_at: int
    idempotency_key: str
    task_ref: str = ""
    step_ref: str = ""

    @classmethod
    def parse(cls, raw: Mapping[str, Any], *, now: int | None = None) -> "OperationProposal":
        timestamp = int(time.time()) if now is None else int(now)
        host_id = str(raw.get("host_id") or "").strip()
        resource_ref = str(raw.get("resource_ref") or "").strip()
        operation = str(raw.get("operation") or "").strip()
        idempotency_key = str(raw.get("idempotency_key") or "").strip()
        if HOST_RE.fullmatch(host_id) is None:
            raise ValueError("invalid host_id")
        if UNIT_RE.fullmatch(resource_ref) is None:
            raise ValueError("resource_ref must be an exact systemd service")
        if operation not in OPERATION_ACTIONS:
            raise ValueError("unsupported operation")
        if not 8 <= len(idempotency_key) <= 160 or any(ch.isspace() for ch in idempotency_key):
            raise ValueError("invalid idempotency_key")
        deadline_at = int(raw.get("deadline_at") or timestamp + 300)
        if deadline_at <= timestamp or deadline_at > timestamp + 3600:
            raise ValueError("deadline must be within the next hour")
        task_ref = str(raw.get("task_ref") or "").strip()
        step_ref = str(raw.get("step_ref") or "").strip()
        if task_ref and ID_RE.fullmatch(task_ref) is None:
            raise ValueError("invalid task_ref")
        if step_ref and ID_RE.fullmatch(step_ref) is None:
            raise ValueError("invalid step_ref")
        return cls(
            host_id=host_id,
            resource_ref=resource_ref,
            operation=operation,
            arguments=bounded_object(raw.get("arguments", {}), field="arguments", max_bytes=4096),
            expected_state=bounded_object(raw.get("expected_state", {}), field="expected_state"),
            verification=bounded_object(raw.get("verification", {}), field="verification"),
            compensation=bounded_object(raw.get("compensation", {}), field="compensation"),
            deadline_at=deadline_at,
            idempotency_key=idempotency_key,
            task_ref=task_ref,
            step_ref=step_ref,
        )


@dataclass(frozen=True)
class WorkerJobProposal:
    kind: str
    payload: dict[str, Any]
    constraints: dict[str, Any]
    deadline_at: int
    idempotency_key: str

    @classmethod
    def parse(cls, raw: Mapping[str, Any], *, now: int | None = None) -> "WorkerJobProposal":
        timestamp = int(time.time()) if now is None else int(now)
        kind = str(raw.get("kind") or "").strip()
        if kind not in WORKER_JOB_KINDS:
            raise ValueError("unsupported worker job kind")
        key = str(raw.get("idempotency_key") or "").strip()
        if not 8 <= len(key) <= 160 or any(ch.isspace() for ch in key):
            raise ValueError("invalid idempotency_key")
        deadline_at = int(raw.get("deadline_at") or timestamp + 900)
        if deadline_at <= timestamp or deadline_at > timestamp + 86_400:
            raise ValueError("deadline must be within the next day")
        payload = bounded_object(raw.get("payload", {}), field="payload", max_bytes=64_000)
        request = ResourceRequest.parse(
            bounded_object(raw.get("constraints", {}), field="constraints")
        )
        kind_policy = WORKER_JOB_CATALOG[kind]
        constraints = replace(
            request,
            safe_rerun=kind_policy.safe_rerun,
            checkpointable=kind_policy.checkpointable,
        ).as_dict()
        if kind in {"artifact.inspect", "document.verify", "media.inspect", "preview.static"}:
            artifact_id = str(payload.get("artifact_id") or "")
            if not re.fullmatch(r"artifact_[a-f0-9]{32}", artifact_id):
                raise ValueError("this job requires a valid artifact_id")
        if kind == "probe.http" and not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,63}", str(payload.get("target_id") or "")
        ):
            raise ValueError("probe.http requires a configured target_id")
        if kind == "preview.static":
            ttl = int(payload.get("ttl_seconds") or 3600)
            if not 300 <= ttl <= 604_800:
                raise ValueError("preview TTL must be between 5 minutes and 7 days")
            payload["ttl_seconds"] = ttl
        return cls(kind, payload, constraints, deadline_at, key)


def safe_artifact_name(value: str) -> str:
    name = PurePosixPath(value.strip()).name
    if not name or name in {".", ".."} or len(name.encode("utf-8")) > 240:
        raise ValueError("invalid artifact name")
    return name
