from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import nonebot

nonebot.init()

from src.plugins.ai_chat.agent_tools import AgentToolExecutor
from src.plugins.ai_chat.conversation_scope import ConversationScope
from src.plugins.ai_chat.ledger import MessageLedger
from src.plugins.ai_chat.message_ir import ForwardNode, MessageBody


class FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[Any] = []
        self.uploads: list[dict[str, Any]] = []

    async def get_msg(self, *, message_id: int) -> dict[str, Any]:
        if message_id == 99:
            return {
                "message_id": message_id,
                "group_id": 100,
                "user_id": 7,
                "time": 125,
                "sender": {"card": "Alice"},
                "message": [
                    {
                        "type": "file",
                        "data": {
                            "file": "report.pdf",
                            "file_id": "message-file-1",
                            "file_size": 5,
                        },
                    }
                ],
            }
        return {
            "message_id": message_id,
            "group_id": 100,
            "user_id": 7,
            "time": 123,
            "sender": {"card": "Alice"},
            "message": [{"type": "text", "data": {"text": "hello project"}}],
        }

    async def call_api(self, api: str, **data: Any) -> Any:
        if api == "get_group_msg_history":
            return {
                "messages": [
                    {
                        "message_id": 1,
                        "user_id": 7,
                        "time": 123,
                        "sender": {"nickname": "Alice"},
                        "message": [
                            {"type": "text", "data": {"text": "hello project"}}
                        ],
                    },
                    {
                        "message_id": 2,
                        "user_id": 8,
                        "time": 124,
                        "sender": {"nickname": "Bob"},
                        "message": [
                            {"type": "text", "data": {"text": "unrelated"}}
                        ],
                    },
                ]
            }
        if api == "get_group_root_files":
            return {
                "files": [
                    {
                        "file_id": "file-1",
                        "file_name": "input.txt",
                        "file_size": 5,
                        "upload_time": 123,
                        "uploader": 7,
                    }
                ]
            }
        if api == "get_forward_msg":
            return {
                "messages": [
                    {
                        "user_id": 7,
                        "time": 126,
                        "sender": {"user_id": 7, "nickname": "Alice"},
                        "message": [
                            {"type": "text", "data": {"text": "inside"}},
                            {"type": "forward", "data": {"id": "nested-native"}},
                        ],
                    }
                ]
            }
        if api == "get_file":
            return {"base64": base64.b64encode(b"hello").decode("ascii")}
        if api == "upload_group_file":
            self.uploads.append(data)
            return {"file_id": "uploaded"}
        raise AssertionError(f"unexpected API: {api}")

    async def send_group_msg(self, **data: Any) -> dict[str, int]:
        self.sent_messages.append(data)
        return {"message_id": len(self.sent_messages)}


class FakeSandboxManager:
    def __init__(self) -> None:
        self.files: dict[tuple[str, str], bytes] = {
            ("s123abc", "result.txt"): b"done"
        }
        self.created: list[str] = []
        self.destroyed: list[str] = []

    async def create(self, owner: str, runtime: str) -> dict[str, str]:
        del owner
        sandbox_id = f"s{len(self.created) + 1:06x}"
        self.created.append(sandbox_id)
        return {
            "sandbox_id": sandbox_id,
            "runtime": runtime,
            "status": "running",
        }

    async def destroy(self, owner: str, sandbox_id: str) -> None:
        del owner
        self.destroyed.append(sandbox_id)

    async def write_file(
        self,
        owner: str,
        sandbox_id: str,
        path: str,
        content: bytes,
        *,
        allow_large: bool = False,
    ) -> int:
        del owner, allow_large
        self.files[(sandbox_id, path)] = content
        return len(content)

    async def read_file(
        self,
        owner: str,
        sandbox_id: str,
        path: str,
        max_bytes: int,
    ) -> bytes:
        del owner
        content = self.files[(sandbox_id, path)]
        if len(content) > max_bytes:
            raise ValueError("too large")
        return content


class FakeContentSource:
    def as_tool_payload(self, *, cached: bool) -> dict[str, object]:
        return {
            "handle": "source#12",
            "platform": "bilibili",
            "title": "Test video",
            "cached": cached,
        }


