from __future__ import annotations

from typing import Any, Union

ToolDefinition = dict[str, Any]
ToolChoice = Union[str, dict[str, Any]]

WEB_SEARCH_TOOL_NAME = "web_search"
READ_IMAGE_TEXT_TOOL_NAME = "read_image_text"
VIEW_IMAGE_TOOL_NAME = "view_image"
FIND_IMAGES_TOOL_NAME = "find_images"
FIND_STICKERS_TOOL_NAME = "find_stickers"
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
PIN_MESSAGE_TOOL_NAME = "pin_message"
UNPIN_MESSAGE_TOOL_NAME = "unpin_message"
USE_SKILL_TOOL_NAME = "use_skill"
INSPECT_SOURCE_TOOL_NAME = "inspect_source"
GROUP_MEMBERS_TOOL_NAME = "group_members"
REMINDER_SET_TOOL_NAME = "reminder_set"
REMINDER_LIST_TOOL_NAME = "reminder_list"
REMINDER_CANCEL_TOOL_NAME = "reminder_cancel"
VIEW_FORWARD_TOOL_NAME = "view_forward"
VIEW_BILIBILI_TOOL_NAME = "view_bilibili"
BROWSER_NAVIGATE_TOOL_NAME = "browser_navigate"
BROWSER_SNAPSHOT_TOOL_NAME = "browser_snapshot"
BROWSER_CLICK_TOOL_NAME = "browser_click"
BROWSER_TYPE_TOOL_NAME = "browser_type"
BROWSER_PRESS_KEY_TOOL_NAME = "browser_press_key"
BROWSER_WAIT_FOR_TOOL_NAME = "browser_wait_for"
BROWSER_SCROLL_TOOL_NAME = "browser_scroll"
BROWSER_CLOSE_TOOL_NAME = "browser_close"
BROWSER_CLEAR_TOOL_NAME = "browser_clear"

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

VIEW_IMAGE_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": VIEW_IMAGE_TOOL_NAME,
        "description": (
            "理解当前消息或当前群一条指定消息中的完整图片画面，不只读取文字。"
            "用户要求看图、分析截图、解释表情包或询问图片内容时调用。"
            "未提供 message_handle 时查看当前触发消息；可提供 msg# 句柄查看当前群历史图片。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message_handle": {
                    "type": "string",
                    "pattern": "^msg#[1-9][0-9]*$",
                },
                "segment_index": {
                    "type": "integer",
                    "minimum": 0,
                },
            },
            "additionalProperties": False,
        },
    },
}

FIND_IMAGES_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": FIND_IMAGES_TOOL_NAME,
        "description": "按画面、文字、情绪或用途搜索当前群已经识别的历史图片。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

FIND_STICKERS_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": FIND_STICKERS_TOOL_NAME,
        "description": (
            "按当前语境、情绪或用途搜索当前群安全可发送的表情包。"
            "先调用它取得 media# 句柄，再调用 send_sticker 发送选中的一张。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
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
            "发送 find_stickers 返回的一张安全表情包。"
            "media_handle 必须是当前群可见的 media# 句柄。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "media_handle": {
                    "type": "string",
                    "pattern": "^media#[1-9][0-9]*$",
                }
            },
            "required": ["media_handle"],
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
            "有新的实际进展时可以多次使用。识图、搜索、普通问答等短任务"
            "不要调用，也不要发送‘正在处理’之类没有实际结果的占位消息。"
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
            "统一检索当前会话的规范消息、固定消息、长期记忆与 episode 摘要。"
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

PIN_MESSAGE_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": PIN_MESSAGE_TOOL_NAME,
        "description": (
            "把当前会话的一条 msg# 长期固定到上下文。适合重要决定、约定、"
            "项目状态或用户明确要求保留的消息；固定消息不会被 /clear 删除。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message_handle": {
                    "type": "string",
                    "pattern": "^msg#[1-9][0-9]*$",
                    "description": "当前会话中的完整 msg# 句柄。",
                }
            },
            "required": ["message_handle"],
            "additionalProperties": False,
        },
    },
}

UNPIN_MESSAGE_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": UNPIN_MESSAGE_TOOL_NAME,
        "description": "取消当前会话中过时或不再需要的一条固定消息。",
        "parameters": {
            "type": "object",
            "properties": {
                "message_handle": {
                    "type": "string",
                    "pattern": "^msg#[1-9][0-9]*$",
                }
            },
            "required": ["message_handle"],
            "additionalProperties": False,
        },
    },
}

