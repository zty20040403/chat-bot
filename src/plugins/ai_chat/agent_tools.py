from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed

from .ai_tools import (
    GET_MESSAGE_BY_ID_TOOL_NAME,
    IMPORT_FILE_TO_SANDBOX_TOOL_NAME,
    LIST_RECENT_FILES_TOOL_NAME,
    SANDBOX_CREATE_TOOL_NAME,
    SANDBOX_DESTROY_TOOL_NAME,
    SANDBOX_EXEC_TOOL_NAME,
    SANDBOX_LIST_TOOL_NAME,
    SANDBOX_READ_FILE_TOOL_NAME,
    SANDBOX_WRITE_FILE_TOOL_NAME,
    SAY_TOOL_NAME,
    SEARCH_MESSAGES_TOOL_NAME,
    SEND_FILE_FROM_SANDBOX_TOOL_NAME,
    SEND_IMAGE_FROM_SANDBOX_TOOL_NAME,
)
from .conversation_scope import ConversationScope
from .ledger import CanonicalMessage, MessageLedger
from .message_ir import MediaNode, MessageBody, TextNode
from .onebot_codec import (
    record_onebot_api_message,
    render_api_attachments,
)
from .sandbox import DockerSandboxManager, SandboxError
from .turn_journal import TurnJournal

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MESSAGE_HANDLE_PATTERN = re.compile(r"^msg#([1-9][0-9]*)$")
AGENT_TOOL_PROMPT = (
    "你可以使用当前群聊的历史消息、群文件和隔离 Docker 开发沙盒。"
    "遇到创建项目、修改群文件、安装依赖、构建、测试或打包任务时，"
    "必须实际调用工具完成，不要只给示例代码后声称已经做好。"
    "长任务开始和每个关键阶段都要用 say 发简短进度；"
    "有新的实际进展时可以持续汇报，但不要重复发送没有信息量的内容。"
    "先创建合适运行环境的沙盒，再写入或导入文件、执行构建和测试；"
    "需要交付时，用 send_file_from_sandbox 或 send_image_from_sandbox 发到当前群。"
    "只有工具结果明确成功时才能说任务已完成。"
    "沙盒是临时开发环境，不等于公网部署；需要云平台账号或密钥时，"
    "先完成可运行项目和打包，再说明仍需用户提供外部部署条件。"
    "不要尝试访问宿主机、机器人密钥、其他用户沙盒或其他群的数据。"
    "工具参数里的 message_handle 一律完整照抄上下文或搜索结果中的 msg# 句柄，"
    "不要猜测 OneBot 原始消息 ID、群号或 QQ 号。"
)


def _json_result(*, ok: bool, **data: Any) -> str:
    return json.dumps({"ok": ok, **data}, ensure_ascii=False)


