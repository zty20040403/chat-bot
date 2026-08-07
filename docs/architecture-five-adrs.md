# 五份 ADR 在本机器人里的实现

这份文档描述当前实际运行的代码，不把参考项目中的未来设想写成已经完成的功能。

## 总体数据流

```text
QQ / NapCat
  -> OneBot V11 事件
  -> onebot_codec.py 解码
  -> MessageBody 规范 IR
  -> ledger.py 不可变消息账本
  -> context_store.py / long_term_memory.py / turn_journal.py 投影
  -> deepseek.py 组装当前 system + 历史证据 + 用户消息
  -> tool_policy.py 校验模型提出的 tool call
  -> 工具执行并把规范事件写入 turn_journal.py
  -> message_lowering.py 按 OneBot 能力降级
  -> onebot_codec.py 只负责发出已降低的消息
```

## ADR 001：上下文与记忆

`ledger.py` 是事实层。OneBot 原生消息 ID 只用于幂等去重；第一次写入后，消息体、
作者、时间和原始事件不再被重复事件覆盖。程序读取时从 `body_json` 的 Message IR
重新生成提示文本，`rendered_text` 只是可重建的 SQLite 搜索缓存。

`context_store.py` 是投影层。它按当前会话的 token 水位，从最旧的未处理消息开始
发布连续 compartment。每段保存确切 `msg#` 列表、起止范围、源 SHA-256、三档
摘要和随机 `episode#UUID`。发布 compartment 与推进 cursor 在同一个事务中；
展开时重新读取同一 Scope 的源消息并核对列表和哈希。旧摘要坏了可以重建，原文
不受影响。

`long_term_memory.py` 与 chronological context 分开保存。每条长期记忆有 Scope、
版本、创建账号、规范 principal、来源 `msg#` 和 mutation 审计。记忆不能替代某段
聊天覆盖，也不能跨群读取。

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
- 现有 horizon-1 DeepSeek tool loop 保留为稳定执行策略；完整动态 Plan IR 只有在
  validator、审批和回放评测都具备后才适合开启。

## ADR 003：Message IR 与能力降级

`message_ir.py` 定义关闭的节点集合：文本、提及、QQ 表情、媒体、卡片、合并转发
和未知节点。每个节点都有总是可用的文字 fallback，规范 JSON 只编码这一份 IR。

`message_lowering.py` 是唯一的出站降级位置。它根据目标能力决定保留原生节点、
转为文字还是明确丢弃，并记录 `LowerNote`。它还处理 sourceless media、原生媒体
数量预算和按 UTF-8 字节安全分块。`onebot_codec.py` 的发送端只把降低后的节点变成
OneBot `MessageSegment`，不再散落平台降级分支。

## ADR 004：规范句柄与 Scope

模型看见的是本机规范键，不是 QQ 群号或 QQ 消息 ID：

```text
msg#42                 一条规范消息
image#42.1             msg#42 中 segment_index=1 的图片
file#42.2              msg#42 中 segment_index=2 的文件
groupfile#53a7...      当前群文件列表中的文件
@#7                    一个 principal
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
移除，工具调用中 DeepSeek 协议需要的 `reasoning_content` 保留。旧触发消息和回复
会从普通 ledger 窗口排除，防止模型看到两遍。

不满足条件时自动使用确定性 digest。digest 和规范 journal 是长期事实；压缩 trace
只是 14 天 TTL、每 Scope 50 份的可丢弃缓存。纯聊天回合不建立原样工作回放面。

## 本地存储

默认都在 `src/plugins/ai_chat/assets/`，也可通过 `AI_STATE_DIR` 改目录：

```text
bot_state.sqlite3       原始规范消息、会话、principal、identity
context_store.sqlite3   compartment 和覆盖 cursor
turn_journal.sqlite3    turn、规范事件、fork 边、digest、trace cache
long_term_memory.json   长期记忆和 mutation 审计
```

这些运行状态已被 `.gitignore` 排除，不应提交到 GitHub。
