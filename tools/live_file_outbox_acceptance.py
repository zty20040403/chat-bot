"""Opt-in live QQ transport acceptance; never start or stop the production bot."""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import ipaddress
import json
import multiprocessing
import os
from pathlib import Path
import re
import signal
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

import httpx

BOUNDARIES = ("queued", "prepared", "claimed", "uploaded", "acknowledged")
MAX_BYTES = 1024


class AcceptanceError(RuntimeError):
    """A bounded test failed; messages intentionally exclude credentials."""


def validate_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
        port = parsed.port
    except ValueError:
        loopback, port = False, None
    if (parsed.scheme not in {"http", "https"} or not loopback or not port
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise AcceptanceError("Use an existing loopback WebUI endpoint without credentials in its URL")
    return value.rstrip("/")


def reserve_upload(directory: Path, filename: str) -> None:
    # Durable pre-send reservation: an interrupted test must not reuse an upload.
    with (directory / (filename + ".upload-attempt")).open("x") as stream:
        stream.write("reserved\n")
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_artifact(directory: Path, filename: str, digest: str) -> bytes:
    content = (directory / filename).read_bytes()
    if not 0 < len(content) <= MAX_BYTES or hashlib.sha256(content).hexdigest() != digest:
        raise AcceptanceError("The persisted test artifact does not match its immutable manifest")
    return content


def read_token(path: Path) -> str:
    value = path.read_text().strip()
    if value.startswith("{"):
        value = json.loads(value).get("token")
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise AcceptanceError("Invalid token file")
    return value.strip()


class NapCatTransport:
    def __init__(self, url: str, credential: str, bot_id: int, group_id: int,
                 directory: Path, filename: str = "", content: bytes = b"", *, after_upload=None,
                 transport=None):
        self.url = validate_url(url)
        self.credential = credential
        self.self_id = str(bot_id)
        self.group_id = group_id
        self.directory = directory
        self.filename = filename
        self.content = content
        self.after_upload = after_upload
        self.transport = transport
        self.upload_attempts = 0

    async def post(self, path: str, body: dict, *, authenticated=True):
        headers = {"Authorization": "Bearer " + self.credential} if authenticated else {}
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=90,
                                    transport=self.transport) as client:
            async with client.stream("POST", self.url + path, json=body, headers=headers) as response:
                if response.status_code != 200:
                    raise AcceptanceError(f"NapCat HTTP status {response.status_code}")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 2 * 1024 * 1024:
                        raise AcceptanceError("NapCat response exceeded the test size limit")
        try:
            result = json.loads(raw)
        except ValueError:
            raise AcceptanceError("NapCat returned invalid JSON") from None
        if not isinstance(result, dict) or result.get("code") != 0:
            raise AcceptanceError("NapCat rejected the request; inspect its local logs")
        return result.get("data")

    async def login(self, token: str) -> None:
        digest = hashlib.sha256((token + ".napcat").encode()).hexdigest()
        result = await self.post("/api/auth/login", {"hash": digest}, authenticated=False)
        if not isinstance(result, dict) or result.get("require2FA") or not result.get("Credential"):
            raise AcceptanceError("NapCat login requires user action; do not bypass second-factor authentication")
        self.credential = result["Credential"]
        identity = await self.call_api("get_login_info")
        if not isinstance(identity, dict) or str(identity.get("user_id")) != self.self_id:
            raise AcceptanceError("The logged-in QQ account does not match the approved account")

    async def call_api(self, action: str, **params):
        if action == "get_login_info":
            allowed = not params
        elif action == "get_group_root_files":
            allowed = params == {"group_id": self.group_id}
        elif action == "upload_group_file":
            allowed = (bool(re.fullmatch(r"gaoji-acceptance-[a-f0-9]{32}-(?:queued|prepared|uploaded|acknowledged)\.txt", self.filename))
                       and 0 < len(self.content) <= MAX_BYTES
                       and params == {"group_id": self.group_id, "name": self.filename,
                                      "file": "base64://" + base64.b64encode(self.content).decode("ascii")})
        else:
            allowed = False
        if not allowed:
            raise AcceptanceError("API action or upload differs from the approved test manifest")
        if action == "upload_group_file":
            self.upload_attempts += 1
            reserve_upload(self.directory, self.filename)
        # WebUI Debug calls the action directly; it may skip schema defaults.
        wire_params = {**params, "file_count": 50} if action == "get_group_root_files" else params
        result = await self.post("/api/Debug/call", {"action": action, "params": wire_params})
        if isinstance(result, dict) and "retcode" in result:
            if result.get("retcode") != 0 or result.get("status") != "ok":
                raise AcceptanceError("The OneBot action failed; inspect its local logs")
            result = result.get("data")
        if action == "upload_group_file" and self.after_upload:
            self.after_upload()
        return result


