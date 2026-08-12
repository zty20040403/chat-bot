# QQ DeepSeek Bot

这是一个可长期运行的 QQ 群聊 AI 机器人：

- 接入层：OneBot V11，推荐先用 NapCatQQ 做自用测试
- 框架层：NoneBot2
- 模型层：可配置的 OpenAI Chat Compatible / Anthropic Messages，多 profile 隔离
- 当前功能：对话、联网搜索、OCR、语音、模型切换、持久化上下文、
  长期记忆、语义召回、固定消息、提醒、并发任务、项目沙箱和持久浏览器
- Max 风格交互：消息拆分、指定引用、静默反应、运行中反馈、旁路提问、
  渐进式技能、受限源码自省、规范句柄、流式段落、持久 outbox、管理页和跨平台镜像

## 目录结构

```text
bot/
  bot.py
  .env.example
  requirements.txt
  pyproject.toml
  src/plugins/ai_chat/
    __init__.py
    bootstrap.py
    config.py
    deepseek.py
    llm_gateway.py
    lifecycle.py
    matchers.py
    model_catalog.py
    runtime.py
    agent_tools.py
    context_store.py
    conversation_scope.py
    delivery.py
    bridges.py
    semantic_recall.py
    historian.py
    quota.py
    admin.py
    browser_tools.py
    media_tools.py
    ledger.py
    long_term_memory.py
    message_ir.py
    message_lowering.py
    memory.py
    onebot_codec.py
    output_planner.py
    pins.py
    reminders.py
    sandbox.py
    self_source.py
    skills.py
    tool_policy.py
    turn_journal.py
  skills/
    browser.md
    qq-chat.md
    reminders.md
    sandbox.md
    self-knowledge.md
    web.md
```

`__init__.py` 是 NoneBot 业务入口；`matchers.py` 只声明触发器，`runtime.py`
统一创建并持有存储、桥接、浏览器和调度器，`lifecycle.py` 监管长时间运行的后台任务，
`bootstrap.py` 注册管理页和桥接 HTTP 路由。业务模块不应自行创建第二套全局资源。

## 本地启动

