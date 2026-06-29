from __future__ import annotations

from typing import Any, Union

ToolDefinition = dict[str, Any]
ToolChoice = Union[str, dict[str, Any]]

WEB_SEARCH_TOOL_NAME = "web_search"
READ_IMAGE_TEXT_TOOL_NAME = "read_image_text"
TRANSCRIBE_VOICE_TOOL_NAME = "transcribe_voice"
REPLY_WITH_VOICE_TOOL_NAME = "reply_with_voice"

WEB_SEARCH_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": WEB_SEARCH_TOOL_NAME,
        "description": (
            "搜索互联网。遇到最新消息、新闻、价格、天气、版本、官网、"
            "用户明确要求联网核实时调用；普通常识且无需更新时不要调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "适合搜索引擎的简洁关键词，保留必要的人名、日期和限定词。",
                },
                "freshness": {
                    "type": "string",
                    "enum": ["auto", "day", "week", "month", "year"],
                    "description": (
                        "时间范围。实时内容用 day，最新新闻通常用 week，"
                        "不确定时用 auto。"
                    ),
                },
            },
            "required": ["query", "freshness"],
            "additionalProperties": False,
        },
    },
}

READ_IMAGE_TEXT_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": READ_IMAGE_TEXT_TOOL_NAME,
        "description": (
            "读取用户本轮图片、回复的图片，或该用户最近发送图片中的文字。"
            "用户要求看图、识图、读取截图、总结截图内容时调用。"
            "它只能读取文字，不能理解没有文字的纯画面。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}

TRANSCRIBE_VOICE_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": TRANSCRIBE_VOICE_TOOL_NAME,
        "description": (
            "把用户当前、回复或最近发送的 QQ 语音转成文字。"
            "用户要求听语音、语音识别、转文字或根据语音内容回答时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}

REPLY_WITH_VOICE_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": REPLY_WITH_VOICE_TOOL_NAME,
        "description": (
            "把完整的最终回答合成为 QQ 语音。"
            "只有用户明确要求语音回答、发语音、念出来或读出来时才调用；"
            "text 必须是可以直接朗读的简洁最终回答，使用自然口语短句和标点停顿，"
            "不要使用书面报告语气、Markdown、编号、项目符号或链接。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要朗读的完整中文口语回答，像群友直接说话。",
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
}


def available_tools(
    *,
    include_web_search: bool,
    include_image_ocr: bool,
    include_voice_transcription: bool = False,
    include_voice_reply: bool = False,
) -> list[ToolDefinition]:
    tools: list[ToolDefinition] = []
    if include_web_search:
        tools.append(WEB_SEARCH_TOOL)
    if include_image_ocr:
        tools.append(READ_IMAGE_TEXT_TOOL)
    if include_voice_transcription:
        tools.append(TRANSCRIBE_VOICE_TOOL)
    if include_voice_reply:
        tools.append(REPLY_WITH_VOICE_TOOL)
    return tools


def force_tool(name: str) -> ToolChoice:
    return {
        "type": "function",
        "function": {"name": name},
    }
