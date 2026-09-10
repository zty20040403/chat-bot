"""Durable checked execution and reboot observation; recovery never resubmits writes."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, TYPE_CHECKING

from .adapters.ops import OpsError
from .execution_contracts import canonical_json
from .host_operations import (TERMINAL_JOB_STATES, decode_logs, execution_params, matches_job,
                              preflight_from_logs, reboot_observation)

if TYPE_CHECKING:
    from .ops_management import OpsManagementService


async def request(manager: OpsManagementService, operation: str, params: dict[str, Any], *, key: str | None = None) -> dict[str, Any]:
    response = await manager.client._request("POST", "/v1/execute",
        body=canonical_json({"op": operation, "params": params}).encode(), idempotency_key=key)
    if not isinstance(response.data, dict):
        raise ValueError("Upstream returned a non-object response")
    return response.data


async def recover_handle(manager: OpsManagementService, record: dict[str, Any], result: dict[str, Any]) -> str | None:
    params = {"host": record["host_id"], "limit": 200}
    if result.get("recovery_cursor"):
        params["cursor"] = result["recovery_cursor"]
    payload = await request(manager, "jobs.list", params)
    jobs = payload.get("jobs")
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
        raise ValueError("Invalid recovery job list")
    expected = execution_params(record)
    matches = [job for job in jobs if matches_job(job, expected)]
    if len(matches) > 1:
        raise ValueError("Multiple jobs match the same host operation; manual inspection required")
    if matches:
        result.pop("recovery_cursor", None)
        return matches[0]["handle"]["job_id"]
    cursor = payload.get("next_cursor")
    if cursor is not None and (not isinstance(cursor, str) or cursor == result.get("recovery_cursor")):
        raise ValueError("Invalid recovery pagination cursor")
    result["recovery_cursor"] = cursor
    return None


async def observe(manager: OpsManagementService, record: dict[str, Any], result: dict[str, Any], backend_id: str) -> tuple[str, str]:
    job = await request(manager, "jobs.status", {"job_id": backend_id})
    handle = job.get("handle", {})
    if (not isinstance(handle, dict) or handle.get("job_id") != backend_id
        or handle.get("host") != record["host_id"] or handle.get("operation") != "exec.run"):
        raise ValueError("Upstream returned an unrelated execution handle")
    state = handle.get("state")
    if state not in TERMINAL_JOB_STATES | {"queued", "dispatching", "running", "reconciling"}:
        raise ValueError("Unknown upstream execution state")
    result["upstream"] = job
    if job.get("result") is not None and not isinstance(job["result"], dict):
        raise ValueError("Upstream command result must be an object")
    proof = result.get("preflight")
    if proof is None:
        logs = await request(manager, "jobs.logs", {"job_id": backend_id, "limit": 65536})
        stdout, stderr = decode_logs(logs, backend_id)
        proof = preflight_from_logs(stdout, record)
        if proof is not None:
            result["preflight"] = proof
        elif state in TERMINAL_JOB_STATES:
            try:
                error = json.loads(stderr.splitlines()[0]) if stderr else {}
            except (ValueError, IndexError):
                error = {}
            if isinstance(error, dict) and error.get("ok") is False and error.get("command_started") is False:
                result.update(phase="preflight_failed", preflight_error=error, command_started=False)
                return "failed", str(error.get("code", "preflight_failed"))[:80]
    reboot = record["arguments"]["op"] == "host.reboot"
    if reboot and proof:
        result["phase"] = "waiting_for_reboot"
        try:
            facts = await request(manager, "host.facts", {"host": record["host_id"]})
            verification = reboot_observation(facts, {**record, "result": result}, proof)
            result["verification"] = verification
            if verification["verified"]:
                result.update(phase="verified", command_started=True)
                if record["status"] == "cancelling":
                    result["cancellation_note"] = "Cancellation arrived after the reboot took effect; it cannot undo a reboot."
                return "succeeded", ""
        except (OpsError, ValueError, TypeError) as exc:
            result["observation_error"] = str(exc)[:1000]
        exit_code = (job.get("result") or {}).get("exit_code")
        if state == "failed" and isinstance(exit_code, int) and exit_code != 0:
            result.update(phase="reboot_command_failed", exit_code=exit_code)
            return "failed", "reboot_command_failed"
        # A reboot can kill its own executor before the final exit receipt. Wait for
        # the host, even if the job reports failed/unknown during the disconnect.
        return "reconciling", ""
    if record["status"] == "cancelling" and state not in TERMINAL_JOB_STATES:
        await request(manager, "jobs.cancel", {"job_id": backend_id, "expected_revision": handle["revision"],
                                              "reason": "Administrator cancellation"})
        return "cancelling", ""
    if state in TERMINAL_JOB_STATES:
        result["phase"] = "verified" if state == "succeeded" and proof else "finished"
        if state == "succeeded" and proof:
            return "succeeded", ""
        if state == "cancelled":
            return "cancelled", "cancelled"
        if state in {"failed", "timed_out"}:
            return "failed", state
        result["phase"] = "outcome_unknown"
        return "needs_attention", "missing_execution_evidence"
    result["phase"] = "executing" if proof else "checking"
    return "running", ""


async def run_host_operation(manager: OpsManagementService, record: dict[str, Any]) -> None:
    result = dict(record.get("result") or {})
    backend_id = record.get("backend_operation_id")
    status, error = "needs_attention", ""
    try:
        await manager.validate_binding(record)
        if record["deadline_at"] <= int(time.time()) and not backend_id and not result.get("submission_started"):
            status = "cancelled" if record["status"] == "cancelling" else "failed"
            error = "submission_deadline"
            result.update(phase="submission_rejected", command_started=False,
                instruction="The submission deadline expired before dispatch. No command was sent.")
        elif record["deadline_at"] <= int(time.time()):
            result.update(phase="outcome_unknown", instruction="Observation deadline reached. Inspect the existing job; do not repeat the command.")
            error = "observation_deadline"
            if (backend_id and record["arguments"]["op"] != "host.reboot"
                    and time.time() <= record["deadline_at"] + 120):
                status, observed_error = await observe(manager, {**record, "status": "cancelling"}, result, backend_id)
                error = observed_error or "execution_deadline"
                result["deadline_exceeded"] = True
                if status == "succeeded":
                    result["instruction"] = "The command completed before cancellation was confirmed; inspect its outcome."
        elif backend_id:
            status, error = await observe(manager, record, result, backend_id)
        elif result.get("submission_started"):
            backend_id = await recover_handle(manager, record, result)
            status = "cancelling" if record["status"] == "cancelling" else "reconciling"
            result["phase"] = "recovering_receipt"
            if backend_id:
                status, error = await observe(manager, record, result, backend_id)
        elif record["status"] == "cancelling":
            status = "cancelled"
            result.update(phase="cancelled_before_submission", command_started=False)
        else:
            await manager._validate_submission(record)
            checkpoint = {**result, "phase": "submitting", "submission_started": True, "submitted_at": int(time.time())}
            params = execution_params({**record, "result": checkpoint})
            # A committed checkpoint distinguishes a never-submitted claim from a
            # lost receipt after a write. Recovery only lists/observes existing jobs.
            await asyncio.to_thread(manager.store.checkpoint_managed_operation, record["operation_id"],
                owner=manager.owner, fence=record["fence"], result=checkpoint)
            result = checkpoint
            handle = await request(manager, "exec.run", params, key=record["operation_id"])
            backend_id = handle.get("job_id")
            if (not isinstance(backend_id, str) or not backend_id or handle.get("host") != record["host_id"]
                or handle.get("operation") != "exec.run"):
                backend_id = None
                raise ValueError("Submission response lacks a matching durable handle")
            status = "running"
            result["phase"] = "submitted"
    except (OpsError, ValueError, PermissionError, KeyError, TypeError) as exc:
        error = getattr(exc, "code", type(exc).__name__)
        result["error"] = str(exc)[:1000]
        submitted = result.get("submission_started") or backend_id
        if submitted and not isinstance(exc, PermissionError) and record["deadline_at"] > int(time.time()):
            status = "cancelling" if record["status"] == "cancelling" else "reconciling"
            result["instruction"] = "Observe the original operation; never repeat it under a new identity."
        else:
            status = "needs_attention" if isinstance(exc, PermissionError) else "failed"
            if not submitted:
                result.update(phase="submission_rejected", command_started=False,
                    instruction="Submission validation failed; no command was dispatched. Review the authorization and target contract.")
    await asyncio.to_thread(manager.store.finish_managed_operation, record["operation_id"],
        owner=manager.owner, fence=record["fence"], status=status, result=result, backend_id=backend_id, error=error)
