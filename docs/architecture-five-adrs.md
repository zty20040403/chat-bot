# 五份 ADR 在本机器人里的实现

这份文档描述当前实际运行的代码，不把参考项目中的未来设想写成已经完成的功能。

## 总体数据流

```text
QQ / NapCat ─┐
Matrix ──────┼─> bridges.py / onebot_codec.py 解码与来源去重
iMessage ────┘
  -> MessageBody 规范 IR
  -> ledger.py 不可变消息账本
  -> context_store.py / historian.py / semantic_recall.py 投影
  -> long_term_memory.py / turn_journal.py 可审计状态
  -> model_catalog.py 解析当前会话的不可变 ModelProfile
  -> deepseek.py 组装当前 system + 历史证据 + 用户消息
  -> llm_gateway.py 按协议转换并调用对应 provider
  -> tool_policy.py 校验模型提出的 tool call
  -> 工具执行并把规范事件写入 turn_journal.py
  -> output_planner.py / browser_tools.py 规划文本、控制标记和富截图
  -> delivery.py 持久投递、租约、回执与结果不明停放
  -> message_lowering.py 按 OneBot / Matrix / iMessage 能力降级
  -> 各平台 adapter 发出并由 echo 对账
```

## 运行时装配边界

插件启动不再由 `__init__.py` 分散创建几十个全局对象。当前装配分成四层：

- `matchers.py` 只声明 NoneBot Matcher，不打开数据库、不启动后台任务。
- `runtime.py` 是 composition root。`build_app_context()` 根据配置创建模型目录、LLM
  Gateway、所有存储、桥接、浏览器和领域服务，并放进唯一的 `AppContext`。
- `bootstrap.py` 只把已经创建好的服务注册到 FastAPI 管理面和桥接 webhook。
- `lifecycle.py` 用 `BackgroundTaskSupervisor` 管理提醒、outbox、Matrix、
  semantic、Historian 和 Dream 循环；同名任务不能重复启动，未捕获异常会留下日志，
  关闭时集中取消并等待任务退出。

`__init__.py` 当前保留一组指向 `AppContext` 的兼容别名，使现有 Handler 和测试不必
在一次改动里全部重写。资源所有权仍只有 `AppContext` 一份；NoneBot shutdown 最终只
调用 `AppContext.shutdown()`，由它先停止后台任务和 AI turn，再按顺序关闭异步客户端
与 PostgreSQL 连接池。后续拆分 Handler 时应显式接收 `AppContext` 或所需的最小服务，不能
重新依赖模块导入副作用。

`model_catalog.py` 把每个 profile 的 provider、协议、endpoint、密钥来源、真实模型、
超时、思考模式和能力声明一起校验；密钥不进入 repr、群聊选择状态或管理接口。
`llm_gateway.py` 当前实现 `openai-chat` 和 `anthropic-messages` 两个协议适配器，对上仍
输出统一的内部消息和 tool call 形状，因此工具策略不需要知道供应商。历史文件名
`deepseek.py` 暂时保留兼容导入，但其中 Agent loop 已由 ModelProfile 驱动。

这个边界也允许后续拆出独立 worker 进程：上层业务只依赖服务能力，构造方式和部署
拓扑可以逐步替换。

## ADR 001：上下文与记忆

`ledger.py` 是事实层。OneBot 原生消息 ID 只用于幂等去重；第一次写入后，消息体、
作者、时间和原始事件不再被重复事件覆盖。程序读取时从 `body_json` 的 Message IR
重新生成提示文本，`rendered_text` 只是可重建的 PostgreSQL 搜索缓存。

`context_store.py` 是投影层。它按当前会话的 token 水位，从最旧的未处理消息开始
发布连续 compartment。每段保存确切 `msg#` 列表、起止范围、源 SHA-256、三档
摘要和随机 `episode#UUID`。发布 compartment 与推进 cursor 在同一个事务中；
展开时重新读取同一 Scope 的源消息并核对列表和哈希。旧摘要坏了可以重建，原文
不受影响。

`long_term_memory.py` 与 chronological context 分开保存。每条长期记忆有 Scope、
版本、创建账号、规范 principal、来源 `msg#` 和 mutation 审计。记忆不能替代某段
聊天覆盖，也不能跨群读取。

`historian.py` 是模型驱动但证据受限的后台投影器。它只接收一段确定的连续消息，
生成结果必须引用 capture 内的 `msg#`，发布时再次比较 cursor；并发期间上下文发生
变化就放弃。Dream 只对长期记忆执行带 `expected_version` 的更新或删除。

`semantic_recall.py` 把消息、episode 和记忆送到可选 embedding provider，并写入
同一 PostgreSQL 中的 pgvector HNSW 索引。向量仅用于混合召回，`messages` 原文表仍是事实源。

## ADR 002：工具执行内核的基础层

参考 ADR 明确把完整 Plan/Hole 自适应执行机列为 post-1.0。本项目先实现它要求的
生产基础层：

