<p align="center">
  <img src="docs/assets/kennethbot-banner.svg" width="100%" alt="Kennethbot - QQ multi-model agent">
</p>

<h1 align="center">Kennethbot</h1>

<p align="center">
  一个能理解群聊上下文、调用工具并完成真实任务的 QQ 多模型 Agent
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-0.5.27-22c55e?style=for-the-badge">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776ab?style=for-the-badge&amp;logo=python&amp;logoColor=white">
  <img alt="NoneBot2" src="https://img.shields.io/badge/NoneBot2-OneBot_V11-ea5252?style=for-the-badge">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-Durable-4169e1?style=for-the-badge&amp;logo=postgresql&amp;logoColor=white">
  <img alt="NixOS" src="https://img.shields.io/badge/NixOS-Reproducible-5277c3?style=for-the-badge&amp;logo=nixos&amp;logoColor=white">
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#系统结构">系统结构</a> ·
  <a href="#常用命令">命令</a> ·
  <a href="#管理控制台">控制台</a> ·
  <a href="#nix-与-nixos">NixOS</a>
</p>

---

Kennethbot 通过 NapCatQQ 接收 OneBot V11 事件，使用 NoneBot2 处理消息，并把对话、
工具调用、长期记忆、媒体任务与投递状态保存到 PostgreSQL。它不只是聊天接口：模型会在
宿主管控的 Agent Loop 中读取证据、选择工具、执行任务，再把适合 QQ 的结果发回群聊。

## 主要能力

<table>
  <tr>
    <td width="50%"><strong>多模型路由</strong><br>兼容 OpenAI Chat 与 Anthropic Messages；按群、用户选择模型 profile。</td>
    <td width="50%"><strong>上下文与记忆</strong><br>理解引用、@、发送者和最近话题；群聊、私聊、个人记忆严格隔离。</td>
  </tr>
  <tr>
    <td><strong>Agent 工具调用</strong><br>搜索、历史消息、提醒、群文件、浏览器与受控源码自省都由模型按需调用。</td>
    <td><strong>图片、表情与语音</strong><br>临时识图、全局安全表情库、QQ 语音转写与腾讯 SILK 语音回复。</td>
  </tr>
  <tr>
    <td><strong>帖子与视频</strong><br>读取分享正文和评论；B 站视频支持抽帧、Whisper 转写与视觉综合分析。</td>
    <td><strong>代码沙箱</strong><br>在临时 Docker 容器中创建项目、安装依赖、测试、打包并把产物发回 QQ。</td>
  </tr>
  <tr>
    <td><strong>Durable Runtime</strong><br>PostgreSQL 保存消息、回合、工具效果、Outbox、提醒和媒体元数据。</td>
    <td><strong>生产部署</strong><br>实时管理控制台、Nix Flake、NixOS module、systemd Worker 与数据库迁移。</td>
  </tr>
</table>

## 系统结构

```mermaid
flowchart LR
    QQ[QQ Client] --> NC[NapCatQQ]
    NC -->|OneBot V11 WS| NB[NoneBot2 Gateway]

    subgraph Runtime[Kennethbot Runtime]
        NB --> IR[Message IR + Ledger]
        IR --> CTX[Context Planner]
        CTX --> AGENT[Agent Loop]
        AGENT --> LLM[Multi-model Gateway]
        LLM --> AGENT
        AGENT --> TOOLS[Tool Policy + Executor]
        TOOLS --> MEDIA[Vision / Voice / Video]
        TOOLS --> BOX[Docker / Browser]
        AGENT --> OUT[Output Planner + Outbox]
    end

    IR --> PG[(PostgreSQL)]
    CTX --> PG
    TOOLS --> PG
    OUT --> PG
    OUT --> NC

    classDef core fill:#18181b,stroke:#22c55e,color:#fafafa,stroke-width:2px;
    classDef service fill:#27272a,stroke:#71717a,color:#fafafa;
    classDef data fill:#172554,stroke:#60a5fa,color:#eff6ff;
    class AGENT,LLM core;
    class NC,NB,IR,CTX,TOOLS,MEDIA,BOX,OUT service;
    class PG data;
```

模型不会直接操作 NapCat 或数据库。消息先转换成统一的 Message IR，Agent 只能调用宿主
明确提供且经过 JSON Schema 校验的工具，最终输出再由宿主降级为 OneBot 消息段并发送。

## 技术栈