USE_SKILL_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": USE_SKILL_TOOL_NAME,
        "description": "读取技能目录中某项工作的完整宿主流程，开始对应任务前调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 80,
                    "description": "技能目录中的名称，例如 sandbox。",
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
}

INSPECT_SOURCE_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": INSPECT_SOURCE_TOOL_NAME,
        "description": (
            "只读检查机器人随仓库发布的白名单源码。回答自身实现、架构、"
            "命令或默认配置问题时用于取证；不能读取 .env、状态数据或宿主机任意路径。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "search", "read", "identity"],
                },
                "path": {"type": "string", "maxLength": 300},
                "query": {"type": "string", "maxLength": 200},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
}

GROUP_MEMBERS_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": GROUP_MEMBERS_TOOL_NAME,
        "description": (
            "按需读取当前 QQ 群成员名单。平时使用上下文中的精简成员记录；"
            "只有需要确认成员、群名片、角色或搜索某人时调用。返回的 principal "
            "是可直接照抄到最终回答中的 [mention#编号] 句柄，不是 QQ 号。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "maxLength": 100,
                    "description": "可选的昵称或群名片子串。",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
}

VIEW_FORWARD_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": VIEW_FORWARD_TOOL_NAME,
        "description": (
            "展开当前群里一条合并转发消息。传上下文中的 msg# 规范句柄，"
            "返回子消息的发送者、时间和正文；嵌套转发可以继续展开。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message_handle": {
                    "type": "string",
                    "pattern": "^msg#[1-9][0-9]*$",
                }
            },
            "required": ["message_handle"],
            "additionalProperties": False,
        },
    },
}

VIEW_BILIBILI_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": VIEW_BILIBILI_TOOL_NAME,
        "description": (
            "读取 B站视频的标题、UP主、简介、时长、播放互动数据和热评。"
            "接受 BV号、av链接、完整视频链接或 b23.tv 短链。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "maxLength": 1000},
                "comment_count": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 20,
                    "default": 10,
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
}

BROWSER_NAVIGATE_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": BROWSER_NAVIGATE_TOOL_NAME,
        "description": "在当前会话的持久浏览器里打开一个公开 http/https 页面并返回可见文字。",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "maxLength": 2000}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
}

BROWSER_SNAPSHOT_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": BROWSER_SNAPSHOT_TOOL_NAME,
        "description": "读取当前浏览器页面的可见文字和可交互元素引用。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}

BROWSER_CLICK_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": BROWSER_CLICK_TOOL_NAME,
        "description": "点击 browser_snapshot 返回的元素引用，例如 b3。",
        "parameters": {
            "type": "object",
            "properties": {"ref": {"type": "string", "pattern": "^b[1-9][0-9]*$"}},
            "required": ["ref"],
            "additionalProperties": False,
        },
    },
}

BROWSER_TYPE_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": BROWSER_TYPE_TOOL_NAME,
        "description": "向浏览器输入框元素填写文字，可选择按 Enter 提交。",
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "pattern": "^b[1-9][0-9]*$"},
                "text": {"type": "string", "maxLength": 10000},
                "submit": {"type": "boolean", "default": False},
            },
            "required": ["ref", "text"],
            "additionalProperties": False,
        },
    },
}

BROWSER_PRESS_KEY_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": BROWSER_PRESS_KEY_TOOL_NAME,
        "description": "在浏览器中按一个导航键，例如 Enter、Escape、Tab 或 PageDown。",
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string", "maxLength": 30}},
            "required": ["key"],
            "additionalProperties": False,
        },
    },
}

BROWSER_WAIT_FOR_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": BROWSER_WAIT_FOR_TOOL_NAME,
        "description": "等待页面出现指定文字，再返回新快照。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "maxLength": 500},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 60},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
}

BROWSER_SCROLL_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": BROWSER_SCROLL_TOOL_NAME,
        "description": "滚动当前浏览器页面；正数向下，负数向上。",
        "parameters": {
            "type": "object",
            "properties": {"amount": {"type": "integer", "minimum": -5000, "maximum": 5000}},
            "required": ["amount"],
            "additionalProperties": False,
        },
    },
}

BROWSER_CLOSE_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": BROWSER_CLOSE_TOOL_NAME,
        "description": "关闭当前会话浏览器，释放内存；登录资料仍保留在持久目录。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}

BROWSER_CLEAR_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": BROWSER_CLEAR_TOOL_NAME,
        "description": "关闭浏览器并删除当前发起者自己的 cookie、缓存和持久登录资料。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}