```bash
cd /path/to/chat-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，至少填上：

```text
DEEPSEEK_API_KEY=你的 DeepSeek API Key
```

然后启动：

```bash
python bot.py
```

默认监听：

```text
127.0.0.1:8080
```

## Nix 打包与 NixOS 服务

仓库根目录的 `flake.nix` 会根据 `uv.lock` 构建固定版本的 Python 依赖和机器人源码：

```bash
nix build
nix run
```

机器人仓库同时导出 `nixosModules.qq-deepseek-bot`。在另一份 NixOS flake 中引用：

```nix
inputs.qq-bot = {
  url = "github:zty20040403/chat-bot";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

把 `inputs.qq-bot.nixosModules.qq-deepseek-bot` 加入目标主机模块后即可配置：

```nix
services.qq-deepseek-bot = {
  enable = true;
  environmentFile = "/run/secrets/qq-deepseek-bot.env";
  host = "172.17.0.1";
  port = 18080;
  browser.enable = true;
};
```

API Key 只应放在服务器上的 `environmentFile`，不能直接写进 Nix 配置，否则会进入
可被本机用户读取的 Nix store。Docker 沙箱和独立 NapCat 分别通过
`sandbox.enable`、`napcat.enable` 开启。

## 连接 NapCatQQ

在 NapCatQQ 里配置 OneBot V11 反向 WebSocket，连接到本机 NoneBot：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

机器人在线后，在群里发送：

```text
/ai 帮我解释一下递归
```

机器人正常回答 AI 问题时，会自动在结尾带一个合适的颜文字。
普通 `@机器人` 提问统一进入 LLM Tool Call 循环，不再按“看图”、
“听语音”或“发表情”等关键词提前走固定回复。联网搜索、可用图片理解与 OCR、
语音、表情和授权用户的沙箱工具都会按现场条件提供给模型，由模型判断是否调用；
工具结果会交回当前 profile 对应的模型后再生成最终回答。
模型还可以按需展开 Bilibili 视频、QQ 合并转发和持久网页会话。浏览器默认关闭；
启用方法及 PostgreSQL、Matrix、iMessage 等可选基础设施见
[`docs/operations-v3.md`](docs/operations-v3.md)。
机器人会引用触发它的原消息。QQ 消息进入机器人后，会先转换成统一的
Message IR，再存入 PostgreSQL 规范消息账本。模型在群聊上下文和消息工具里
只看到 `msg#12`、`[mention#3]` 这类机器人内部句柄；原始群号、QQ 号和 NapCat
消息 ID 只留在本地适配层，不直接交给模型，也不会提交到 Git 仓库。
每条未点名的普通群消息都会经过一次轻量 LLM 兴趣判断；只有模型给出很高兴趣分，
并且确实有自然、有用或有趣的话可说时才主动接一句。主动回复没有随机抽样、冷却或
每日次数限制，但默认阈值为 90，因此大多数消息保持沉默。适合短口语表达的主动回复
有 60% 概率直接发送为独立 QQ 语音；语音生成失败会退回文字。机器人不再定时暖场。
消息、上下文、长期记忆、提醒、配额和工具执行记录统一写入 PostgreSQL；
`AI_STATE_DIR` 只保留浏览器 profile 等主机本地状态。生产启动必须配置
`AI_POSTGRES_DSN`，不会自动退回 SQLite。旧 SQLite/JSON 数据只由一次性迁移工具
读取，步骤见 [`docs/postgresql-migration.md`](docs/postgresql-migration.md)。同一个
OneBot 消息重复到达只会命中原记录，不会覆盖原文。较旧聊天按 token 水位压成带精确来源范围、
源哈希和 `episode#` 句柄的 P1/P2/P3 分段，最近消息保留为原文尾部。摘要只是可
重建投影，原始 Message IR 才是事实来源。投影失败时会退回有界原始消息，不会
推进覆盖游标。群聊、私聊和用户长期记忆严格按 `ConversationScope` 隔离。

启用持久媒体库后，QQ 图片会按 SHA-256 去重保存到 `AI_MEDIA_ROOT`，图片元数据、
识别结果和可重试任务保存在 PostgreSQL。后台固定使用 `AI_VISION_PROFILE` 做完整
画面理解，聊天模型通过 `view_image`、`find_images`、`find_stickers` 和
`send_sticker` 使用结果。图片检索严格按当前群隔离；真人、聊天截图、二维码和含
隐私内容的图片不会自动进入可发送表情库。详细部署参数见
[`docs/operations-v3.md`](docs/operations-v3.md)。

## Max 风格交互

机器人现在会先把模型回答交给宿主侧输出规划器，再发给 QQ。规划器能够：

- 按空行或模型给出的 `[split]` 把长回答拆成最多 10 条，代码块不会从中间截断。
- 用 `[reply#编号]` 把某一段引用到当前会话内的指定 `msg#`；句柄在发送前重新校验 Scope。
- 用 `[mention#人物编号]` 指定群成员；发送前会把人物映射到当前群真实 QQ 账号，
  确认成员仍在群里后才生成 OneBot `at` 消息段，内部编号永远不会直接作为 QQ 号。
- 用 `[face#编号]`、`[image#消息.段]` 和 `[sticker#消息.段]` 发送 QQ 表情或
  重用当前会话中的媒体；代码块和行内代码里的这些文本不会被执行。
- 把严格的 `[silence]` 变成 QQ 反应，不把控制标记泄漏到聊天文本。
- 开始处理时临时添加“正在想”反应，成功、失败和静默都有各自的宿主侧状态。
- 第一段默认引用并 @ 提问者，后续拆分段不重复刷引用；语音仍作为独立消息发送。
- 当前 profile 支持流式返回时，闭合的完整段落会先发；代码围栏和 `[silence]` 会等到
  完整答案确定，`/停止` 会取消底层 HTTP stream。
- fenced code 和 Markdown 表格可渲染为 PNG；Playwright 不可用时自动退回原文本。

常用控制命令：

```text
!ps                         查看当前群正在运行的任务
!kill [tID]                 停止指定任务或最新任务
!feedback 补充内容          把新要求送进仍在运行的任务
!btw 另一个问题             并行开启一个不打断原任务的新回合
回复消息并发送 !pin         固定重要消息
!unpin msg#12               取消固定
!pins                       查看当前会话的固定消息
!usage                      查看当前会话 token 用量
!version                    查看机器人版本
```

固定消息按群聊或私聊 Scope 隔离，`/clear` 只清上下文和记忆，不会误删 Pins。
模型还可以通过 `reminder_set`、`reminder_list`、`reminder_cancel` 创建、查看和取消
持久提醒；机器人重启后提醒仍在，发送失败会重试，发送结果不确定时不会盲目重复。

宿主只把简短技能目录放进 system prompt。模型需要某项能力时再调用 `use_skill`
读取完整说明，避免所有操作手册永久占用上下文。`inspect_source` 只允许查看本仓库
白名单目录，禁止访问 `.env`、状态数据库、隐藏文件、绝对路径、符号链接和目录
穿越；`group_members` 只返回 `[mention#principal]`，不会把原始 QQ 号交给模型。

## 工作回合与连续任务

每个 AI 请求现在都会留下一个当前会话内可见的回合编号，例如
`t#3`。机器人会把模型说明、工具开始、成功、失败或已实际发送等事件写入
PostgreSQL 的 `agent_turns` 与 `turn_journal_events`，而不是只保留最后一句回答。普通闲聊仍会记录为回合，
但不会挤进提供给模型的 recent turns 工作摘要。

你可以自然地说：

```text
@机器人 继续 t#3，把刚才的项目再加一个登录页
@机器人 展开 t#3，告诉我上次执行到了哪里
```

当前模型会按需调用 `context_search` 查找当前会话的 `msg#` 和 `episode#`，再用
`context_expand` 展开 `t#` 或 `episode#` 的证据。所有句柄都必须连同宿主给定的
当前 `ConversationScope` 查询；猜中其他群的编号或 UUID 也读不到内容。

机器人成功发出的最终回复、`say` 进度消息和沙箱图片会绑定到对应回合。群友
直接引用这些消息继续提问时，机器人会新建一个 `fork-from` 回合，并自动注入
旧回合摘要、经过时间以及期间新增的少量群聊消息；它不会恢复已经结束的 Python
协程。旧回合只有在成功完成、包含工具工作、归档仍在、模型、提示词版本和工具
目录完全匹配且链预算足够时，才会原样重放 provider 消息段。任一条件不满足就
自动降级到永久保存的确定性摘要，当前 system prompt 永远不会从归档恢复。

为了排查中断，模型和工具之间的短期 trace 默认压缩保留 14 天，每个会话最多
50 份；检测到密码、Token、API Key 或验证码的 trace 不会归档。`/ai_reset`
和 `/clear` 会立刻移动当前会话的可见性边界，让旧 `t#` 不再进入模型上下文。
底层审计行保留在 Tank 的 PostgreSQL 中，不会上传 Git。机器人启动时会把没有完成行
的 `started` 工具效果标记为 `outcome-unknown`，并把未结束回合标为异常中断，
避免把“可能已经执行过”误当成“肯定没执行”。最终 QQ 回复也按发送尝试记录
`started`、`committed`、`failed` 或 `outcome-unknown`；NapCat 超时不会被伪装成
明确失败，若启用短回复重试，两次尝试会分别留下记录。

## 长期记忆和任务管理

自然聊天时当前模型可以调用：

```text
memory_add / memory_list / memory_remove
context_search / context_expand
pin_message / unpin_message
reminder_set / reminder_list / reminder_cancel
use_skill / inspect_source / group_members
```

它只应保存稳定偏好、身份事实和长期约定，不保存临时聊天、密码、Token、
API Key 或验证码。所有记忆都可以手动审计：

```text
/记忆
/记忆 添加 我喜欢简洁回答
/记忆 群 项目统一使用 Python 3.12
/记忆 删除 3
/记忆 清空
/记忆 清空 群
/记忆 审计
```

个人记忆只能用于“当前群 + 当前 QQ 用户”；群记忆只有群管理员或
`AI_SANDBOX_ALLOWED_USERS` 中明确授权的用户可以修改。每条记忆记录版本、创建者
`[mention#principal]`、来源 `msg#` 和更新时间；新增、更新、删除、清空与容量淘汰都会
写入 mutation 审计记录，更新接口使用版本比较避免静默覆盖。

长时间执行沙盒任务时，可以查看或取消自己的当前任务：

```text
/任务
/停止
/停止 t2
```

私聊消息现在也复用同一套 AI、长期记忆、工具和任务管理流程。

按用户和会话切换已配置的模型 profile：

```text
/模型
/模型 deepseek
/模型 claude
/模型 默认
```

`/模型` 会显示 profile 名、provider、真实模型和工具/流式/JSON 能力。选择只保存
profile 名，不保存 API Key；它不影响同群其他用户，也不会提交到 Git 仓库。可用
`AI_GROUP_MODEL_PROFILES_JSON={"201644592":"gpt-5.6-sol"}` 为群配置默认 profile；
群友自己的 `/模型` 选择优先级更高，执行 `/模型 默认` 后会恢复该群默认值。

## 项目沙箱和群文件工具

开启 Docker Desktop 后，可以直接在群里让机器人完成代码任务：

```text
@机器人 创建一个 Python 记账 CLI，运行测试后打包发到群里
@机器人 用 Node.js 做一个静态网页，把源码压缩包发给我
@机器人 看看群里最近上传的文件，把 CSV 导入沙箱后做个统计
@机器人 搜一下最近群聊里谁提到过“比赛”
```

当前模型可以按任务自动调用：

```text
get_message_by_id / context_search / context_expand
search_messages / view_forward / view_bilibili
sandbox_create / sandbox_list / sandbox_destroy / sandbox_exec
sandbox_write_file / sandbox_read_file
send_file_from_sandbox / send_image_from_sandbox
list_recent_files / import_file_to_sandbox
say / send_sticker / send_qq_face
memory_add / memory_list / memory_remove
pin_message / unpin_message
reminder_set / reminder_list / reminder_cancel
use_skill / inspect_source / group_members
browser_navigate / browser_snapshot / browser_click / browser_type
browser_press_key / browser_wait_for / browser_scroll / browser_close
browser_clear
```

`context_search` 会统一搜索当前会话的消息、摘要片段、Pins 和长期记忆，并返回
完整规范句柄。后续读取消息或导入该消息中的附件时，把 `msg#` 原样放进
`message_handle`；消息附件使用 `file#消息.段号`，
群文件列表使用 Scope 绑定的 `groupfile#...`。执行器会在当前群 Scope 内映射回
NapCat 原始消息和文件 ID，模型不能直接看到或提交这些原生 ID。

所有模型工具调用先经过宿主维护的工具目录和 JSON Schema 校验；不存在的工具、
缺少必填参数、越界值和多余字段都会在执行前拒绝，并记为 `rejected`，模型不能
靠自己声明一个新工具来获得权限。每轮和每回合分别有调用预算。

沙箱以“群 + 发起用户”隔离，不挂载宿主机目录。每个容器最多使用 8GB 内存，
CPU 和进程数不设上限。生产部署应使用 `AI_SANDBOX_ALLOWED_USERS` 限制调用者，
并限制同时存在的沙箱数量。
它可以联网安装依赖、构建、测试、打包并把产物发到当前群。
这里的“部署”是把项目在临时沙箱中构建并运行验证；
发布成公网服务仍需要对应云平台的账号、密钥和部署配置。
每次 `sandbox_exec` 还会返回并记入观测清单：完整命令、耗时、退出码、原始
stdout/stderr 的长度与 SHA-256、`/workspace` 变更路径、`docker diff` 和容器
网络模式。它说明命令实际碰过什么，但不会因此扩大沙箱权限。

建议先在 `.env` 中只允许自己的 QQ 使用：

```text
AI_SANDBOX_ENABLED=true
AI_SANDBOX_ALLOWED_USERS=你的QQ号
AI_SANDBOX_MAX_PER_USER=2
AI_SANDBOX_MAX_TOTAL=8
AI_SANDBOX_TIMEOUT_SECONDS=120
AI_SANDBOX_MAX_FILE_MB=0
```

`AI_SANDBOX_MAX_FILE_MB=0` 表示 Bot 不额外限制沙箱文件导入和发送大小；
实际传输仍受 QQ、NapCat、网络和服务器剩余磁盘空间限制。

`AI_SANDBOX_ALLOWED_USERS` 留空时，所有已启用群的成员都能创建沙箱，
会占用本机内存和磁盘。机器人关闭后，已创建容器仍会保留，之后可让机器人
调用 `sandbox_list` 和 `sandbox_destroy` 清理。

手动联网搜索：

```text
/搜 DeepSeek 最新模型
/搜索 Arch Linux 新闻
```

`/搜` 会把原始关键词直接交给 DuckDuckGo，返回标题、摘要和完整链接；
它不经过大模型，也不会写入 AI 对话上下文。普通 `/ai` 和 `@机器人`
仍可由当前模型自动调用 `web_search` 工具并整理回答。
DuckDuckGo 返回人机验证、空结果或请求失败时，会自动改用 Bing RSS
作为备用搜索入口。

识别截图中的文字并交给 AI 分析：

```text
先发送图片，5 分钟内再发送：@机器人 看看这张图
回复一张图片并发送：@机器人 识别并总结
/ocr [可与图片分开发送]
@机器人 看看这张图
```

`/ocr` 会强制调用 `read_image_text` 工具；普通 `@机器人` 消息由
当前模型根据问题和图片是否可用自行决定是否调用，不再使用关键词匹配。
机器人优先识别当前消息中的图片，其次识别被回复的图片，最后使用同一用户
5 分钟内最近发送的图片。Windows 使用 NapCat OCR；macOS 使用系统 Vision OCR，
图片不会上传到第三方视觉服务。

让机器人用 QQ 语音回答：

```text
/语音 给我讲个短笑话
@机器人 用语音回答：递归是什么
```

读取群友发出的 QQ 语音：

```text
先发送语音，5 分钟内再发送：@机器人 听一下
回复一条语音并发送：/听
回复一条语音并发送：@机器人 帮我理解这段内容
```

`/语音` 强制调用 `reply_with_voice`，`/听` 强制调用
`transcribe_voice`；普通 `@机器人` 时由当前模型自己决定是否调用。
语音回答默认使用标记为 `Cute` 的在线神经音色
`zh-CN-YunxiaNeural`，再由机器人本地
解码并编码为腾讯 SILK；不依赖 NapCat PacketBackend，也不需要 NapCat
转换音频。生成语音时，只有待朗读文字会发送给在线 TTS 服务。
网络失败时自动降级到 macOS `Tingting`，语音转文字使用 `fetch_ptt_text`。
可通过 `.env` 中的 `AI_VOICE_NAME`、`AI_VOICE_RATE`、
`AI_VOICE_PITCH` 调整在线音色。
当前音色偏好是软萌、少年感；`Flo（中文中国大陆）` 和
`zh-TW-HsiaoYuNeural` 已试用并明确排除，不要自动改回。
语音回复会作为独立 QQ 语音发送，不附带引用回复；普通文字回答仍会引用原消息。

发送表情包图片：

```text
/表情
@机器人 发个适合现在气氛的表情包
```

发送 QQ 自带表情：

```text
/qq表情
/qq表情 14
/qq表情 微笑
@机器人 发一个可爱的 QQ 自带表情
```

斜杠命令是手动快捷入口；自然语言请求会由当前模型调用
`send_sticker` 或 `send_qq_face`，不会再靠关键词直接发送。

清空当前群的上下文：

```text
/ai_reset
```

## 常用配置

```text
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_THINKING=disabled
AI_MODEL_DEFAULT_PROFILE=deepseek
# AI_MODEL_PROFILES_JSON 的完整示例见 .env.example 和 docs/operations-v3.md
AI_MAX_CONTEXT_TURNS=6
AI_GROUP_CONTEXT_MESSAGES=40
AI_GROUP_CONTEXT_CHARS=4000
AI_LEDGER_ENABLED=true
AI_CONTEXT_LIFECYCLE_ENABLED=true
AI_CONTEXT_INPUT_BUDGET_TOKENS=6000
AI_CONTEXT_HIGH_WATERMARK_TOKENS=4500
AI_CONTEXT_LOW_WATERMARK_TOKENS=2200
AI_CONTEXT_COMPARTMENT_TARGET_TOKENS=1200
AI_CONTEXT_RAW_TAIL_MIN_MESSAGES=8
AI_CONTEXT_MAX_COMPARTMENTS=12
AI_TURN_JOURNAL_ENABLED=true
AI_TURN_RECENT_HOURS=24
AI_TURN_RECENT_LIMIT=5
AI_TURN_ARCHIVE_TTL_DAYS=14
AI_TURN_ARCHIVE_MAX_PER_SCOPE=50
AI_TURN_ARCHIVE_MAX_KB=512
AI_TURN_EVENT_MAX_CHARS=12000
AI_TURN_EXPAND_MAX_CHARS=10000
AI_TURN_REPLAY_ENABLED=true
AI_TURN_REPLAY_MAX_CHARS=40000
AI_TURN_REPLAY_MAX_SEGMENTS=3
AI_MEMORY_MAX_ENTRIES=30
AI_MEMORY_MAX_CHARS=300
AI_MAX_INPUT_CHARS=1500
AI_MAX_REPLY_CHARS=3000
AI_REPLY_CHUNK_DELAY_SECONDS=0.6
AI_TOOL_MAX_ROUNDS=30
AI_TOOL_SIMPLE_MAX_ROUNDS=3
AI_TOOL_MAX_CALLS_PER_ROUND=4
AI_TOOL_MAX_TOTAL_CALLS=60
AI_TOOL_MAX_RESULT_CHARS=12000
AI_TOOL_MAX_CONTEXT_CHARS=60000
AI_SEARCH_ENABLED=true
AI_SEARCH_AUTO_ENABLED=true
AI_SEARCH_MAX_RESULTS=5
AI_SEARCH_TIMEOUT_SECONDS=10
AI_OCR_ENABLED=true
AI_OCR_MAX_IMAGES=2
AI_OCR_MAX_CHARS=4000
AI_OCR_TIMEOUT_SECONDS=30
AI_OCR_RECENT_IMAGE_SECONDS=300
AI_PROACTIVE_ENABLED=true
AI_PROACTIVE_INTEREST_THRESHOLD=90
AI_PROACTIVE_VOICE_PERCENT=60
AI_PROACTIVE_MAX_REPLY_CHARS=180
AI_REMINDERS_ENABLED=true
AI_REMINDER_CHECK_SECONDS=20
AI_REMINDER_MAX_PER_SCOPE=50
AI_OUTBOX_ENABLED=true
AI_STREAM_ENABLED=true
AI_QUOTA_ENABLED=true
AI_QUOTA_DAILY_CALLS=0
AI_QUOTA_DAILY_INPUT_TOKENS=0
AI_QUOTA_DAILY_OUTPUT_TOKENS=0
AI_POSTGRES_DSN=postgresql://qq_bot:强密码@100.64.0.4:5432/qq_bot
AI_POSTGRES_SCHEMA=qq_bot
AI_POSTGRES_POOL_MIN_SIZE=1
AI_POSTGRES_POOL_MAX_SIZE=10
AI_POSTGRES_POOL_TIMEOUT_SECONDS=10
AI_ALLOW_LEGACY_SQLITE=false
AI_RICH_RENDER_ENABLED=true
AI_BROWSER_ENABLED=false
AI_SEMANTIC_ENABLED=false
AI_HISTORIAN_ENABLED=false
AI_DREAM_ENABLED=false
AI_ADMIN_ENABLED=false
AI_MIRROR_ROUTES_JSON=[]
AI_SANDBOX_ENABLED=false
AI_SANDBOX_ALLOWED_USERS=
AI_SANDBOX_MAX_PER_USER=2
AI_SANDBOX_MAX_TOTAL=8
AI_SANDBOX_TIMEOUT_SECONDS=120
AI_SANDBOX_MAX_FILE_MB=20
```

只允许某些群使用，填 QQ 群号，逗号分隔：

```text
AI_ENABLED_GROUPS=123456789,987654321
```

留空则所有群都可以用。

需要让机器人在个别群完全静默时，使用禁用名单：

```text
AI_DISABLED_GROUPS=201644592
```

禁用名单优先于允许名单，并同时阻止命令回复、主动聊天、提醒和待投递消息。

## 五份 ADR 的落地范围

本项目参考了 [HCHogan/max 的 ADR](https://github.com/HCHogan/max/tree/main/docs/adr)
（MIT），但继续使用 NoneBot2、OneBot V11 和独立 LLM Gateway；PostgreSQL 保存规范事实，
pgvector 在同一数据库内保存可重建的语义派生索引：

| ADR | 本项目中的对应实现 |
| --- | --- |
| 001 Context/Memory | 不可变账本、token 水位、`episode#`、来源哈希、pgvector 混合召回、Historian 与 Dream CAS |
| 002 Partial Plans | 宿主 schema、规范效果事件、outbox 租约与 `outcome-unknown`、沙箱观测清单；ADR 标为未来工作的完整 Plan/Hole 执行机仍未伪装启用 |
| 003 Message IR | 富 IR 只存一次；OneBot/Matrix/iMessage 从 IR 降级；统一 outbox、echo、镜像、富截图和 UTF-8 分块 |
| 004 Canonical Handles | 模型只使用 `msg#`、`image#/file#消息.段序号`、`groupfile#`、`[mention#principal]`、`episode#`、`t#`；原生 QQ ID 留在适配层，所有读取和发送重新校验 Scope |
| 005 Turn Continuity | durable turn、`fork-from`、Level 0/1/2、trace TTL/LRU、有效性判定、原样回放、ledger 去重和 digest 退化 |

更细的模块和数据流见 [`docs/architecture-five-adrs.md`](docs/architecture-five-adrs.md)。
本轮与 Max 的功能对照及有意保留的差异见
[`docs/max-compatibility.md`](docs/max-compatibility.md)。引用或改编部分的许可见
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
默认 `context_search` 使用本地词面检索；配置 embedding provider 和 pgvector 后，
会合并 Scope 受限的语义结果。完整启用与故障边界见
[`docs/operations-v3.md`](docs/operations-v3.md)。
