"""Per-step containers and immutable host-owned artifact snapshots."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import tempfile
from pathlib import Path, PurePosixPath

from .control import assert_job_owned
from .execution import active_agent_step


IMPORT_AGENT_ARTIFACT = {"type": "function", "function": {
    "name": "import_agent_artifact", "description": "将已授权上游的产物快照导入自己的沙盒；原始快照只读，构建前复制到自己的工作目录。",
    "parameters": {"type": "object", "additionalProperties": False, "properties": {
        "step_id": {"type": "string"}, "artifact_index": {"type": "integer", "minimum": 0},
        "sandbox_id": {"type": "string"}}, "required": ["step_id", "artifact_index", "sandbox_id"]}}}


class StepWorkspaces:
    def __init__(self, root: Path, executor):
        self.root = root / "subagent_artifacts"
        self.executor = executor
        self.manager = executor.sandbox_manager

    def _path(self, task_id: int, digest: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid artifact digest")
        return self.root / str(int(task_id)) / digest

    def _persist(self, task_id: int, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        path = self._path(task_id, digest)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
                output.write(content)
                name = output.name
            os.chmod(name, 0o400)
            os.replace(name, path)
        return digest

    async def capture(self, task_id: int, artifacts: list) -> list[dict]:
        captured = []
        for item in artifacts:
            if not isinstance(item, dict):
                raise ValueError("Invalid artifact entry")
            match = re.fullmatch(r"(s[0-9a-f]{6}):(/workspace/.+)", str(item.get("handle", "")))
            if not match:
                raise ValueError("Artifact must reference a real sandbox file")
            sandbox_id, path = match.groups()
            content = await self.manager.read_file(self.executor.owner, sandbox_id, path, max_bytes=None)
            if not content:
                raise ValueError("Artifact is empty")
            digest = await asyncio.to_thread(self._persist, task_id, content)
            captured.append({**item, "snapshot": digest, "size": len(content),
                             "name": PurePosixPath(str(item.get("name") or path)).name})
        return captured

    async def import_artifact(self, task_id: int, upstream: dict, arguments: dict) -> str:
        result = upstream.get(str(arguments.get("step_id")))
        if result is None:
            raise ValueError("This step may only read its declared upstream artifacts")
        index = arguments.get("artifact_index")
        artifacts = result.get("artifacts", [])
        if not isinstance(index, int) or index < 0 or index >= len(artifacts):
            raise ValueError("Unknown upstream artifact")
        item = artifacts[index]
        content = await asyncio.to_thread(self._path(task_id, item.get("snapshot", "")).read_bytes)
        sandbox_id = str(arguments.get("sandbox_id", ""))
        filename = PurePosixPath(item["name"]).name
        if filename in {"", ".", ".."}:
            raise ValueError("Invalid artifact filename")
        target = f"/workspace/upstream/{item['snapshot']}/{filename}"
        await self.manager.install_readonly_file(self.executor.owner, sandbox_id, target, content)
        return json.dumps({"ok": True, "path": target, "sha256": item["snapshot"], "read_only": True})

    async def validate(self, task_id: int, artifact: dict) -> dict:
        content = await asyncio.to_thread(self._path(task_id, artifact.get("snapshot", "")).read_bytes)
        if not content or hashlib.sha256(content).hexdigest() != artifact["snapshot"]:
            return {"ok": False, "error": "Artifact checksum mismatch or empty file"}
        # Format checks run in a separate container, not in the producing agent's process.
        sandbox = await self.manager.create(self.executor.owner, "python")
        sid = sandbox["sandbox_id"]
        suffix = Path(artifact["name"]).suffix.lower()
        path = "/workspace/acceptance" + suffix
        try:
            await self.manager.write_file(self.executor.owner, sid, path, content, allow_large=True)
            commands = {
                ".pdf": "pdfinfo /workspace/acceptance.pdf && pdffonts /workspace/acceptance.pdf && pdftotext /workspace/acceptance.pdf -",
                ".zip": "unzip -t /workspace/acceptance.zip",
                ".docx": "unzip -t /workspace/acceptance.docx", ".xlsx": "unzip -t /workspace/acceptance.xlsx",
                ".pptx": "unzip -t /workspace/acceptance.pptx",
                ".png": "python -c 'from PIL import Image; Image.open(\"/workspace/acceptance.png\").verify()'",
            }
            command = commands.get(suffix)
            if not command:
                return {"ok": True, "checks": ["nonempty", "sha256"], "functional": "requires_review"}
            check = await self.manager.exec(self.executor.owner, sid, command, 45)
            ok = check.returncode == 0
            if suffix == ".pdf":
                ok = ok and bool(re.search(r"Pages:\s*[1-9][0-9]*", check.stdout))
                font_lines = [line for line in check.stdout.splitlines() if re.search(r"\s+(yes|no)\s+(yes|no)\s+(yes|no)\s+\d+\s+\d+\s*$", line)]
                ok = ok and bool(font_lines) and all(re.search(r"\s+yes\s+(?:yes|no)\s+(?:yes|no)\s+\d+\s+\d+\s*$", line) for line in font_lines)
                # A rendered first page catches broken PDFs that text extraction alone misses.
                rendered = await self.manager.exec(self.executor.owner, sid,
                    "pdftoppm -f 1 -singlefile -scale-to 1000 -png /workspace/acceptance.pdf /workspace/rendered", 45)
                ok = ok and rendered.returncode == 0
            return {"ok": ok, "checks": ["nonempty", "sha256", "format"],
                    "details": (check.stdout + check.stderr)[-4000:], "functional": "requires_review"}
        finally:
            await self.manager.destroy(self.executor.owner, sid)

    async def deliver(self, task_id: int, artifact: dict) -> str:
        assert_job_owned()
        content = await asyncio.to_thread(self._path(task_id, artifact["snapshot"]).read_bytes)
        sandbox = await self.manager.create(self.executor.owner, "python")
        sid = sandbox["sandbox_id"]
        try:
            path = "/workspace/" + PurePosixPath(artifact["name"]).name
            await self.manager.write_file(self.executor.owner, sid, path, content, allow_large=True)
            assert_job_owned()
            return await self.executor._send_file_from_sandbox({"sandbox_id": sid, "path": path, "filename": artifact["name"]})
        finally:
            await self.manager.destroy(self.executor.owner, sid)

    async def reconcile(self, filename: str, size: int) -> dict:
        response = await self.executor.bot.call_api("get_group_root_files", group_id=self.executor.event.group_id)
        files = response.get("files", []) if isinstance(response, dict) else []
        match = next((f for f in files if f.get("file_name") == filename and int(f.get("file_size", -1)) == size
                      and str(f.get("uploader")) == str(self.executor.bot.self_id)), None)
        return {"ok": bool(match), "filename": filename, "size": size,
                "file_id": match.get("file_id") if match else None, "reconciled": bool(match)}

    async def cleanup_step(self):
        for sandbox in await self.manager.list(self.executor.owner):
            if sandbox.get("purpose", "task") == "task":
                await self.manager.destroy(self.executor.owner, sandbox["sandbox_id"])

    async def restore_step(self):
        for sandbox in await self.manager.list(self.executor.owner):
            if sandbox.get("purpose", "task") == "task":
                await self.manager.start_owned(self.executor.owner, sandbox["sandbox_id"])

    async def cleanup_task(self, task_id: int, runs):
        for run in runs:
            token = active_agent_step.set(f"task#{task_id}/{run.step_key}")
            try:
                await self.cleanup_step()
            finally:
                active_agent_step.reset(token)
