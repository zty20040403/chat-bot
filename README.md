# QQ DeepSeek Bot

这是一个最小可跑的 QQ 群聊 AI 机器人骨架：

- 接入层：OneBot V11，推荐先用 NapCatQQ 做自用测试
- 框架层：NoneBot2
- 模型层：DeepSeek API
- 当前功能：`/ai 问题`、`/ai_reset` 清空当前群/私聊记忆

## 目录结构

```text
bot/
  bot.py
  .env.example
  requirements.txt
  pyproject.toml
  src/plugins/ai_chat/
    __init__.py
    config.py
    deepseek.py
    memory.py
    rate_limit.py
```

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
普通提问会把联网搜索和可用图片 OCR 作为 DeepSeek Tool Call 提供给模型，
由模型判断是否调用；工具结果会交回 DeepSeek 后再生成最终回答。
机器人会引用触发它的原消息，并按“群号 + 用户QQ”隔离对话记忆；
群成员的 QQ 号、昵称和群名片保存在本地，不会提交到 Git 仓库。
群聊累计一定数量的普通消息后，机器人会低概率参考上下文主动接一句。
群成员连续 30 分钟没有发言时，机器人会主动暖场；每个群每天最多暖场 2 次，
凌晨 1 点到 8 点保持安静。

手动联网搜索：

```text
/搜 DeepSeek 最新模型
/搜索 Arch Linux 新闻
```

`/搜` 会强制调用 `web_search` 工具，并在回答末尾列出完整来源链接。

识别截图中的文字并交给 AI 分析：

```text
先发送图片，5 分钟内再发送：看看这张图
回复一张图片并发送：识图
/ocr [可与图片分开发送]
@机器人 看看这张图
```

`/ocr`、回复图片说“识图”以及先发图后说“看看这张图”会强制调用
`read_image_text` 工具。
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
先发送语音，5 分钟内再发送：听一下
回复一条语音并发送：/听
回复一条语音并发送：语音识别
```

`/语音` 强制调用 `reply_with_voice`，`/听` 强制调用
`transcribe_voice`；普通 `@机器人` 时由 DeepSeek 自己决定是否调用。
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
```

发送 QQ 自带表情：

```text
/qq表情
/qq表情 14
/qq表情 微笑
```

清空当前群的上下文：

```text
/ai_reset
```

## 常用配置

```text
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_THINKING=disabled
AI_MAX_CONTEXT_TURNS=6
AI_RATE_LIMIT_SECONDS=8
AI_MAX_INPUT_CHARS=1500
AI_MAX_REPLY_CHARS=3000
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
AI_PROACTIVE_CHANCE_PERCENT=15
AI_PROACTIVE_COOLDOWN_SECONDS=120
AI_PROACTIVE_MIN_MESSAGES=4
AI_PROACTIVE_MAX_REPLY_CHARS=180
AI_WARMUP_ENABLED=true
AI_WARMUP_IDLE_SECONDS=1800
AI_WARMUP_COOLDOWN_SECONDS=1800
AI_WARMUP_DAILY_LIMIT=2
AI_WARMUP_CHECK_SECONDS=60
AI_WARMUP_MAX_REPLY_CHARS=80
AI_WARMUP_QUIET_START_HOUR=1
AI_WARMUP_QUIET_END_HOUR=8
```

只允许某些群使用，填 QQ 群号，逗号分隔：

```text
AI_ENABLED_GROUPS=123456789,987654321
```

留空则所有群都可以用。
