"""Exact-revision deployment evidence from Ops; this module never approves writes."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from blake3 import blake3

from .adapters.ops import OpsError
from .deployment_contracts import REVISION_RE
from .execution_contracts import HOST_RE, content_hash


ReadOperation = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
STORE_PATH = re.compile(r"/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise OpsError("deployment_evidence_mismatch", message)


def store_path(value: Any) -> str:
    require(isinstance(value, str) and STORE_PATH.fullmatch(value) is not None,
            "Deployment evidence has no valid Nix store path")
    return value


def revision(value: Any) -> int:
    require(type(value) is int and value > 0, "Invalid Ops resource revision")
    return value


async def read_job_result(read: ReadOperation, job_id: str, *, pointer: str = "",
                          max_bytes: int = 256 * 1024, allow_failed: bool = False) -> Any:
    """Decode bounded, byte-offset JSON pages from one completed upstream job."""
    require(1 <= max_bytes <= 2 * 1024 * 1024, "Invalid job result budget")
    status = await read("jobs.status", {"job_id": job_id})
    handle = status.get("handle", {})
    require(handle.get("job_id") == job_id and handle.get("state") in
            ({"succeeded", "failed"} if allow_failed else {"succeeded"}),
            "Job is not confirmed successful; do not infer a deployment result")
    offset, total, chunks = 0, None, []
    while True:
        require(len(chunks) < (max_bytes + 32767) // 32768 + 1,
                "Ops result has too many short pages")
        page = await read("jobs.result", {
            "job_id": job_id, "pointer": pointer, "offset": offset,
            "limit": min(32768, max_bytes - offset),
        })
        require(page.get("available") is True and page.get("job_id") == job_id
                and page.get("pointer") == pointer and page.get("encoding") == "json_utf8",
                "Ops returned an unavailable or unrelated result page")
        text = page.get("text")
        require(isinstance(text, str), "Ops result page has no JSON text")
        size = len(text.encode("utf-8"))
        end, length = page.get("next_offset"), page.get("total_bytes")
        require(type(end) is int and type(length) is int and 0 < length <= max_bytes
                and 0 < size <= min(32768, max_bytes - offset)
                and end == offset + size and end <= length,
                "Ops result offsets or size do not match its UTF-8 content")
        require(total is None or total == length, "Ops result changed during pagination")
        require(type(page.get("complete")) is bool and page["complete"] == (end == length),
                "Ops result has an inconsistent completion marker")
        total, offset = length, end
        chunks.append(text)
        if page["complete"]:
            try:
                return json.loads("".join(chunks))
            except json.JSONDecodeError as exc:
                raise OpsError("invalid_response", "Completed Ops result is not valid JSON") from exc


@dataclass(frozen=True)
class DeploymentTargetBinding:
    host_id: str
    repository: str
    profile: str
    source_revision: str
    expected_remote_revision: str
    flake_attribute: str

    def __post_init__(self) -> None:
        require(HOST_RE.fullmatch(self.host_id) is not None, "Invalid deployment host")
        require(bool(re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.repository)),
                "Invalid Ops repository")
        require(bool(re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.profile)),
                "Invalid Ops deployment profile")
        require(REVISION_RE.fullmatch(self.source_revision) is not None
                and REVISION_RE.fullmatch(self.expected_remote_revision) is not None,
                "Deployment revisions must be exact Git commits")
        require(bool(self.flake_attribute), "Deployment flake attribute is required")


class OpsDeploymentProtocol:
    def __init__(self, read: ReadOperation, target: DeploymentTargetBinding) -> None:
        self.read = read
        self.target = target

    def create_workspace_params(self) -> dict[str, Any]:
        require(self.target.source_revision == self.target.expected_remote_revision,
                "Ops workspace.create only supports the pinned remote head; it cannot checkout a different commit")
        return {"repository": self.target.repository,
                "expected_remote_head": self.target.expected_remote_revision}

    async def workspace(self, workspace_id: str) -> dict[str, Any]:
        item = await self.read("workspace.status", {
            "repository": self.target.repository, "workspace_id": workspace_id,
        })
        require(item.get("repository") == self.target.repository
                and item.get("workspace_id") == workspace_id,
                "Workspace belongs to another repository or request")
        revision(item.get("revision"))
        require(item.get("state") in {"clean", "committed", "published"},
                "Dirty workspace cannot be deployed as an exact revision")
        source = item.get("commit_hash") or item.get("base_commit")
        require(source == self.target.source_revision,
                "Workspace commit differs from the requested source revision")
        require(REVISION_RE.fullmatch(str(item.get("tree_hash") or "")) is not None,
                "Workspace tree identity is missing")
        return item

    def prepare_params(self, workspace: Mapping[str, Any]) -> dict[str, Any]:
        require(workspace.get("repository") == self.target.repository
                and (workspace.get("commit_hash") or workspace.get("base_commit")) == self.target.source_revision
                and workspace.get("state") in {"clean", "committed", "published"},
                "Workspace does not match the frozen source")
        require(bool(workspace.get("workspace_id")), "Workspace identity is missing")
        # A legacy hub may reject a clean workspace. Never work around that rejection
        # by making a synthetic commit: Nix self.rev must retain the requested identity.
        return {"repository": self.target.repository, "workspace_id": workspace["workspace_id"],
                "expected_revision": revision(workspace.get("revision")),
                "target_host": self.target.host_id, "profile": self.target.profile}

    async def change(self, change_id: str) -> dict[str, Any]:
        item = await self.read("changes.status", {"change_id": change_id})
        plan = item.get("plan")
        require(isinstance(plan, dict), "Change plan is missing")
        require(plan.get("change_id") == change_id and plan.get("repository") == self.target.repository
                and plan.get("target_host") == self.target.host_id
                and plan.get("deployment_profile") == self.target.profile
                and plan.get("kind") == "system" and plan.get("flake_attribute") == self.target.flake_attribute
                and plan.get("source_commit") == self.target.source_revision
                and plan.get("source_remote_head") == self.target.expected_remote_revision,
                "Change host, profile or source differs from the deployment contract")
        revision(item.get("revision"))
        return item

    async def preflight(self, change_id: str) -> dict[str, Any]:
        change = await self.change(change_id)
        require(change.get("state") == "ready", "Preflight has not produced a ready artifact")
        plan, artifact = change["plan"], change.get("artifact")
        require(isinstance(artifact, dict), "Built artifact evidence is missing")
        workspace = await self.workspace(str(plan.get("workspace_id") or ""))
        require(workspace["revision"] == plan.get("workspace_revision")
                and workspace["tree_hash"] == plan.get("tree_hash"), "Prepared workspace changed")
        file = await self.read("workspace.read", {
            "repository": self.target.repository, "workspace_id": workspace["workspace_id"],
            "expected_revision": workspace["revision"], "path": "flake.lock", "max_bytes": 262144,
        })
        require(file.get("workspace_id") == workspace["workspace_id"]
                and file.get("revision") == workspace["revision"] and file.get("path") == "flake.lock"
                and file.get("encoding") == "utf-8" and isinstance(file.get("content"), str),
                "flake.lock evidence belongs to another workspace revision")
        data = file["content"].encode("utf-8")
        digest = blake3(data).hexdigest()
        require(file.get("digest") == digest and plan.get("lock_digest") == digest
                and artifact.get("lock_digest") == digest, "flake.lock differs from the built artifact")
        try:
            lock = json.loads(file["content"])
        except json.JSONDecodeError as exc:
            raise OpsError("deployment_evidence_mismatch", "flake.lock is not valid JSON") from exc
        require(isinstance(lock, dict) and isinstance(lock.get("nodes"), dict), "Invalid flake.lock structure")
        require(artifact.get("source_commit") == self.target.source_revision
                and artifact.get("tree_hash") == plan["tree_hash"]
                and artifact.get("drv_path") == plan.get("drv_path"), "Artifact differs from the prepared source")
        baseline = plan.get("runtime_baseline", {})
        require(baseline.get("host") == self.target.host_id and baseline.get("profile") == self.target.profile,
                "Runtime baseline belongs to another host/profile")
        evidence = {
            "backend": "ops-management-v2", "host_id": self.target.host_id,
            "change_id": change_id, "change_revision": change["revision"],
            "resolved_revision": self.target.source_revision,
            "remote_revision": self.target.expected_remote_revision,
            "flake_lock_sha256": hashlib.sha256(data).hexdigest(),
            "ops_lock_blake3": digest, "plan": plan, "artifact": artifact,
            "current_toplevel": store_path(baseline.get("running_closure")),
            "current_profile": store_path(baseline.get("persistent_profile")),
            "target_toplevel": store_path(artifact.get("out_path")),
        }
        store_path(artifact.get("drv_path"))
        evidence["evidence_hash"] = content_hash(evidence)
        return evidence

    async def activation_params(self, evidence: Mapping[str, Any]) -> dict[str, Any]:
        require(evidence.get("evidence_hash") == content_hash({
            key: value for key, value in evidence.items() if key != "evidence_hash"
        }), "Preflight evidence changed after review")
        current = await self.preflight(str(evidence.get("change_id") or ""))
        require(current == evidence, "Preflight or upstream change revision changed; review again")
        try:
            expires = datetime.fromisoformat(current["plan"]["expires_at"].replace("Z", "+00:00"))
            require(expires.tzinfo is not None and expires > datetime.now(timezone.utc),
                    "Upstream change plan expired")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise OpsError("deployment_evidence_mismatch", "Invalid plan expiration") from exc
        return {"change_id": current["change_id"], "expected_revision": current["change_revision"],
                "until": "verified"}

    async def verification(self, evidence: Mapping[str, Any], *, rollback: bool = False) -> dict[str, Any]:
        require(evidence.get("evidence_hash") == content_hash({
            key: value for key, value in evidence.items() if key != "evidence_hash"
        }), "Preflight evidence changed after review")
        change = await self.change(str(evidence.get("change_id") or ""))
        require(change["plan"] == evidence.get("plan") and change.get("artifact") == evidence.get("artifact"),
                "Verification is for a different deployment artifact")
        state, action = ("rolled_back", "rollback") if rollback else ("succeeded", "verify")
        require(change.get("state") == state, "Deployment is not verified in the expected state")
        jobs = change.get("jobs", {})
        if rollback:
            action = next((name for name in ("rollback", "verify", "activate") if jobs.get(name)), "rollback")
        job_id = jobs.get(action)
        require(isinstance(job_id, str) and bool(job_id), "Deployment has no verification job receipt")
        report = await read_job_result(self.read, job_id, pointer="/deployment", allow_failed=rollback)
        require(isinstance(report, dict) and report.get("action") == action
                and report.get("status") == ("rolled_back" if rollback else "verified"),
                "Job succeeded without the required deployment verification report")
        running = evidence["current_toplevel"] if rollback else evidence["target_toplevel"]
        profile = evidence["current_profile"] if rollback else evidence["target_toplevel"]
        require(report.get("observed_running") == running and report.get("observed_profile") == profile,
                "Actual runtime or boot profile differs from the expected closure")
        return {"ok": True, "host_id": self.target.host_id, "change_id": evidence["change_id"],
                "job_id": job_id, "actual_toplevel": running, "report": report}