- `tool_policy.py` 从宿主实际下发的工具 schema 建目录，调用前检查名称、类型、
  必填字段、枚举、长度、上下界和多余字段。
- `turn_journal.py` 记录 `started / rejected / succeeded / failed /
  committed / outcome-unknown`，模型写出的名称和效果标签不会授予权限。
- `sandbox.py` 对 opaque shell 保持粗粒度授权，但执行后生成 observed manifest。
- 服务重启时，只有 started 而没有终态的效果会补写 `outcome-unknown`，不会自动
  重试可能已经产生副作用的操作。
- 最终 QQ 回复也是两阶段效果；每次发送尝试分别记录，回执成功后再链接规范
  `msg#`，超时则保留 `outcome-unknown` 供后续审计。
- `delivery.py` 进一步把所有出站效果放进 lease-based outbox。进程中断或租约过期
  不能证明没发出去，因此停成 `ambiguous`；Matrix 的稳定 transaction id 才允许
  自动安全重试。
- 现有 horizon-1 LLM tool loop 保留为稳定执行策略；完整动态 Plan IR 只有在
  validator、审批和回放评测都具备后才适合开启。

## ADR 003：Message IR 与能力降级

`message_ir.py` 定义关闭的节点集合：文本、提及、QQ 表情、媒体、卡片、合并转发
和未知节点。每个节点都有总是可用的文字 fallback，规范 JSON 只编码这一份 IR。

`message_lowering.py` 是唯一的出站降级位置。它根据目标能力决定保留原生节点、
转为文字还是明确丢弃，并记录 `LowerNote`。它还处理 sourceless media、原生媒体
数量预算和按 UTF-8 字节安全分块。`onebot_codec.py`、`MatrixClient` 和
`BlueBubblesClient` 只发已经降低的内容，不再各自解释规范语义。

`browser_tools.py` 给代码块和 Markdown 表格生成 PNG；浏览失败会回退到原始文本，
不会让表现层故障改变消息事实。持久网页会话使用宿主生成的 `b#` 元素引用，模型
不能提供任意 selector。

## ADR 004：规范句柄与 Scope

模型看见的是本机规范键，不是 QQ 群号或 QQ 消息 ID：

```text
msg#42                 一条规范消息
image#42.1             msg#42 中 segment_index=1 的图片
file#42.2              msg#42 中 segment_index=2 的文件
groupfile#53a7...      当前群文件列表中的文件
[mention#7]            一个 principal（人物，不是 QQ 号）
episode#550e8400-...   一份已发布历史摘要
t#12                   当前会话的第 12 个工作回合
```

工具参数必须完整照抄这些带类型前缀的句柄，不能把 `msg#42` 拆成裸数字。
句柄是定位符，不是权限。`conversation_scope.py` 由当前真实事件构造 Scope；
`ledger.py`、`context_store.py`、`turn_journal.py` 和文件工具都在查询时再次附加 Scope
条件。因此全局递增的 `msg#` 和每群从 1 开始的 `t#` 即使能猜到，也不能跨群读。

## ADR 005：回合连续性

`turn_journal.py` 为每次 AI 请求创建 durable turn，保存规范工具事件、最终结果、
token 使用量、模型、提示词版本和工具目录指纹。含工具的最近回合以 Level 0 `t#`
短行进入上下文；模型可用 `context_expand` 查看 Level 2 规范记录。

用户引用机器人已经发送的结果时，新 turn 写一条 `fork-from` 边。回放器只有在旧
turn 成功、确实调用过工具、trace 未过期、模型/提示词/工具目录匹配且预算足够时
才使用原样 provider segment。当前 system 永远重新生成；旧 final reasoning 被
移除，OpenAI-compatible 工具段需要的 `reasoning_content` 保留。旧触发消息和回复
会从普通 ledger 窗口排除，防止模型看到两遍。

不满足条件时自动使用确定性 digest。digest 和规范 journal 是长期事实；压缩 trace
只是 14 天 TTL、每 Scope 50 份的可丢弃缓存。纯聊天回合不建立原样工作回放面。

## 持久存储

业务状态统一位于 PostgreSQL 的 `qq_bot` schema：

```text
messages / conversations / principals  原始规范消息、会话和身份
context_*                              compartment 和覆盖 cursor
agent_turns / turn_*                   turn、效果事件、fork、digest、trace
deliveries / delivery_attempts         投递、租约、尝试和结果状态
bridge_*                               平台来源、原生副本映射和同步 cursor
usage_events / quota_overrides         token 用量与 Scope 配额覆盖
semantic_documents                    pgvector 语义派生索引
state_blobs                            长期记忆、模型选择等小型状态
```

`AI_STATE_DIR/browser_profiles`、沙盒目录和缓存仍在 h610 本地，但不是权威业务数据。
旧 `.sqlite3`/JSON 文件只允许迁移程序读取，详见 `postgresql-migration.md`。