def load_runtime():
    # Import library modules without the service bootstrap's recovery side effects.
    import types
    import nonebot
    if "src.plugins.ai_chat" in sys.modules:
        raise AcceptanceError("Run in a separate interpreter, never inside the production bot")
    nonebot.init()
    package = types.ModuleType("src.plugins.ai_chat")
    package.__path__ = [str(Path(__file__).resolve().parents[1] / "src/plugins/ai_chat")]
    sys.modules[package.__name__] = package
    from src.bot_storage.database import PostgresDatabase
    from src.plugins.ai_chat.agent.file_outbox import attempt_file
    from src.plugins.ai_chat.agent.background import SubAgentDispatcher
    from src.plugins.ai_chat.agent_tools import AgentToolExecutor
    from src.plugins.ai_chat.subagents import SubAgentStore
    from nonebot.adapters.onebot.v11 import GroupMessageEvent
    return SimpleNamespace(database=PostgresDatabase, store=SubAgentStore, attempt=attempt_file,
        dispatcher=SubAgentDispatcher, executor=AgentToolExecutor, event=GroupMessageEvent)


def open_store(runtime, dsn, schema):
    if not re.fullmatch(r"test_live_file_[a-f0-9]{32}", schema):
        raise AcceptanceError("Only a randomly generated acceptance schema is permitted")
    database = runtime.database(dsn, schema=schema, min_size=1, max_size=2,
                                application_name="gaoji-file-acceptance")
    return database, runtime.store(database)


def make_executor(runtime, bot, event):
    return runtime.executor(bot=bot, event=event, owner="live-file-acceptance",
                            sandbox_manager=None, max_file_bytes=MAX_BYTES)


def pause_at(pipe, boundary):
    pipe.send({"boundary": boundary, "pid": os.getpid()})
    while True:
        time.sleep(60)


def crash_worker(config, dsn, task_id, boundary, content, pipe):
    runtime = load_runtime()
    database, store = open_store(runtime, dsn, config["schema"])
    row = store.deliveries(task_id)[0]
    pause = lambda: pause_at(pipe, boundary)
    bot = NapCatTransport(config["url"], config["credential"], config["bot_id"], config["group_id"],
        Path(config["directory"]), row["payload"]["filename"], content,
        after_upload=pause if boundary == "uploaded" else None)
    event = runtime.event.model_validate(config["event"])
    executor = make_executor(runtime, bot, event)

    async def run():
        if boundary == "queued":
            pause()
        async def prepare(_artifact):
            data = read_artifact(Path(config["directory"]), row["payload"]["filename"], row["key"])
            if boundary == "prepared":
                pause()
            return data
        async def send(data, filename):
            if boundary == "claimed":
                pause()
            return await executor.send_file_content(data, filename)
        result = await runtime.attempt(store, task_id, row, prepare=prepare, send=send)
        if boundary == "acknowledged" and result.get("ok"):
            pause()
        raise AcceptanceError("The intended crash boundary was not reached")
    try:
        asyncio.run(run())
    except Exception as exc:
        pipe.send({"error": type(exc).__name__})
    finally:
        store.close()
        database.close()
        pipe.close()


async def reconcile(runtime, store, task_id, bot, directory):
    from unittest.mock import patch
    dispatcher = object.__new__(runtime.dispatcher)
    dispatcher.store = store
    dispatcher.context = SimpleNamespace(state_dir=directory, sandbox_manager=None,
        settings=SimpleNamespace(subagent_retention_seconds=3600))
    dispatcher.coordinator = SimpleNamespace(_artifact_retention_state=lambda _: (False, set()))
    def get_test_bot(identifier):
        if str(identifier) != bot.self_id:
            raise AcceptanceError("Unexpected bot identity during reconciliation")
        return bot
    # Only replace service discovery in this isolated process, not the receipt matcher.
    with patch("src.plugins.ai_chat.agent.background.get_bot", side_effect=get_test_bot):
        await dispatcher.reconcile(task_id)


