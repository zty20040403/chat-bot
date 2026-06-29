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
遇到“最新、今天、查一下、联网、搜索”等问题时，会自动联网搜索后再回答。
群聊累计一定数量的普通消息后，机器人会低概率参考上下文主动接一句。
群成员连续 30 分钟没有发言时，机器人会主动暖场；每个群每天最多暖场 2 次，
凌晨 1 点到 8 点保持安静。

手动联网搜索：

```text
/搜 DeepSeek 最新模型
/搜索 Arch Linux 新闻
```

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