def _parse_message_handle(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    matched = MESSAGE_HANDLE_PATTERN.fullmatch(value.strip())
    return int(matched.group(1)) if matched is not None else None


def _message_segments(raw_message: Any) -> list[dict[str, Any]]:
    if isinstance(raw_message, Message):
        return [
            {"type": segment.type, "data": dict(segment.data)}
            for segment in raw_message
        ]
    if isinstance(raw_message, list):
        return [
            item
            for item in raw_message
            if isinstance(item, dict) and isinstance(item.get("type"), str)
        ]
    if isinstance(raw_message, str):
        try:
            return [
                {"type": segment.type, "data": dict(segment.data)}
                for segment in Message(raw_message)
            ]
        except Exception:
            return [{"type": "text", "data": {"text": raw_message}}]
    return []


def _message_attachments(raw_message: Any) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for segment in _message_segments(raw_message):
        segment_type = segment.get("type", "")
        if segment_type not in {"file", "onlinefile"}:
            continue
        data = segment.get("data")
        if not isinstance(data, dict):
            continue
        attachments.append(
            {
                "file_id": str(data.get("file_id") or ""),
                "file_name": str(
                    data.get("name")
                    or data.get("fileName")
                    or data.get("file")
                    or ""
                ),
                "file_size": int(
                    data.get("file_size") or data.get("fileSize") or 0
                ),
                "type": segment_type,
            }
        )
    return attachments


def _safe_file_info(raw_file: dict[str, Any]) -> dict[str, Any]:
    return {
        "_native_file_id": str(raw_file.get("file_id", "")),
        "file_name": str(raw_file.get("file_name", "")),
        "file_size": int(raw_file.get("file_size") or raw_file.get("size") or 0),
        "upload_time": int(raw_file.get("upload_time") or 0),
        "uploader": int(raw_file.get("uploader") or raw_file.get("uploader_id") or 0),
    }


class AgentToolExecutor:
    def __init__(
        self,
        *,
        bot: Bot,
        event: GroupMessageEvent,
        owner: str,
        sandbox_manager: DockerSandboxManager,
        max_file_bytes: int,
        ledger: MessageLedger | None = None,
        scope: ConversationScope | None = None,
        turn_journal: TurnJournal | None = None,
        turn_id: int | None = None,
    ) -> None:
        self.bot = bot
        self.event = event
        self.owner = owner
        self.sandbox_manager = sandbox_manager
        self.max_file_bytes = max(1024, max_file_bytes)
        self.ledger = ledger
        self.scope = scope
        self.turn_journal = turn_journal
        self.turn_id = turn_id

    @property
    def canonical_messages_enabled(self) -> bool:
        return self.ledger is not None and self.scope is not None

    async def ensure_canonical_message(
        self,
        native_message_id: str | int,
    ) -> int | None:
        if not self.canonical_messages_enabled:
            return None
        assert self.ledger is not None
        assert self.scope is not None
        existing = self.ledger.canonical_id_for_native(
            self.scope,
            native_message_id,
        )
        if existing is not None:
            return existing

        message = await self.bot.get_msg(message_id=int(native_message_id))
        if int(message.get("group_id") or 0) != self.event.group_id:
            return None
        stored = record_onebot_api_message(
            self.ledger,
            self.scope,
            message,
            bot_native_user_id=self.scope.bot_native_user_id,
        )
        if stored is None:
            return None
        visible = self.ledger.get_in_scope(
            self.scope,
            stored.canonical_message_id,
        )
        return visible.canonical_message_id if visible is not None else None

    async def execute(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> str | None:
        handlers = {
            GET_MESSAGE_BY_ID_TOOL_NAME: self._get_message_by_id,
            SEARCH_MESSAGES_TOOL_NAME: self._search_messages,
            SANDBOX_CREATE_TOOL_NAME: self._sandbox_create,
            SANDBOX_LIST_TOOL_NAME: self._sandbox_list,
            SANDBOX_DESTROY_TOOL_NAME: self._sandbox_destroy,
            SANDBOX_EXEC_TOOL_NAME: self._sandbox_exec,
            SANDBOX_WRITE_FILE_TOOL_NAME: self._sandbox_write_file,
            SANDBOX_READ_FILE_TOOL_NAME: self._sandbox_read_file,
            SEND_FILE_FROM_SANDBOX_TOOL_NAME: self._send_file_from_sandbox,
            SEND_IMAGE_FROM_SANDBOX_TOOL_NAME: self._send_image_from_sandbox,
            LIST_RECENT_FILES_TOOL_NAME: self._list_recent_files,
            IMPORT_FILE_TO_SANDBOX_TOOL_NAME: self._import_file_to_sandbox,
            SAY_TOOL_NAME: self._say,
        }
        handler = handlers.get(name)
        if handler is None:
            return None

        try:
            return await handler(arguments)
        except SandboxError as exc:
            return _json_result(ok=False, error=str(exc))
        except ActionFailed as exc:
            logger.warning(f"NapCat agent tool {name} failed: {exc}")
            return _json_result(ok=False, error="NapCat 执行这个操作失败。")
        except (httpx.HTTPError, OSError, ValueError, sqlite3.Error) as exc:
            logger.warning(f"Agent tool {name} failed: {exc}")
            return _json_result(ok=False, error="工具执行失败。")

    async def _get_message_by_id(
        self,
        arguments: dict[str, object],
    ) -> str:
        message_id = _parse_message_handle(arguments.get("message_handle"))
        if message_id is None:
            return _json_result(ok=False, error="message_handle 格式无效。")

        if not self.canonical_messages_enabled:
            return _json_result(
                ok=False,
                error="规范消息账本不可用，已拒绝原生 ID 降级读取。",
            )
        assert self.ledger is not None
        assert self.scope is not None
        message = self.ledger.get_in_scope(self.scope, message_id)
        if message is None:
            return _json_result(
                ok=False,
                error="当前群可见上下文中找不到这条规范消息。",
            )
        return _json_result(
            ok=True,
            message=self._canonical_message_payload(message),
        )

    async def _search_messages(
        self,
        arguments: dict[str, object],
    ) -> str:
        query = str(arguments.get("query", "")).strip()
        limit = min(max(int(arguments.get("limit") or 10), 1), 20)
        if not query:
            return _json_result(ok=False, error="搜索关键词不能为空。")

        if not self.canonical_messages_enabled:
            return _json_result(
                ok=False,
                error="规范消息账本不可用，已拒绝原生 ID 降级搜索。",
            )
        assert self.ledger is not None
        assert self.scope is not None
        try:
            response = await self.bot.call_api(
                "get_group_msg_history",
                group_id=self.event.group_id,
                count=100,
                reverse_order=False,
            )
        except ActionFailed as exc:
            logger.warning(
                "NapCat history backfill failed; searching the local "
                f"message ledger instead: {exc}"
            )
        else:
            raw_messages = (
                response.get("messages", [])
                if isinstance(response, dict)
                else response
            )
            if isinstance(raw_messages, list):
                for raw in raw_messages:
                    if isinstance(raw, dict):
                        record_onebot_api_message(
                            self.ledger,
                            self.scope,
                            raw,
                            bot_native_user_id=self.scope.bot_native_user_id,
                        )
        matches = self.ledger.search_in_scope(
            self.scope,
            query,
            limit,
        )
        return _json_result(
            ok=True,
            query=query,
            messages=[
                self._canonical_message_payload(message, include_files=False)
                for message in matches
            ],
        )

    async def _sandbox_create(
        self,
        arguments: dict[str, object],
    ) -> str:
        runtime = str(arguments.get("runtime", "python"))
        sandbox = await self.sandbox_manager.create(self.owner, runtime)
        return _json_result(ok=True, sandbox=sandbox)

    async def _sandbox_list(
        self,
        arguments: dict[str, object],
    ) -> str:
        del arguments
        sandboxes = await self.sandbox_manager.list(self.owner)
        return _json_result(ok=True, sandboxes=sandboxes)

    async def _sandbox_destroy(
        self,
        arguments: dict[str, object],
    ) -> str:
        sandbox_id = str(arguments.get("sandbox_id", ""))
        await self.sandbox_manager.destroy(self.owner, sandbox_id)
        return _json_result(ok=True, sandbox_id=sandbox_id, status="destroyed")

    async def _sandbox_exec(
        self,
        arguments: dict[str, object],
    ) -> str:
        sandbox_id = str(arguments.get("sandbox_id", ""))
        command = str(arguments.get("command", ""))
        raw_timeout = arguments.get("timeout_seconds")
        timeout = int(raw_timeout) if raw_timeout is not None else None
        result = await self.sandbox_manager.exec(
            self.owner,
            sandbox_id,
            command,
            timeout,
        )
        return _json_result(
            ok=result.returncode == 0,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            observed_manifest=(
                asdict(result.manifest)
                if result.manifest is not None
                else None
            ),
        )

    async def _sandbox_write_file(
        self,
        arguments: dict[str, object],
    ) -> str:
        sandbox_id = str(arguments.get("sandbox_id", ""))
        path = str(arguments.get("path", ""))
        content = str(arguments.get("content", "")).encode("utf-8")
        size = await self.sandbox_manager.write_file(
            self.owner,
            sandbox_id,
            path,
            content,
        )
        return _json_result(ok=True, path=path, bytes_written=size)

    async def _sandbox_read_file(
        self,
        arguments: dict[str, object],
    ) -> str:
        sandbox_id = str(arguments.get("sandbox_id", ""))
        path = str(arguments.get("path", ""))
        content = await self.sandbox_manager.read_file(
            self.owner,
            sandbox_id,
            path,
            max_bytes=64 * 1024,
        )
        return _json_result(
            ok=True,
            path=path,
            content=content.decode("utf-8", errors="replace"),
        )

    async def _send_file_from_sandbox(
        self,
        arguments: dict[str, object],
    ) -> str:
        sandbox_id = str(arguments.get("sandbox_id", ""))
        path = str(arguments.get("path", ""))
        requested_name = str(arguments.get("filename", "")).strip()
        filename = self._safe_filename(
            requested_name or PurePosixPath(path).name
        )
        content = await self.sandbox_manager.read_file(
            self.owner,
            sandbox_id,
            path,
            max_bytes=self.max_file_bytes,
        )
        response = await self.bot.call_api(
            "upload_group_file",
            group_id=self.event.group_id,
            file="base64://" + base64.b64encode(content).decode("ascii"),
            name=filename,
        )
        return _json_result(
            ok=True,
            filename=filename,
            size=len(content),
            uploaded=bool(response is not False),
        )

    async def _send_image_from_sandbox(
        self,
        arguments: dict[str, object],
    ) -> str:
        sandbox_id = str(arguments.get("sandbox_id", ""))
        path = str(arguments.get("path", ""))
        if PurePosixPath(path).suffix.lower() not in IMAGE_SUFFIXES:
            return _json_result(ok=False, error="只允许发送常见图片格式。")
        content = await self.sandbox_manager.read_file(
            self.owner,
            sandbox_id,
            path,
            max_bytes=min(self.max_file_bytes, 10 * 1024 * 1024),
        )
        response = await self.bot.send_group_msg(
            group_id=self.event.group_id,
            message=MessageSegment.image(content),
        )
        canonical_message_id = None
        if self.canonical_messages_enabled and isinstance(response, dict):
            native_message_id = response.get("message_id")
            if native_message_id:
                assert self.ledger is not None
                assert self.scope is not None
                stored = self.ledger.record_message(
                    self.scope,
                    native_message_id=str(native_message_id),
                    sender_native_user_id=self.scope.bot_native_user_id,
                    sender_display="机器人",
                    body=MessageBody(
                        (
                            MediaNode(
                                0,
                                "image",
                                source=f"sandbox:{path}",
                                name=PurePosixPath(path).name,
                                source_type="image",
                            ),
                        )
                    ),
                    occurred_at=int(time.time()),
                    direction="outbound",
                )
                canonical_message_id = stored.canonical_message_id
                self._link_turn_send(canonical_message_id, "send_image")
        return _json_result(
            ok=True,
            size=len(content),
            message_handle=(
                f"msg#{canonical_message_id}"
                if canonical_message_id is not None
                else None
            ),
        )

    async def _list_recent_files(
        self,
        arguments: dict[str, object],
    ) -> str:
        limit = min(max(int(arguments.get("limit") or 20), 1), 50)
        files = await self._group_files(limit)
        return _json_result(
            ok=True,
            files=[self._public_group_file(item) for item in files[:limit]],
        )

    async def _import_file_to_sandbox(
        self,
        arguments: dict[str, object],
    ) -> str:
        sandbox_id = str(arguments.get("sandbox_id", ""))
        file_handle = str(arguments.get("file_handle", "")).strip()
        attachment_handle = str(arguments.get("attachment_handle", "")).strip()
        raw_message_handle = arguments.get("message_handle")
        message_id = _parse_message_handle(raw_message_handle)
        destination = str(arguments.get("destination", "")).strip()
        matched_file: dict[str, Any] | None = None
        native_file_id = ""

        if raw_message_handle is not None and message_id is None:
            return _json_result(
                ok=False,
                error="message_handle 格式无效。",
            )

        if message_id is not None:
            if not self.canonical_messages_enabled:
                return _json_result(
                    ok=False,
                    error="规范消息账本不可用，已拒绝原生 ID 降级读取。",
                )
            assert self.ledger is not None
            assert self.scope is not None
            canonical = self.ledger.get_in_scope(self.scope, message_id)
            if canonical is None or not canonical.native_message_id:
                return _json_result(
                    ok=False,
                    error="当前群可见上下文中找不到这条附件消息。",
                )
            native_message_id = int(canonical.native_message_id)

            canonical_attachments = self._canonical_attachment_records(
                canonical
            )
            if attachment_handle:
                selected = next(
                    (
                        item
                        for item in canonical_attachments
                        if item["handle"] == attachment_handle
                    ),
                    None,
                )
                if selected is None:
                    return _json_result(
                        ok=False,
                        error="当前消息中找不到这个规范附件句柄。",
                    )
                native_file_id = str(selected["_native_file_id"])
            elif len(canonical_attachments) == 1:
                native_file_id = str(
                    canonical_attachments[0]["_native_file_id"]
                )
            elif len(canonical_attachments) > 1:
                return _json_result(
                    ok=False,
                    error="这条消息有多个附件，请提供目标 attachment_handle。",
                )

            message = await self.bot.get_msg(message_id=native_message_id)
            if int(message.get("group_id") or 0) != self.event.group_id:
                return _json_result(ok=False, error="附件消息不属于当前群。")
            attachments = _message_attachments(
                message.get("message") or message.get("raw_message")
            )
            if native_file_id:
                matched_file = next(
                    (
                        item
                        for item in attachments
                        if item["file_id"] == native_file_id
                    ),
                    None,
                )
            elif len(attachments) == 1:
                matched_file = attachments[0]
                native_file_id = matched_file["file_id"]
            elif len(attachments) > 1:
                return _json_result(
                    ok=False,
                    error="这条消息有多个附件，请提供目标 attachment_handle。",
                )
            if matched_file is None:
                return _json_result(
                    ok=False,
                    error="被回复消息中没有找到这个附件。",
                )
            if not native_file_id:
                return _json_result(
                    ok=False,
                    error="这个附件没有可下载的宿主映射。",
                )
        else:
            if not file_handle:
                return _json_result(
                    ok=False,
                    error="必须提供 groupfile# 句柄或附件消息的 msg# 句柄。",
                )
            group_files = await self._group_files(50)
            matched_file = next(
                (item for item in group_files if item["handle"] == file_handle),
                None,
            )
            if matched_file is None:
                return _json_result(
                    ok=False,
                    error="当前群最近文件中没有这个 groupfile# 句柄。",
                )
            native_file_id = str(matched_file["_native_file_id"])

        if matched_file["file_size"] > self.max_file_bytes:
            return _json_result(ok=False, error="群文件超过导入大小上限。")

        response = await self.bot.call_api(
            "get_file",
            file_id=native_file_id,
        )
        if not isinstance(response, dict):
            return _json_result(ok=False, error="NapCat 没有返回可下载文件。")
        content = await self._read_napcat_file(response)
        await self.sandbox_manager.write_file(
            self.owner,
            sandbox_id,
            destination,
            content,
            allow_large=True,
        )
        return _json_result(
            ok=True,
            source_name=(
                matched_file["file_name"]
                or str(response.get("file_name") or "")
            ),
            destination=destination,
            size=len(content),
        )

    @staticmethod
    def _canonical_message_payload(
        message: CanonicalMessage,
        *,
        include_files: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "handle": f"msg#{message.canonical_message_id}",
            "sender": (
                f"@#{message.sender_principal_id} {message.sender_display}"
                if message.sender_principal_id is not None
                else message.sender_display
            ),
            "time": message.occurred_at,
            "text": message.rendered_text[:1000],
            "reply_to": (
                f"msg#{message.reply_to_canonical_message_id}"
                if message.reply_to_canonical_message_id is not None
                else None
            ),
        }
        if include_files:
            payload["attachments"] = render_api_attachments(
                message.body,
                message.canonical_message_id,
            )
        return payload

    async def _say(
        self,
        arguments: dict[str, object],
    ) -> str:
        text = str(arguments.get("text", "")).strip()
        if not text:
            return _json_result(ok=False, error="进度消息不能为空。")

        response = await self.bot.send_group_msg(
            group_id=self.event.group_id,
            message=MessageSegment.text(text[:200]),
        )
        canonical_message_id = None
        if self.canonical_messages_enabled and isinstance(response, dict):
            native_message_id = response.get("message_id")
            if native_message_id:
                assert self.ledger is not None
                assert self.scope is not None
                stored = self.ledger.record_message(
                    self.scope,
                    native_message_id=str(native_message_id),
                    sender_native_user_id=self.scope.bot_native_user_id,
                    sender_display="机器人",
                    body=MessageBody((TextNode(0, text[:200]),)),
                    occurred_at=int(time.time()),
                    direction="outbound",
                )
                canonical_message_id = stored.canonical_message_id
                self._link_turn_send(canonical_message_id, "say")
        return _json_result(
            ok=True,
            message_handle=(
                f"msg#{canonical_message_id}"
                if canonical_message_id is not None
                else None
            ),
        )

    async def _group_files(self, limit: int) -> list[dict[str, Any]]:
        response = await self.bot.call_api(
            "get_group_root_files",
            group_id=self.event.group_id,
            file_count=limit,
        )
        if isinstance(response, dict):
            raw_files = response.get("files", [])
        elif isinstance(response, list):
            raw_files = response
        else:
            raw_files = []
        if not isinstance(raw_files, list):
            return []
        files = [
            _safe_file_info(item)
            for item in raw_files
            if isinstance(item, dict) and item.get("file_id")
        ]
        for file_info in files:
            file_info["handle"] = self._group_file_handle(
                str(file_info["_native_file_id"])
            )
        if self.canonical_messages_enabled:
            assert self.ledger is not None
            for file_info in files:
                native_uploader = file_info.pop("uploader", 0)
                file_info["uploader"] = (
                    self.ledger.principal_label_for_native(
                        "onebot-v11",
                        native_uploader,
                    )
                    or "未知群成员"
                )
        else:
            for file_info in files:
                file_info["uploader"] = "未知群成员"
        return files

    def _group_file_handle(self, native_file_id: str) -> str:
        scope_key = (
            self.scope.key
            if self.scope is not None
            else f"onebot-v11:group:{self.event.group_id}"
        )
        digest = hashlib.sha256(
            f"{scope_key}\0{native_file_id}".encode("utf-8")
        ).hexdigest()[:20]
        return f"groupfile#{digest}"

    @staticmethod
    def _public_group_file(file_info: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in file_info.items()
            if not key.startswith("_")
        }

    @staticmethod
    def _canonical_attachment_records(
        message: CanonicalMessage,
    ) -> list[dict[str, str]]:
        records = []
        for node in message.body.nodes:
            if not isinstance(node, MediaNode) or node.media_kind != "file":
                continue
            records.append(
                {
                    "handle": (
                        f"file#{message.canonical_message_id}."
                        f"{node.segment_index}"
                    ),
                    "_native_file_id": str(
                        node.raw_data.get("file_id") or node.source or ""
                    ),
                }
            )
        return records

    async def _read_napcat_file(self, response: dict[str, Any]) -> bytes:
        encoded = response.get("base64")
        if isinstance(encoded, str) and encoded:
            prefix = "base64://"
            if encoded.startswith(prefix):
                encoded = encoded[len(prefix):]
            content = base64.b64decode(encoded, validate=True)
            return self._check_download_size(content)

        local_file = response.get("file")
        if isinstance(local_file, str) and local_file:
            path = Path(local_file)
            size = await asyncio.to_thread(path.stat)
            if size.st_size > self.max_file_bytes:
                raise ValueError("文件超过导入大小上限。")
            content = await asyncio.to_thread(path.read_bytes)
            return self._check_download_size(content)

        url = response.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            async with httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
            ) as client:
                async with client.stream("GET", url) as download:
                    download.raise_for_status()
                    content = bytearray()
                    async for chunk in download.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > self.max_file_bytes:
                            raise ValueError("文件超过导入大小上限。")
                    return bytes(content)

        raise ValueError("NapCat 没有提供可读取的文件内容。")

    def _check_download_size(self, content: bytes) -> bytes:
        if len(content) > self.max_file_bytes:
            raise ValueError("文件超过导入大小上限。")
        return content

    def _link_turn_send(
        self,
        canonical_message_id: int,
        node_id: str,
    ) -> None:
        if self.turn_journal is None or self.turn_id is None:
            return
        try:
            self.turn_journal.link_send(
                self.turn_id,
                canonical_message_id,
                node_id=node_id,
            )
        except sqlite3.Error as exc:
            logger.warning(f"Could not link tool send to turn: {exc}")

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = Path(filename).name.strip()
        if not name or name in {".", ".."}:
            raise ValueError("文件名无效。")
        return name[:180]