async def exercise(runtime, config, dsn, boundary):
    directory = Path(config["directory"])
    content = f"GAOJI isolated file recovery test\nrun={config['run']}\nboundary={boundary}\n".encode()
    filename = f"gaoji-acceptance-{config['run']}-{boundary}.txt"
    digest = hashlib.sha256(content).hexdigest()
    with (directory / filename).open("xb") as artifact_file:
        artifact_file.write(content)
        artifact_file.flush()
        os.fsync(artifact_file.fileno())
    database, store = open_store(runtime, dsn, config["schema"])
    process = parent = child = None
    try:
        task = store.create_task(scope_key=f"onebot-v11:group:{config['group_id']}",
            conversation_id=f"group:{config['group_id']}:user:{config['requester_id']}",
            requester_user_id=config["requester_id"], trigger_message_id=None,
            objective="Isolated live file recovery acceptance", max_parallelism=1, max_steps=2)
        store.update_control(task.task_id, expected_version=0,
            dispatch={"bot_id": str(config["bot_id"]), "event": config["event"]})
        artifact = {"name": filename, "snapshot": digest, "size": len(content),
                    "handle": "acceptance:" + digest}
        store.queue_file(task.task_id, artifact, filename)
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=crash_worker, args=(config, dsn, task.task_id, boundary, content, child))
        process.start()
        child.close()
        if not await asyncio.to_thread(parent.poll, 120):
            raise AcceptanceError("Test child did not reach its boundary before the deadline")
        observed = parent.recv()
        if observed != {"boundary": boundary, "pid": process.pid}:
            raise AcceptanceError("The child reported a different boundary or failed")
        process.kill()
        await asyncio.to_thread(process.join, 10)
        if process.exitcode != -signal.SIGKILL:
            raise AcceptanceError("The isolated child was not confirmed killed")
        store.close()
        database.close()
        database, store = open_store(runtime, dsn, config["schema"])
        before = store.deliveries(task.task_id)[0]
        expected = {"queued": "queued", "prepared": "queued", "claimed": "sending",
                    "uploaded": "sending", "acknowledged": "acknowledged"}[boundary]
        if before["state"] != expected or before["payload"]["artifact"] != artifact:
            raise AcceptanceError("The durable manifest did not survive as expected")
        bot = NapCatTransport(config["url"], config["credential"], config["bot_id"], config["group_id"],
                              directory, filename, content)
        executor = make_executor(runtime, bot, runtime.event.model_validate(config["event"]))
        async def prepare(_artifact):
            return read_artifact(directory, filename, digest)
        await runtime.attempt(store, task.task_id, before, prepare=prepare, send=executor.send_file_content)
        recovery_uploads = 1 if boundary in {"queued", "prepared"} else 0
        if bot.upload_attempts != recovery_uploads:
            raise AcceptanceError("Recovery attempted an unexpected upload, even if the test guard blocked it")
        target = "unknown" if boundary == "claimed" else "acknowledged"
        for attempt in range(8):
            await reconcile(runtime, store, task.task_id, bot, directory)
            final = store.deliveries(task.task_id)[0]
            if final["state"] == target:
                break
            await asyncio.sleep(2)
        receipts = await bot.call_api("get_group_root_files", group_id=config["group_id"])
        matches = [row for row in receipts.get("files", []) if row.get("file_name") == filename]
        expected_uploads = 0 if boundary == "claimed" else 1
        if (final["state"] != target or len(matches) != expected_uploads
                or any(str(row.get("uploader")) != bot.self_id or row.get("file_size") != len(content)
                       for row in matches)):
            raise AcceptanceError("QQ receipt or durable state did not match the boundary expectation")
        await runtime.attempt(store, task.task_id, final, prepare=prepare, send=executor.send_file_content)
        if bot.upload_attempts != recovery_uploads or store.deliveries(task.task_id)[0]["state"] != target:
            raise AcceptanceError("A repeated recovery changed settled state or attempted another upload")
        return {"boundary": boundary, "state_after_kill": before["state"], "final_state": final["state"],
                "upload_reservations": int((directory / (filename + ".upload-attempt")).exists()),
                "qq_receipts": len(matches), "filename": filename, "sha256": digest,
                "file_ids": [row.get("file_id") for row in matches]}
    finally:
        if process is not None and process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, 10)
        if parent:
            parent.close()
        if child:
            child.close()
        store.close()
        database.close()


