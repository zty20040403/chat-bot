# 0.3.0 可选基础设施启用手册

基础 QQ 对话只需要 NapCat、NoneBot 和 DeepSeek。下面功能都能独立开启，建议一次
只开一组，启动后先看日志和管理页，再继续下一组。

## 1. 安装新增依赖

```bash
cd /path/to/chat-bot
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

`psycopg` 用于 pgvector，`playwright` 用于持久浏览器和代码/表格截图，`Pygments`
用于代码高亮。Chromium 会额外占用磁盘；不安装时机器人仍可对话，代码和表格只会
按普通文本发送。

## 2. 持久 outbox 与流式发送

```text
AI_OUTBOX_ENABLED=true
AI_STREAM_ENABLED=true
```

outbox 状态保存在 `delivery_outbox.sqlite3`：

- `pending`：等待投递。
- `sending`：已领取租约，正在请求平台。
- `committed`：平台返回确定回执，或后续 self-message echo 对账成功。
- `ambiguous`：请求超时、进程中断或租约过期，平台可能已经发出；不会盲目重试。
- `failed`：平台明确拒绝，或安全重试次数用完。
- `cancelled`：管理员取消。

Matrix 使用稳定 transaction id，网络失败可安全重试；OneBot 和 iMessage 超时会先
停放。确认目标会话确实没收到后，才能在管理页手动重试 `ambiguous` 项。

流式输出只发送已经闭合的完整段落。未闭合代码块、`[silence]` 和还没有段落边界的
短回答会等模型完成；`/停止` 会取消任务并关闭 DeepSeek HTTP stream。

## 3. 管理页与配额

```text
AI_ADMIN_ENABLED=true
AI_ADMIN_TOKEN=生成一段足够长的随机字符串
AI_ADMIN_PATH=/bot-admin
AI_QUOTA_ENABLED=true
AI_QUOTA_DAILY_CALLS=100
AI_QUOTA_DAILY_INPUT_TOKENS=500000
AI_QUOTA_DAILY_OUTPUT_TOKENS=100000
```

启动后访问 `http://127.0.0.1:8080/bot-admin`。页面可查看 outbox、token 用量、运行
任务、桥接和浏览器状态，并可停止任务、重试或取消投递。若把 `HOST` 改为公网地址，
必须设置 `AI_ADMIN_TOKEN` 并在反向代理上再加 TLS 与访问控制。

数值 `0` 表示不限制。当前配额按 canonical ConversationScope 和上海自然日计算，
不会因为同一群的不同镜像端重复计费。

## 4. PostgreSQL + pgvector 语义召回

先准备装有 pgvector 扩展的 PostgreSQL。用 Docker/OrbStack 测试可运行：

```bash
docker run -d --name qqbot-pgvector \
  -e POSTGRES_DB=qqbot \
  -e POSTGRES_USER=qqbot \
  -e POSTGRES_PASSWORD=请换成强密码 \
  -p 127.0.0.1:5432:5432 \
  -v qqbot-pgvector:/var/lib/postgresql/data \
  pgvector/pgvector:pg17
```

再配置一个确实提供 `/v1/embeddings` 的 OpenAI-compatible 服务：

```text
AI_SEMANTIC_ENABLED=true
AI_POSTGRES_DSN=postgresql://qqbot:强密码@127.0.0.1:5432/qqbot
AI_EMBEDDING_BASE_URL=https://api.openai.com/v1
AI_EMBEDDING_API_KEY=你的 embedding key
AI_EMBEDDING_MODEL=text-embedding-3-small
AI_EMBEDDING_DIMENSIONS=1536
AI_SEMANTIC_INDEX_SECONDS=60
AI_SEMANTIC_BATCH_SIZE=32
```

DeepSeek Chat API 本身不等于 embedding API，不能直接把聊天模型名填在这里。维度必须
与 embedding 服务实际返回一致。向量表带 Scope、来源句柄和 HNSW cosine 索引；
原始消息仍以 SQLite ledger 为准，删掉向量库后可以重新生成。

## 5. Historian 与 Dream

```text
AI_HISTORIAN_ENABLED=true
AI_HISTORIAN_MODEL=
AI_HISTORIAN_CHECK_SECONDS=60
AI_DREAM_ENABLED=true
AI_DREAM_MODEL=
AI_DREAM_HOUR=4
AI_DREAM_MIN_ENTRIES=15
```

