"""Host-owned command preparation and post-reboot evidence, over the Ops transport."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import re
import shlex
import time
from typing import Any

from src.host_control import PROTOCOL, REPORT_PREFIX, digest

from .execution_contracts import canonical_json


REBOOT_DEFINITION = {
    "name": "host.reboot", "summary": "Reboot the selected host using its fixed target command; verify a changed boot ID",
    "read_only": False, "kind": "job_submission", "idempotency": "required", "provider": "gaoji",
    "params_schema": {"type": "object", "properties": {
        "host": {"type": "string"}, "reason": {"type": "string", "minLength": 1, "maxLength": 1000}},
        "required": ["host", "reason"], "additionalProperties": False},
}
BOOT_ID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")
TERMINAL_JOB_STATES = {"succeeded", "failed", "cancelled", "timed_out", "outcome_unknown"}


def parse_helpers(raw: str) -> dict[str, str]:
    value = json.loads(raw or "{}")
    if not isinstance(value, dict) or any(not isinstance(host, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", host) is None
        or not isinstance(path, str) or not path.startswith("/") or "\0" in path
        or len(path) > 4096 for host, path in value.items()):
        raise ValueError("Host control helpers must map host identities to absolute executable paths")
    return value


def host_contract(operation: str, params: dict[str, Any], helpers: dict[str, str]) -> dict[str, Any]:
    host = params["host"]
    if host not in helpers:
        raise PermissionError("Target-side command checks are not configured for this host")
    if operation == "host.reboot":
        intent = {"host": host, "action": "reboot", "reason": params["reason"]}
        profile = "operator"
    else:
        intent = {"host": host, "action": "exec", "command": params["command"]}
        intent.update({key: params[key] for key in ("cwd", "env") if params.get(key) is not None})
        profile = params["profile"]
    return {"version": PROTOCOL, "intent": intent, "helper": helpers[host], "profile": profile}


def execution_params(record: dict[str, Any]) -> dict[str, Any]:
    contract = record["arguments"]["host_control"]
    request = {"phase": "run", "intent": contract["intent"], "operation_id": record["operation_id"]}
    script = "exec " + shlex.quote(contract["helper"]) + " " + shlex.quote(canonical_json(request))
    if len(script.encode()) > 65536:
        raise ValueError("Encoded host command exceeds the upstream 64 KiB script limit")
    original = record["arguments"]["params"]
    params = {"host": contract["intent"]["host"], "profile": contract["profile"], "command": {"script": script}}
    # Never inject user environment into the bootstrap interpreter before checking.
    # The upstream still validates cwd and credential grants using its own profile.
    params.update({key: original[key] for key in ("cwd", "credential_refs", "timeout_seconds")
                   if original.get(key) is not None})
    submitted_at = (record.get("result") or {}).get("submitted_at", record["created_at"])
    params["timeout_seconds"] = min(original.get("timeout_seconds") or 1800,
                                    max(1, record["deadline_at"] - submitted_at))
    return params


def matches_job(job: dict[str, Any], params: dict[str, Any]) -> bool:
    handle, spec = job.get("handle", {}), job.get("spec", {})
    return (isinstance(handle, dict) and isinstance(spec, dict) and handle.get("operation") == "exec.run"
        and handle.get("host") == params["host"] and spec.get("host") == params["host"]
        and spec.get("profile") == params["profile"] and spec.get("command") == params["command"]
        and spec.get("cwd") == params.get("cwd")
        and spec.get("env", {}) == {} and spec.get("credential_refs", []) == params.get("credential_refs", [])
        and spec.get("timeout_seconds") == params.get("timeout_seconds"))


def decode_logs(payload: dict[str, Any], job_id: str) -> tuple[str, str]:
    if payload.get("job_id") != job_id or payload.get("encoding") != "base64":
        raise ValueError("Unrelated or unsupported job log response")
    values = []
    for key in ("stdout_base64", "stderr_base64"):
        raw = payload.get(key)
        if not isinstance(raw, str) or len(raw) > 90000:
            raise ValueError("Invalid bounded job logs")
        values.append(base64.b64decode(raw, validate=True).decode("utf-8", errors="replace"))
    return values[0], values[1]


def preflight_from_logs(stdout: str, record: dict[str, Any]) -> dict[str, Any] | None:
    # Only the first line is trusted: command output must not forge a later report.
    if "\n" not in stdout:
        return None
    line = stdout.split("\n", 1)[0]
    if not line.startswith(REPORT_PREFIX):
        return None
    report = json.loads(line[len(REPORT_PREFIX):])
    if not isinstance(report, dict):
        raise ValueError("Target preflight report must be an object")
    proof = report.get("preflight", {})
    evidence = proof.get("evidence", {}) if isinstance(proof, dict) else {}
    intent = record["arguments"]["host_control"]["intent"]
    if (report.get("operation_id") != record["operation_id"] or not isinstance(evidence, dict)
        or evidence.get("protocol") != PROTOCOL or proof.get("ok") is not True
        or proof.get("fingerprint") != digest(evidence) or evidence.get("intent_hash") != digest(intent)
        or evidence.get("host") != intent["host"] or not BOOT_ID.fullmatch(str(evidence.get("boot_id", "")))):
        raise ValueError("Target preflight evidence does not match this authorized operation")
    return proof


def reboot_observation(payload: dict[str, Any], record: dict[str, Any], proof: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    # host.facts returns an agent snapshot, not an executor job or cached Bot view.
    observed_at = datetime.fromisoformat(str(payload.get("observed_at", "")).replace("Z", "+00:00"))
    if observed_at.tzinfo is None:
        raise ValueError("Host observation must have an explicit timezone")
    observed = observed_at.astimezone(timezone.utc).timestamp()
    facts = payload.get("facts")
    if not isinstance(facts, dict):
        raise ValueError("Host facts must be an object")
    current = facts.get("boot_id")
    baseline = proof["evidence"]["boot_id"]
    if (payload.get("host") != record["host_id"] or not isinstance(current, str)
        or not BOOT_ID.fullmatch(current) or now - observed > 60 or observed > now + 5
        or observed < record["result"]["submitted_at"]):
        raise ValueError("Host reboot observation is stale, incomplete or belongs to another host")
    return {"verified": current != baseline, "before_boot_id": baseline,
            "after_boot_id": current, "observed_at": payload["observed_at"], "host": payload["host"]}