| 层级 | 实现 |
| --- | --- |
| QQ 接入 | NapCatQQ、OneBot V11 |
| Bot 框架 | NoneBot2、FastAPI、Uvicorn |
| 模型网关 | OpenAI Python SDK、HTTPX、自定义 Anthropic 协议适配 |
| 数据库 | PostgreSQL、Alembic，可选 pgvector |
| 浏览器 | Playwright、Chromium |
| 沙箱 | Docker |
| 图片理解 | 可配置视觉模型 profile |
| 视频分析 | Bilibili 公共接口、FFmpeg、whisper.cpp |
| 语音 | Edge TTS、腾讯 SILK、NapCat 语音转写 |
| 富文本 | CodeSnap、Pygments |
| 部署 | Nix Flakes、NixOS、systemd |

## 目录结构

```text
bot/
├── bot.py                       # NoneBot 启动入口
├── pyproject.toml               # Python 项目与依赖声明
├── uv.lock                      # 可复现依赖锁
├── flake.nix                    # Nix 包、开发环境和模块导出
├── nix/module.nix               # NixOS 服务模块
├── migrations/                  # PostgreSQL / Alembic 迁移
├── skills/                      # Agent 按需加载的操作说明
├── docs/                        # 架构、运维和迁移文档
├── tests/                       # 单元测试与集成测试
└── src/
    ├── bot_storage/             # PostgreSQL、迁移与存储工具
    └── plugins/ai_chat/
        ├── __init__.py          # 插件装配与消息主流程
        ├── runtime.py           # 全局服务和后台 Worker 生命周期
        ├── deepseek.py          # Agent Loop
        ├── llm_gateway.py       # 多协议模型网关
        ├── context_pipeline/    # 上下文规划与证据选择
        ├── turn_journal.py      # 持久回合、工具事件和连续任务
        ├── media_library.py     # 永久表情库
        ├── vision_worker.py     # 一次性图片理解任务
        ├── video_analysis.py    # B 站视频深度分析
        ├── sandbox.py           # Docker 任务沙箱
        ├── browser_tools.py     # 持久浏览器工具
        ├── output_planner.py    # 回复拆分与控制句柄
        └── admin.py             # 管理控制台 API
```

## 运行要求

你需要准备：

1. Python 3.12，或启用了 Flakes 的 Nix。
2. PostgreSQL 服务器。
3. 至少一个可用的大模型 API Key。
4. 已登录 QQ 的 NapCatQQ。
5. 可选的 Docker、Chromium、FFmpeg 和 whisper.cpp。

生产模式必须连接 PostgreSQL，不会静默退回 SQLite。

## 快速开始

> [!IMPORTANT]
> 生产模式必须连接 PostgreSQL，不会静默退回 SQLite。API Key、数据库 DSN 和管理
> Token 只能放在 `.env` 或服务器密钥文件中，不能提交到 Git。

### 1. 安装依赖

```bash
git clone https://github.com/zty20040403/chat-bot.git
cd chat-bot
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

也可以使用 Nix 开发环境：

```bash
nix develop
```

### 2. 准备 PostgreSQL

下面是只适合本地开发的 Docker 示例：

```bash
docker run -d \
  --name kennethbot-postgres \
  -e POSTGRES_USER=qq_bot \
  -e POSTGRES_PASSWORD=change-me \
  -e POSTGRES_DB=qq_bot \
  -p 5432:5432 \
  postgres:17
