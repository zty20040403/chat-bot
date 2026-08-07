from __future__ import annotations

from typing import Any, Union

ToolDefinition = dict[str, Any]
ToolChoice = Union[str, dict[str, Any]]

WEB_SEARCH_TOOL_NAME = "web_search"
READ_IMAGE_TEXT_TOOL_NAME = "read_image_text"
TRANSCRIBE_VOICE_TOOL_NAME = "transcribe_voice"
REPLY_WITH_VOICE_TOOL_NAME = "reply_with_voice"
SEND_STICKER_TOOL_NAME = "send_sticker"
SEND_QQ_FACE_TOOL_NAME = "send_qq_face"
GET_MESSAGE_BY_ID_TOOL_NAME = "get_message_by_id"
SEARCH_MESSAGES_TOOL_NAME = "search_messages"
SANDBOX_CREATE_TOOL_NAME = "sandbox_create"
SANDBOX_LIST_TOOL_NAME = "sandbox_list"
SANDBOX_DESTROY_TOOL_NAME = "sandbox_destroy"
SANDBOX_EXEC_TOOL_NAME = "sandbox_exec"
SANDBOX_WRITE_FILE_TOOL_NAME = "sandbox_write_file"
SANDBOX_READ_FILE_TOOL_NAME = "sandbox_read_file"
SEND_FILE_FROM_SANDBOX_TOOL_NAME = "send_file_from_sandbox"
SEND_IMAGE_FROM_SANDBOX_TOOL_NAME = "send_image_from_sandbox"
LIST_RECENT_FILES_TOOL_NAME = "list_recent_files"
IMPORT_FILE_TO_SANDBOX_TOOL_NAME = "import_file_to_sandbox"
SAY_TOOL_NAME = "say"
MEMORY_ADD_TOOL_NAME = "memory_add"
MEMORY_LIST_TOOL_NAME = "memory_list"
MEMORY_REMOVE_TOOL_NAME = "memory_remove"
CONTEXT_EXPAND_TOOL_NAME = "context_expand"
CONTEXT_SEARCH_TOOL_NAME = "context_search"

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

SEND_STICKER_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": SEND_STICKER_TOOL_NAME,
        "description": (
            "发送一张机器人已学习或本地保存的 QQ 图片表情包。"
            "仅当用户明确要求发送表情包、贴纸或 meme 时调用；"
            "普通回答不要为了装饰而调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}

SEND_QQ_FACE_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": SEND_QQ_FACE_TOOL_NAME,
        "description": (
            "发送一个 QQ 自带的小黄脸表情。仅当用户明确要求 QQ 自带表情、"
            "小黄脸或指定表情名称时调用；用户说普通的“表情包”时使用 send_sticker。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": (
                        "表情名称或 QQ 表情 ID，例如“微笑”“可爱”“疑问”“65”；"
                        "没有指定时传“随机”。"
                    ),
                }
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
    },
}

GET_MESSAGE_BY_ID_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": GET_MESSAGE_BY_ID_TOOL_NAME,
        "description": (
            "读取当前群聊中指定规范 msg# 句柄的消息原文和附件名称。"
            "句柄必须完整照抄当前会话上下文或搜索结果。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message_handle": {
                    "type": "string",
                    "pattern": "^msg#[1-9][0-9]*$",
                    "description": "完整规范句柄，例如 msg#42。",
                }
            },
            "required": ["message_handle"],
            "additionalProperties": False,
        },
    },
}

SEARCH_MESSAGES_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": SEARCH_MESSAGES_TOOL_NAME,
        "description": "在当前群最近的聊天记录中搜索包含指定关键词的消息。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要搜索的文字片段。"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "最多返回多少条匹配消息。",
                },
            },
            "required": ["query", "limit"],
            "additionalProperties": False,
        },
    },
}

SANDBOX_CREATE_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": SANDBOX_CREATE_TOOL_NAME,
        "description": (
            "创建隔离的 Docker 开发沙盒。需要写代码、安装依赖、构建或测试项目时先调用。"
            "工作目录固定为 /workspace。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "runtime": {
                    "type": "string",
                    "enum": ["python", "node", "debian"],
                    "description": "项目所需运行环境。",
                }
            },
            "required": ["runtime"],
            "additionalProperties": False,
        },
    },
}

SANDBOX_LIST_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": SANDBOX_LIST_TOOL_NAME,
        "description": "列出当前用户拥有的 Docker 沙盒及状态。",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}