async def run_live(args):
    import psycopg
    from psycopg import sql
    from alembic import command
    from alembic.config import Config
    from unittest.mock import patch
    dsn = os.environ.get("TEST_POSTGRES_DSN", "")
    if not dsn or dsn == os.environ.get("AI_POSTGRES_DSN"):
        raise AcceptanceError("Supply TEST_POSTGRES_DSN for a separate test database, not the bot DSN")
    database_name = psycopg.conninfo.conninfo_to_dict(dsn).get("dbname", "")
    if not re.fullmatch(r"gaoji_acceptance(?:_[a-z0-9]+)?", database_name):
        raise AcceptanceError("Use a dedicated database named gaoji_acceptance or gaoji_acceptance_<suffix>")
    dsn = psycopg.conninfo.make_conninfo(dsn, connect_timeout=10, application_name="gaoji-file-acceptance")
    config = {"url": validate_url(args.webui_url), "bot_id": args.bot_id, "group_id": args.group_id,
              "requester_id": args.requester_id, "directory": str(args.output_dir.resolve()), "run": uuid.uuid4().hex}
    directory = Path(config["directory"])
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    config["schema"] = "test_live_file_" + uuid.uuid4().hex
    report = {"transport": "live-napcat-webui", "scope": "isolated-test-database-and-child",
              "schema": config["schema"], "status": "running", "phase": "preflight_login", "cases": []}
    def save():
        temporary = directory / "result.tmp"
        with temporary.open("w") as stream:
            json.dump(report, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(directory / "result.json")
    created = False
    try:
        save()
        token_data = read_token(args.token_file)
        transport = NapCatTransport(config["url"], "", args.bot_id, args.group_id, directory)
        await transport.login(token_data)
        report["phase"] = "preflight_group_files"
        save()
        await transport.call_api("get_group_root_files", group_id=args.group_id)
        config["credential"] = transport.credential
        runtime = load_runtime()
        config["event"] = runtime.event(time=int(time.time()), self_id=args.bot_id, post_type="message",
            message_type="group", sub_type="normal", message_id=0, group_id=args.group_id,
            user_id=args.requester_id, message="Isolated acceptance", raw_message="Isolated acceptance",
            font=0, sender={"user_id": args.requester_id}).model_dump(mode="json")
        report["phase"] = "database_setup"
        save()
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(config["schema"])))
            created = True
        save()
        with patch.dict(os.environ, AI_POSTGRES_DSN=dsn, AI_POSTGRES_SCHEMA=config["schema"]):
            command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
        async with asyncio.timeout(600):
            for boundary in BOUNDARIES:
                report["phase"] = boundary
                save()
                report["cases"].append(await exercise(runtime, config, dsn, boundary))
                save()
        report["status"] = "passed"
    except BaseException as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        try:
            if created:
                with psycopg.connect(dsn, autocommit=True) as connection:
                    connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(config["schema"])))
            report["schema_cleanup"] = "completed" if created else "not_created"
        except Exception as exc:
            report.update(status="failed", schema_cleanup="failed", cleanup_error_type=type(exc).__name__)
            raise AcceptanceError("Test schema cleanup failed; inspect the private result file") from None
        finally:
            save()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--webui-url", required=True)
    parser.add_argument("--bot-id", type=int, required=True)
    parser.add_argument("--group-id", type=int, required=True)
    parser.add_argument("--requester-id", type=int, required=True)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--confirm-group", type=int)
    args = parser.parse_args(argv)
    try:
        validate_url(args.webui_url)
    except AcceptanceError as exc:
        parser.error(str(exc))
    if min(args.bot_id, args.group_id, args.requester_id) <= 0:
        parser.error("QQ identifiers must be positive")
    if not args.execute_live:
        print(json.dumps({"mode": "plan-only", "boundaries": BOUNDARIES, "max_uploads": 4,
            "max_bytes_per_file": MAX_BYTES, "kills": "owned test children only",
            "database": "new isolated schema in TEST_POSTGRES_DSN",
            "requires_user_approval": True}))
        return 0
    if args.confirm_group != args.group_id or not args.token_file or not args.output_dir:
        parser.error("Live execution requires explicit group confirmation, a token file and a NEW output directory")
    try:
        result = asyncio.run(run_live(args))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "message": str(exc) if isinstance(exc, AcceptanceError) else
                          "Inspect private test output; credentials and server payloads are not printed"}))
        return 1
    print(json.dumps({"status": result["status"], "boundaries_checked": len(result["cases"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