```

在 `.env` 中配置连接：

```dotenv
AI_POSTGRES_DSN=postgresql://qq_bot:change-me@127.0.0.1:5432/qq_bot
AI_POSTGRES_SCHEMA=qq_bot
```

初始化数据库：

```bash
python -m src.bot_storage.cli upgrade
python -m src.bot_storage.cli check
```

### 3. 配置模型

最小 DeepSeek 配置：

```dotenv
DEEPSEEK_API_KEY=replace-with-your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
AI_MODEL_DEFAULT_PROFILE=deepseek
```

多模型配置使用一个 JSON 目录。Key 只通过环境变量引用，不要写进 JSON：

```dotenv
DEEPSEEK_API_KEY=replace-with-your-deepseek-key
OPENAI_API_KEY=replace-with-your-openai-key
AI_MODEL_PROFILES_JSON={"default":"deepseek","profiles":{"deepseek":{"provider":"deepseek","protocol":"openai-chat","base_url":"https://api.deepseek.com","api_key_env":"DEEPSEEK_API_KEY","model":"deepseek-v4-flash","aliases":["ds"]},"openai":{"provider":"openai","protocol":"openai-chat","base_url":"https://api.openai.com/v1","api_key_env":"OPENAI_API_KEY","model":"gpt-5-mini","vision":true,"aliases":["gpt"]}}}
```

完整字段和可选服务见 [`.env.example`](.env.example)。

### 4. 启动 Bot

```bash
python bot.py
```

默认监听 `127.0.0.1:8080`。根路径没有网页内容，返回 `Not Found` 是正常现象。

### 5. 连接 NapCatQQ

在 NapCatQQ 中新增 OneBot V11 反向 WebSocket：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

NapCat 日志出现连接成功后，可以在 QQ 中测试：

```text
/ai 你好
@机器人 帮我解释一下递归
```

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `/ai 问题` | 发起普通 AI 对话 |
| `/搜 关键词` | 直接联网搜索，不经过模型整理 |
| `/模型` | 查看当前可用模型与选择 |
| `/模型 profile` | 为当前用户切换模型 |
| `/模型 默认` | 恢复群默认模型 |
| `/识图` | 读取当前、引用或最近图片 |
| `/听` | 转写引用或最近 QQ 语音 |
| `/语音 内容` | 使用语音回答 |
| `/表情` | 从全局表情库发送图片表情 |
| `/qq表情 名称` | 发送 QQ 自带表情 |
| `/表情状态` | 查看表情库状态 |
| `/记忆` | 查看或管理长期记忆 |
| `/任务` | 查看当前任务 |
| `/停止` | 取消当前任务 |
| `/usage` | 查看当前会话用量 |
| `/pin`、`/pins` | 固定消息或查看固定列表 |
| `/ai_reset` | 重置当前会话的 AI 上下文边界 |
| `/clear` | 清理当前会话的上下文和存储数据 |

自然语言请求不依赖关键词硬编码。`@机器人 看看这张图`、`帮我查一下最新消息`、
`创建一个 Python 项目并把文件发出来` 等请求会进入同一个 Agent Loop，由当前模型决定
是否调用相应工具。

## 上下文与记忆

每条 QQ 消息会写入不可变的规范消息账本。模型看到的是 `msg#12`、`[mention#3]`、
`image#12.0`、`t#8` 等内部句柄；原始 QQ 号、群号和 NapCat 消息 ID 留在本地适配层。

上下文由四部分组成：

1. 当前问题、引用链和被 @ 的完整句子。
2. 当前群最近的原始消息窗口。
3. 较旧消息生成的分段摘要和按需展开证据。
4. 当前群、当前用户可见的 Pins 与长期记忆。

消息事实按群聊或私聊隔离，个人记忆进一步按用户隔离。模型不能通过猜测内部编号读取
其他群的数据。`/clear` 只影响当前会话，不会清除其他群的上下文。

每个 Agent 请求还会生成持久回合 `t#`，记录模型、工具调用、进度、最终回答和异常状态。
引用机器人之前的任务回复继续提问时，新回合可以继承旧回合摘要和期间新增的群聊消息。

## 图片与表情

普通图片和表情使用两条独立链路：

- **普通图片**：创建一次性 `vision_jobs`，按图片地址调用视觉模型。交付结果后清理 URL 和
  识别结果，不保存图片 Blob。
- **QQ 表情**：只有平台明确标记为表情的图片才会下载，按 SHA-256 去重，并经过真人、隐私和
  安全检查后进入永久表情库。

所有群共享同一套安全表情库。`send_sticker` 会综合标签相关度、近期发送记录和历史使用次数
选择候选；用户说“换个”时会排除刚发过的图片，没有其他匹配候选就明确返回没有。

管理台中的群“识图”开关只控制自动图片介绍：开启后，成员单独发送不带文字的普通图片时，
机器人会自动回复简短简介；关闭后仍然可以通过 @ 或 `/识图` 手动查看图片。

## 帖子与视频

`inspect_shared_content` 可以读取群里的 `source#`、`msg#` 或完整链接。普通模式读取页面元数据、
正文和评论；B 站视频支持额外的深度模式：

1. 获取低清 DASH 视频与音频流。
2. 临时下载媒体文件。
3. 使用 FFmpeg 均匀抽取关键帧。
4. 使用本地 whisper.cpp 转写音轨。
5. 把关键帧、转写、标题和用户问题交给视觉模型综合分析。
6. 删除临时媒体文件，只缓存有时效的分析结果。

默认深度分析上限为 60 分钟、1GB 和 12 张关键帧。它不是逐帧审片，转写也可能误识别
专有名词，回答中会保留这些限制说明。

## Docker 沙箱

启用沙箱后，Agent 可以在临时 Docker 容器中：

- 创建和修改项目文件。
- 安装依赖、执行命令和运行测试。
- 导入当前群上传的文件。
- 把构建产物、压缩包或图片发回当前群。
- 使用 `say` 汇报长任务进度。