模型留空时使用当前默认 DeepSeek 模型。Historian 只总结一段连续、哈希确定的旧消息，
并要求每条长期记忆建议引用该 capture 内的 `msg#`；发布时 cursor 已变化则 CAS 失败，
不会覆盖新状态。Dream 每天在指定小时做一次长期记忆整理，更新必须携带当前版本，
因此群聊过程中刚被人修改的记录不会被后台静默覆盖。

两者都会额外消耗模型额度，建议先只开 Historian，观察管理页用量后再开 Dream。

## 6. 持久浏览器与富消息截图

```text
AI_RICH_RENDER_ENABLED=true
AI_BROWSER_ENABLED=true
AI_BROWSER_MAX_SESSIONS=3
AI_BROWSER_IDLE_SECONDS=1800
AI_BROWSER_ALLOW_PRIVATE_NETWORK=false
```

模型可调用 `browser_navigate`、`browser_snapshot`、`browser_click`、`browser_type`、
`browser_press_key`、`browser_wait_for`、`browser_scroll`、`browser_close`、
`browser_clear`。页面快照只给
模型 `b1`、`b2` 这类宿主生成的元素引用，不接受任意 CSS selector。profile 位于
`AI_STATE_DIR/browser_profiles`，按发起者哈希隔离并在重启后保留登录状态。
`/clear` 或 `browser_clear` 只删除当前发起者自己的 profile。

默认拒绝 localhost、`.local`、IP 私网和解析到非公网 IP 的地址。浏览器仍是宿主
进程，不应用它打开不可信内网页面，也不要在共享群里登录重要账号。

## 7. Bilibili 与合并转发

这两项无需额外开关。模型在群聊工具回合中可调用：

```text
view_bilibili
view_forward
```

`view_bilibili` 读取公开视频元数据和有限条热门评论；`view_forward` 只允许展开当前
Scope 已有的 `msg#`，子消息中的原生 QQ 号和嵌套 forward id 不会交给模型。

## 8. Matrix 镜像

示例 bundle：

```text
AI_MIRROR_ROUTES_JSON=[{"name":"main","endpoints":[{"platform":"onebot-v11","kind":"group","id":"QQ群号","bot_user_id":"机器人QQ"},{"platform":"matrix","kind":"group","id":"!room:example.org","bot_user_id":"@bot:example.org"}]}]
AI_MATRIX_ENABLED=true
AI_MATRIX_HOMESERVER=https://matrix.example.org
AI_MATRIX_ACCESS_TOKEN=Matrix机器人access token
AI_MATRIX_USER_ID=@bot:example.org
AI_MATRIX_APPSERVICE_TOKEN=单独生成的桥接token
```

程序使用 `/sync` 持久 cursor，timeline 有 gap 时用 `/messages` 回填；发送用稳定 txn id。
若同时配置 Application Service，让 homeserver 把交易发到：

```text
PUT /_matrix/app/v1/transactions/{txn_id}?access_token=AI_MATRIX_APPSERVICE_TOKEN
```

一个 endpoint 只能属于一个 bundle。bundle 中存在 OneBot 时它固定为 canonical；
同一事件在 `/sync` 与 Application Service 重复到达也会按原生 event id 去重。

## 9. BlueBubbles iMessage 镜像

```text
AI_IMESSAGE_ENABLED=true
AI_IMESSAGE_BASE_URL=http://127.0.0.1:1234
AI_IMESSAGE_PASSWORD=BlueBubbles服务密码
AI_IMESSAGE_WEBHOOK_TOKEN=单独生成的随机token
AI_IMESSAGE_CHAT_GUID=iMessage chat GUID
AI_IMESSAGE_BOT_HANDLE=机器人发送账号
```

把 BlueBubbles webhook 指向：

```text
POST http://机器人地址:8080/bot-bridge/bluebubbles?token=AI_IMESSAGE_WEBHOOK_TOKEN
```

并在 `AI_MIRROR_ROUTES_JSON` 中加入 `imessage` endpoint。iMessage POST 没有 Matrix 那样
的幂等 transaction id，超时会进入 `ambiguous`，等 webhook echo 或管理员检查。

桥接 token 为空时 webhook 返回 503，这是故意的 fail-closed 行为。

## 10. 验证

不启动真实机器人也能运行：

```bash
python -m compileall -q src tests
python -m unittest discover -s tests -v
```

然后再启动 `python bot.py`，依次观察：OneBot 连接、outbox worker、可选 Matrix sync、
semantic worker、Historian/Dream 日志。不要一次同时修改所有开关，否则某个外部服务
配置错误时很难判断是哪一层。