SANDBOX_DESTROY_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": SANDBOX_DESTROY_TOOL_NAME,
        "description": "销毁不再需要的沙盒，释放 CPU、内存和磁盘。",
        "parameters": {
            "type": "object",
            "properties": {
                "sandbox_id": {"type": "string", "description": "例如 s1a2b3c。"}
            },
            "required": ["sandbox_id"],
            "additionalProperties": False,
        },
    },
}

SANDBOX_EXEC_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": SANDBOX_EXEC_TOOL_NAME,
        "description": (
            "在指定沙盒的 /workspace 中执行 shell 命令，适合安装依赖、构建、测试、"
            "运行程序和打包文件。不要用于读写宿主机。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sandbox_id": {"type": "string"},
                "command": {"type": "string", "description": "要执行的 shell 命令。"},
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 300,
                },
            },
            "required": ["sandbox_id", "command"],
            "additionalProperties": False,
        },
    },
}

SANDBOX_WRITE_FILE_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": SANDBOX_WRITE_FILE_TOOL_NAME,
        "description": "向沙盒 /workspace 下写入 UTF-8 文本文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "sandbox_id": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "相对于 /workspace 的路径，例如 src/main.py。",
                },
                "content": {"type": "string", "description": "完整文件内容。"},
            },
            "required": ["sandbox_id", "path", "content"],
            "additionalProperties": False,
        },
    },
}

SANDBOX_READ_FILE_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": SANDBOX_READ_FILE_TOOL_NAME,
        "description": "读取沙盒 /workspace 下的 UTF-8 文本文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "sandbox_id": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["sandbox_id", "path"],
            "additionalProperties": False,
        },
    },
}

SEND_FILE_FROM_SANDBOX_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": SEND_FILE_FROM_SANDBOX_TOOL_NAME,
        "description": (
            "把沙盒中的构建产物、源码压缩包或文档作为 QQ 群文件发送。"
            "发送前应先完成构建或打包。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sandbox_id": {"type": "string"},
                "path": {"type": "string"},
                "filename": {
                    "type": "string",
                    "description": "群里显示的文件名；留空则使用路径文件名。",
                },
            },
            "required": ["sandbox_id", "path"],
            "additionalProperties": False,
        },
    },
}

SEND_IMAGE_FROM_SANDBOX_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": SEND_IMAGE_FROM_SANDBOX_TOOL_NAME,
        "description": "把沙盒中的 PNG/JPG/GIF/WebP 图片直接发送到当前 QQ 群。",
        "parameters": {
            "type": "object",
            "properties": {
                "sandbox_id": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["sandbox_id", "path"],
            "additionalProperties": False,
        },
    },
}

LIST_RECENT_FILES_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": LIST_RECENT_FILES_TOOL_NAME,
        "description": "列出当前 QQ 群最近上传的文件及规范 groupfile# 句柄。",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                }
            },
            "required": ["limit"],
            "additionalProperties": False,
        },
    },
}

IMPORT_FILE_TO_SANDBOX_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": IMPORT_FILE_TO_SANDBOX_TOOL_NAME,
        "description": (
            "把当前群文件或被回复消息中的附件下载并导入指定沙盒。"
            "处理用户所说的“这个文件”时传入被回复消息的完整 msg# 句柄；"
            "如果该消息只有一个附件，可以省略 attachment_handle。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sandbox_id": {"type": "string"},
                "file_handle": {
                    "type": "string",
                    "pattern": "^groupfile#[0-9a-f]{20}$",
                    "description": "list_recent_files 返回的完整 groupfile# 句柄。",
                },
                "attachment_handle": {
                    "type": "string",
                    "pattern": "^file#[1-9][0-9]*\\.[0-9]+$",
                    "description": "消息工具返回的完整 file#消息.段序号句柄。",
                },
                "message_handle": {
                    "type": "string",
                    "pattern": "^msg#[1-9][0-9]*$",
                    "description": "包含目标附件的完整规范句柄，例如 msg#42。",
                },
                "destination": {
                    "type": "string",
                    "description": "沙盒中的目标相对路径，例如 input/data.zip。",
                },
            },
            "required": ["sandbox_id", "destination"],
            "additionalProperties": False,
        },
    },
}