沙箱不挂载宿主机目录，并按“群 + 发起用户”授权。任务结束后，本轮创建的容器会自动销毁。
生产环境应配置允许使用沙箱的 QQ 账号和并发数量；开放给所有群成员可能消耗大量 CPU、
内存、网络和磁盘。

```dotenv
AI_SANDBOX_ENABLED=true
AI_SANDBOX_ALLOWED_USERS=123456789
AI_SANDBOX_MAX_PER_USER=2
AI_SANDBOX_MAX_TOTAL=8
AI_SANDBOX_TIMEOUT_SECONDS=120
```

## 管理控制台

启用管理台：

```dotenv
AI_ADMIN_ENABLED=true
AI_ADMIN_TOKEN=replace-with-a-long-random-token
AI_ADMIN_PATH=/bot-admin
```

访问：

```text
http://127.0.0.1:8080/bot-admin
```

控制台提供：

- Bot、NapCat、后台 Worker 与数据库状态。
- 模型 profile 的协议、模型名和能力开关。
- “我自己”和“其他群友”两类默认模型配置。
- 每个群的启用开关、自动识图开关和成员单独覆盖。
- 当前沙箱、执行任务、媒体库、识图队列和旧数据治理状态。
- Token 用量、Agent 回合、工具调用和投递状态。

推荐只通过 Tailscale、反向代理或 SSH 隧道在可信内网访问。不要把无 Token 的管理台直接暴露
到公网。

## Nix 与 NixOS

仓库可直接构建和运行：

```bash
nix build
nix run
nix flake check
```

在自己的 NixOS flake 中引用：

```nix
inputs.qq-bot = {
  url = "github:zty20040403/chat-bot";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

把模块加入目标主机：

```nix
imports = [inputs.qq-bot.nixosModules.qq-deepseek-bot];

services.qq-deepseek-bot = {
  enable = true;
  environmentFile = "/run/secrets/qq-deepseek-bot.env";
  host = "127.0.0.1";
  port = 18080;

  sandbox.enable = true;
  browser.enable = true;
  codesnap.enable = true;
  videoDeep.enable = true;
};
```

`environmentFile` 应位于 Nix store 外部并由 sops-nix、agenix 或其他密钥系统生成。
不要把 API Key、管理 Token 或数据库密码直接写进 Nix 表达式。

更新服务器前建议严格按下面的顺序操作，避免共享配置被旧工作树回滚：

```bash
git fetch origin
git status -sb
git pull --ff-only
sudo nixos-rebuild switch --flake .#your-host
```

## 数据库与归档

PostgreSQL 是消息、上下文、模型选择、记忆、工具日志、媒体元数据和任务状态的事实来源。
Alembic 在升级时管理 schema 版本，NixOS module 默认在服务启动前执行迁移。

生产环境可以把低延迟节点作为首选数据库，把容量更大的节点用于复制、备份和冷归档。
普通回复不应同步读取冷归档；旧媒体、投递正文和大文件由后台任务异步迁移，归档不可用时不会
阻塞普通聊天。

旧 SQLite/JSON 数据的迁移方法见
[`docs/postgresql-migration.md`](docs/postgresql-migration.md)。完整生产配置和故障边界见
[`docs/operations-v3.md`](docs/operations-v3.md)。

## 开发与测试

进入固定开发环境：

```bash
nix develop
```

运行测试：

```bash
AI_ALLOW_LEGACY_SQLITE=true python -m unittest discover -s tests
nix flake check
```

提交前至少检查：

```bash
git diff --check
git status -sb
```

涉及数据库、工具权限、消息 Scope、媒体发送或 NixOS module 的改动，应补充相应的定向测试。

## 安全边界

- `.env`、API Key、数据库 DSN、QQ Token 和管理 Token 不应提交到 Git。
- 模型只获得当前会话可见的规范句柄，工具执行时会再次检查 Scope 与权限。
- 图片 OCR、网页内容、群文件和工具返回值都视为不可信输入，不能覆盖 system prompt。
- Docker 沙箱不是宿主机管理员权限；不要挂载宿主目录或 Docker Socket 到任务容器。
- NapCat 和 QQ 账号应运行在受控环境中，并定期备份数据库与表情 Blob。
- 管理控制台应使用强 Token，并限制在可信网络访问。

## 文档

- [从零搭建教程](docs/from-zero.html)
- [生产运维与配置](docs/operations-v3.md)
- [PostgreSQL 迁移](docs/postgresql-migration.md)
- [上下文、消息 IR 与连续任务架构](docs/architecture-five-adrs.md)
- [第三方组件声明](THIRD_PARTY_NOTICES.md)