class FakeContentSourceStore:
    def __init__(self) -> None:
        self.targets: list[str] = []

    async def inspect(self, scope, target: str, **kwargs):
        del scope, kwargs
        self.targets.append(target)
        return FakeContentSource(), False

    def get_cached(self, scope, source_handle: str):
        del scope
        self.targets.append(source_handle)
        return FakeContentSource()


class AgentToolExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = FakeBot()
        self.sandbox = FakeSandboxManager()
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.ledger = MessageLedger(
            Path(self.temporary_directory.name) / "ledger.sqlite3"
        )
        self.addCleanup(self.ledger.close)
        self.scope = ConversationScope(
            platform="onebot-v11",
            kind="group",
            native_conversation_id="100",
            actor_native_user_id="7",
            bot_native_user_id="42",
        )
        self.executor = AgentToolExecutor(
            bot=self.bot,  # type: ignore[arg-type]
            event=SimpleNamespace(group_id=100),  # type: ignore[arg-type]
            owner="group:100:user:7",
            sandbox_manager=self.sandbox,  # type: ignore[arg-type]
            max_file_bytes=1024,
            ledger=self.ledger,
            scope=self.scope,
        )

    async def test_search_messages_filters_current_history(self) -> None:
        result = json.loads(
            await self.executor.execute(
                "search_messages",
                {"query": "PROJECT", "limit": 10},
            )
            or "{}"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            [item["handle"] for item in result["messages"]],
            ["msg#1"],
        )

    async def test_unified_shared_content_tool_uses_scoped_source_store(self) -> None:
        source_store = FakeContentSourceStore()
        self.executor.source_store = source_store  # type: ignore[assignment]
        result = json.loads(
            await self.executor.execute(
                "inspect_shared_content",
                {"target": "source#12"},
            )
            or "{}"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"]["handle"], "source#12")
        self.assertEqual(source_store.targets, ["source#12"])

    async def test_import_requires_file_from_current_group(self) -> None:
        listed = json.loads(
            await self.executor.execute(
                "list_recent_files",
                {"limit": 10},
            )
            or "{}"
        )
        file_handle = listed["files"][0]["handle"]

        rejected = json.loads(
            await self.executor.execute(
                "import_file_to_sandbox",
                {
                    "sandbox_id": "s123abc",
                    "file_handle": "groupfile#not-in-this-scope",
                    "destination": "input.txt",
                },
            )
            or "{}"
        )
        self.assertFalse(rejected["ok"])

        imported = json.loads(
            await self.executor.execute(
                "import_file_to_sandbox",
                {
                    "sandbox_id": "s123abc",
                    "file_handle": file_handle,
                    "destination": "input.txt",
                },
            )
            or "{}"
        )
        self.assertTrue(imported["ok"])
        self.assertEqual(self.sandbox.files[("s123abc", "input.txt")], b"hello")

    async def test_imports_attachment_directly_from_replied_message(self) -> None:
        canonical_message_id = await self.executor.ensure_canonical_message(99)
        self.assertIsNotNone(canonical_message_id)
        message = json.loads(
            await self.executor.execute(
                "get_message_by_id",
                {"message_handle": f"msg#{canonical_message_id}"},
            )
            or "{}"
        )
        attachment = message["message"]["attachments"][0]
        self.assertEqual(
            attachment["handle"],
            f"file#{canonical_message_id}.0",
        )
        self.assertNotIn("file_id", attachment)

        imported = json.loads(
            await self.executor.execute(
                "import_file_to_sandbox",
                {
                    "sandbox_id": "s123abc",
                    "message_handle": f"msg#{canonical_message_id}",
                    "destination": "report.pdf",
                },
            )
            or "{}"
        )
        self.assertTrue(imported["ok"])
        self.assertEqual(
            self.sandbox.files[("s123abc", "report.pdf")],
            b"hello",
        )

    async def test_say_allows_repeated_progress_messages(self) -> None:
        seeded = await self.executor.ensure_canonical_message(500)
        self.assertEqual(seeded, 1)
        results = []
        for index in range(10):
            result = await self.executor.execute(
                "say",
                {"text": f"step {index}"},
            )
            results.append(json.loads(result or "{}"))

        self.assertTrue(all(item["ok"] for item in results))
        self.assertEqual(len(self.bot.sent_messages), 10)
        self.assertEqual(results[0]["message_handle"], "msg#2")

    async def test_message_tools_fail_closed_without_canonical_ledger(self) -> None:
        degraded = AgentToolExecutor(
            bot=self.bot,  # type: ignore[arg-type]
            event=SimpleNamespace(group_id=100),  # type: ignore[arg-type]
            owner="group:100:user:7",
            sandbox_manager=self.sandbox,  # type: ignore[arg-type]
            max_file_bytes=1024,
        )

        result = json.loads(
            await degraded.execute(
                "get_message_by_id",
                {"message_handle": "msg#1"},
            )
            or "{}"
        )

        self.assertFalse(result["ok"])
        self.assertIn("拒绝", result["error"])

    async def test_recent_file_uploader_does_not_expose_qq_id(self) -> None:
        await self.executor.ensure_canonical_message(1)
        result = json.loads(
            await self.executor.execute(
                "list_recent_files",
                {"limit": 10},
            )
            or "{}"
        )

        self.assertTrue(result["ok"])
        recent_file = result["files"][0]
        self.assertIn("[mention#", recent_file["uploader"])
        self.assertNotIn("7", recent_file["uploader"].split(" ")[0])
        self.assertTrue(recent_file["handle"].startswith("groupfile#"))
        self.assertNotIn("file_id", recent_file)
        self.assertNotIn("_native_file_id", recent_file)

    async def test_send_file_uploads_base64_to_current_group(self) -> None:
        result = json.loads(
            await self.executor.execute(
                "send_file_from_sandbox",
                {
                    "sandbox_id": "s123abc",
                    "path": "result.txt",
                    "filename": "../result.txt",
                },
            )
            or "{}"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(self.bot.uploads[0]["group_id"], 100)
        self.assertEqual(self.bot.uploads[0]["name"], "result.txt")
        self.assertEqual(
            self.bot.uploads[0]["file"],
            "base64://" + base64.b64encode(b"done").decode("ascii"),
        )

    async def test_task_sandboxes_are_destroyed_once_after_delivery(self) -> None:
        first = json.loads(
            await self.executor.execute("sandbox_create", {"runtime": "python"})
            or "{}"
        )
        second = json.loads(
            await self.executor.execute("sandbox_create", {"runtime": "node"})
            or "{}"
        )
        first_id = first["sandbox"]["sandbox_id"]
        second_id = second["sandbox"]["sandbox_id"]

        await self.executor.execute(
            "sandbox_destroy",
            {"sandbox_id": first_id},
        )
        cleanup = await self.executor.cleanup_task_sandboxes()
        repeated = await self.executor.cleanup_task_sandboxes()

        self.assertEqual(cleanup["destroyed"], (second_id,))
        self.assertEqual(cleanup["failed"], ())
        self.assertEqual(repeated["destroyed"], ())
        self.assertEqual(self.sandbox.destroyed, [first_id, second_id])

    async def test_forward_expansion_never_exposes_native_user_or_forward_ids(self) -> None:
        await self.executor.ensure_canonical_message(1)
        stored = self.ledger.record_message(
            self.scope,
            native_message_id="forward-parent",
            sender_native_user_id="7",
            sender_display="Alice",
            body=MessageBody((ForwardNode(0, "forward-native"),)),
            occurred_at=126,
        )
        result = json.loads(
            await self.executor.execute(
                "view_forward",
                {"message_handle": f"msg#{stored.canonical_message_id}"},
            )
            or "{}"
        )

        self.assertTrue(result["ok"])
        child = result["children"][0]
        self.assertEqual(child["sender_handle"], "[mention#1]")
        self.assertTrue(child["has_nested_forward"])
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("forward-native", encoded)
        self.assertNotIn("nested-native", encoded)
        self.assertNotIn('"user_id"', encoded)


if __name__ == "__main__":
    unittest.main()