SAY_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": SAY_TOOL_NAME,
        "description": (
            "长任务中向当前群发送一条简短进度通知。"
            "在下载、安装、编写、构建、测试和打包等关键阶段主动调用，"
            "有新的实际进展时可以多次使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "不超过 200 字的自然进度消息。",
                }
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
}

MEMORY_ADD_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": MEMORY_ADD_TOOL_NAME,
        "description": (
            "保存一条以后仍然有用的长期记忆。仅当用户明确要求记住，或用户清楚表达了"
            "稳定偏好、身份事实、长期项目约定时调用；不要保存临时聊天、推测、密码、"
            "API Key、验证码或其他秘密。默认保存为当前用户记忆；只有对整个群都适用的"
            "共同约定才使用 group。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["user", "group"],
                    "description": "记忆范围：当前用户或当前群。",
                },
                "content": {
                    "type": "string",
                    "description": "独立、简洁、以后可直接理解的一条事实或偏好。",
                },
            },
            "required": ["scope", "content"],
            "additionalProperties": False,
        },
    },
}

MEMORY_LIST_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": MEMORY_LIST_TOOL_NAME,
        "description": "查看当前用户或当前群已保存的长期记忆及其 ID。",
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["user", "group", "all"],
                    "description": "要查看的记忆范围。",
                }
            },
            "required": ["scope"],
            "additionalProperties": False,
        },
    },
}

MEMORY_REMOVE_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": MEMORY_REMOVE_TOOL_NAME,
        "description": (
            "按 ID 删除当前用户或当前群可见的一条长期记忆。"
            "仅在用户明确要求忘记或更正该记忆时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "memory_list 返回的记忆 ID。",
                }
            },
            "required": ["memory_id"],
            "additionalProperties": False,
        },
    },
}

CONTEXT_EXPAND_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": CONTEXT_EXPAND_TOOL_NAME,
        "description": (
            "展开当前会话中的工作回合 t#，或历史摘要 episode#。"
            "用户说继续、修改、复用或追问旧任务且细节不足时调用。"
            "只能读取当前会话可见的句柄。优先传 target。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "完整句柄，例如 t#12 或 episode#550e8400-e29b-41d4-a716-446655440000。",
                },
                "turn_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "兼容字段：t# 后面的数字，例如 t#12 传 12。",
                }
            },
            "additionalProperties": False,
        },
    },
}

CONTEXT_SEARCH_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": CONTEXT_SEARCH_TOOL_NAME,
        "description": (
            "在当前群或当前私聊的规范消息与历史 episode 摘要中检索。"
            "用户询问以前聊过什么、谁提到某事、旧决定或旧任务时调用。"
            "返回的 msg# 和 episode# 仍只能在当前会话使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": "需要查找的关键词或短语。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "最多返回多少条，默认 5。",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

MEMORY_TOOLS = [MEMORY_ADD_TOOL, MEMORY_LIST_TOOL, MEMORY_REMOVE_TOOL]

AGENT_TOOLS = [
    GET_MESSAGE_BY_ID_TOOL,
    SEARCH_MESSAGES_TOOL,
    SANDBOX_CREATE_TOOL,
    SANDBOX_LIST_TOOL,
    SANDBOX_DESTROY_TOOL,
    SANDBOX_EXEC_TOOL,
    SANDBOX_WRITE_FILE_TOOL,
    SANDBOX_READ_FILE_TOOL,
    SEND_FILE_FROM_SANDBOX_TOOL,
    SEND_IMAGE_FROM_SANDBOX_TOOL,
    LIST_RECENT_FILES_TOOL,
    IMPORT_FILE_TO_SANDBOX_TOOL,
    SAY_TOOL,
]


def available_tools(
    *,
    include_web_search: bool,
    include_image_ocr: bool,
    include_voice_transcription: bool = False,
    include_voice_reply: bool = False,
    include_stickers: bool = False,
    include_memory_tools: bool = False,
    include_agent_tools: bool = False,
    include_turn_tools: bool = False,
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
    if include_stickers:
        tools.extend([SEND_STICKER_TOOL, SEND_QQ_FACE_TOOL])
    if include_memory_tools:
        tools.extend(MEMORY_TOOLS)
    if include_agent_tools:
        tools.extend(AGENT_TOOLS)
    if include_turn_tools:
        tools.extend([CONTEXT_EXPAND_TOOL, CONTEXT_SEARCH_TOOL])
    return tools


def force_tool(name: str) -> ToolChoice:
    return {
        "type": "function",
        "function": {"name": name},
    }