REMINDER_SET_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": REMINDER_SET_TOOL_NAME,
        "description": (
            "创建重启后仍保留的提醒。用户明确要求稍后、明天或某个时间提醒时调用；"
            "due_at 必须先按 Asia/Shanghai 当前日期换算成绝对时间。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "due_at": {
                    "type": "string",
                    "minLength": 10,
                    "maxLength": 40,
                    "description": "带时区 ISO 8601，例如 2026-08-10T09:00:00+08:00。",
                },
                "message": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1000,
                    "description": "到点后发送的独立可读提醒内容。",
                },
            },
            "required": ["due_at", "message"],
            "additionalProperties": False,
        },
    },
}

REMINDER_LIST_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": REMINDER_LIST_TOOL_NAME,
        "description": "列出当前群或当前私聊仍待触发的持久提醒。",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}

REMINDER_CANCEL_TOOL: ToolDefinition = {
    "type": "function",
    "function": {
        "name": REMINDER_CANCEL_TOOL_NAME,
        "description": "取消当前会话中一个尚未触发的持久提醒。",
        "parameters": {
            "type": "object",
            "properties": {
                "reminder_handle": {
                    "type": "string",
                    "pattern": "^reminder#[1-9][0-9]*$",
                }
            },
            "required": ["reminder_handle"],
            "additionalProperties": False,
        },
    },
}

MEMORY_TOOLS = [MEMORY_ADD_TOOL, MEMORY_LIST_TOOL, MEMORY_REMOVE_TOOL]

SANDBOX_TOOLS = [
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
]

CONVERSATION_TOOLS = [
    GET_MESSAGE_BY_ID_TOOL,
    SEARCH_MESSAGES_TOOL,
    VIEW_FORWARD_TOOL,
    VIEW_BILIBILI_TOOL,
    SAY_TOOL,
]

BROWSER_TOOLS = [
    BROWSER_NAVIGATE_TOOL,
    BROWSER_SNAPSHOT_TOOL,
    BROWSER_CLICK_TOOL,
    BROWSER_TYPE_TOOL,
    BROWSER_PRESS_KEY_TOOL,
    BROWSER_WAIT_FOR_TOOL,
    BROWSER_SCROLL_TOOL,
    BROWSER_CLOSE_TOOL,
    BROWSER_CLEAR_TOOL,
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
    include_conversation_tools: bool = False,
    include_browser_tools: bool = False,
    include_turn_tools: bool = False,
    include_pin_tools: bool = False,
    include_self_tools: bool = False,
    include_group_tools: bool = False,
    include_reminder_tools: bool = False,
    include_media_tools: bool = False,
) -> list[ToolDefinition]:
    tools: list[ToolDefinition] = []
    if include_web_search:
        tools.append(WEB_SEARCH_TOOL)
    if include_image_ocr:
        tools.append(READ_IMAGE_TEXT_TOOL)
    if include_media_tools:
        tools.extend([VIEW_IMAGE_TOOL, FIND_IMAGES_TOOL, FIND_STICKERS_TOOL])
    if include_voice_transcription:
        tools.append(TRANSCRIBE_VOICE_TOOL)
    if include_voice_reply:
        tools.append(REPLY_WITH_VOICE_TOOL)
    if include_stickers:
        tools.extend([SEND_STICKER_TOOL, SEND_QQ_FACE_TOOL])
    if include_memory_tools:
        tools.extend(MEMORY_TOOLS)
    if include_agent_tools:
        tools.extend(SANDBOX_TOOLS)
    if include_conversation_tools:
        tools.extend(CONVERSATION_TOOLS)
    if include_browser_tools:
        tools.extend(BROWSER_TOOLS)
    if include_turn_tools:
        tools.extend([CONTEXT_EXPAND_TOOL, CONTEXT_SEARCH_TOOL])
    if include_pin_tools:
        tools.extend([PIN_MESSAGE_TOOL, UNPIN_MESSAGE_TOOL])
    if include_self_tools:
        tools.extend([USE_SKILL_TOOL, INSPECT_SOURCE_TOOL])
    if include_group_tools:
        tools.append(GROUP_MEMBERS_TOOL)
    if include_reminder_tools:
        tools.extend(
            [REMINDER_SET_TOOL, REMINDER_LIST_TOOL, REMINDER_CANCEL_TOOL]
        )
    return tools


def force_tool(name: str) -> ToolChoice:
    return {
        "type": "function",
        "function": {"name": name},
    }
