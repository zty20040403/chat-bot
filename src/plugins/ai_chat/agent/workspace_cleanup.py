"""Host-owned cleanup tickets for delivered task workspaces, never model input."""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from pathlib import Path


MAX_RETENTION_SECONDS = 3600


def schedule_cleanup(root: Path, *, task_id: int, revision: int, finished_at: int,
                     sandboxes: list[dict[str, str]], snapshots: tuple[str, ...],
                     retention_seconds: int) -> None:
    directory = root / ".workspace-cleanup"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = directory / f"{int(task_id)}.json"
    payload = {
        "task_id": int(task_id), "revision": int(revision),
        "finished_at": int(finished_at), "sandboxes": sandboxes,
        "snapshots": list(snapshots), "acknowledged_at": int(time.time()),
    }
    payload["delete_after"] = payload["acknowledged_at"] + min(max(retention_seconds, 0), MAX_RETENTION_SECONDS)
    if marker.is_file() and not marker.is_symlink():
        previous = json.loads(marker.read_text())
        if previous.get("revision") == revision and previous.get("finished_at") == finished_at:
            payload["acknowledged_at"] = previous["acknowledged_at"]
            payload["delete_after"] = min(previous["delete_after"], payload["delete_after"])
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
        output.write(json.dumps(payload, separators=(",", ":")).encode())
        name = output.name
    os.chmod(name, 0o600)
    os.replace(name, marker)


async def prune_task_workspaces(root: Path, manager, coordinator, *, now: int | None = None) -> tuple[int, int]:
    """Recheck the task/receipts on every sweep; never force-remove a running container."""
    directory = root / ".workspace-cleanup"
    if directory.is_symlink() or not directory.is_dir():
        return 0, 0
    current = int(time.time() if now is None else now)
    deleted = invalid = 0
    store = coordinator.store
    for marker in list(directory.iterdir()):
        if marker.is_symlink() or not marker.is_file() or not re.fullmatch(r"[0-9]+\.json", marker.name):
            invalid += 1
            continue
        try:
            payload = json.loads(marker.read_text())
            task_id = int(marker.stem)
            if payload["task_id"] != task_id:
                raise ValueError("Cleanup ticket task mismatch")
            snapshots = tuple(payload["snapshots"])
            if any(not re.fullmatch(r"[a-f0-9]{64}", digest) for digest in snapshots):
                raise ValueError("Invalid snapshot digest")

            def eligible():
                task = store.get(task_id)
                if (task is None or task.status != "completed"
                        or task.finished_at != payload["finished_at"]
                        or store.control(task_id)["revision"] != payload["revision"]):
                    return False
                ready, digests = coordinator._artifact_retention_state(task)
                return ready and set(digests) == set(snapshots)

            if not eligible():
                # A revision, failed delivery, or restart invalidates the old deletion deadline.
                for digest in snapshots:
                    (root / ".retention" / str(task_id) / f"{digest}.json").unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
                continue
            deadline = min(int(payload["delete_after"]), int(payload["acknowledged_at"]) + MAX_RETENTION_SECONDS)
            if deadline > current:
                continue
            task = store.get(task_id)
            owners = {f"{task.conversation_id}:task#{task_id}/{run.step_key}"
                      for run in store.runs(task_id)}
            sandboxes = payload["sandboxes"]
            for item in sandboxes:
                if item["owner"] not in owners or not re.fullmatch(r"s[0-9a-f]{6}", item["sandbox_id"]):
                    raise ValueError("Cleanup ticket sandbox owner mismatch")
            for item in sandboxes:
                await manager.reclaim_stopped(item["owner"], item["sandbox_id"], eligible=eligible,
                    not_used_since=int(payload["acknowledged_at"]))
            if not eligible():
                continue
            # Remove delivered snapshots only after workspace cleanup succeeded.
            artifact_dir = root / str(task_id)
            if artifact_dir.is_symlink():
                raise ValueError("Artifact directory must not be a symlink")
            for digest in snapshots:
                path = artifact_dir / digest
                if path.is_symlink():
                    raise ValueError("Artifact must not be a symlink")
                await asyncio.to_thread(path.unlink, missing_ok=True)
                (root / ".retention" / str(task_id) / f"{digest}.json").unlink(missing_ok=True)
            store.append_event(task_id, "task.workspace_reclaimed", {
                "revision": payload["revision"], "containers": len(sandboxes),
                "snapshots": len(snapshots), "reason": "delivered_retention_expired",
            })
            marker.unlink(missing_ok=True)
            deleted += len(sandboxes)
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
            invalid += 1
            coordinator.logger.warning("Task workspace cleanup deferred for %s: %s", marker.stem, type(exc).__name__)
    return deleted, invalid
