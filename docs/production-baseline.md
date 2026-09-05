# Kennethbot 生产基线

更新日期：2026-08-25。本文只记录仓库和 Nix 配置能够证明的事实，不把计划写成
已经上线的能力。

## 当前拓扑

- Bot 与 NapCat 运行在 h610，由 NixOS 和 systemd 管理。
- PostgreSQL 17 使用 `pg_auto_failover`：h610 是优先级 100 的首选主库，Tank 是
  优先级 50 的同步热备。
- monitor 当前位于 h610，因此 h610 整机故障时仍需要人工恢复；这不是完整三节点 HA。
- 结构化业务状态统一写入 PostgreSQL；普通图片采用临时生命周期，安全表情包才长期保存。
- h610 通过 NFS 把大体积冷归档写到 Tank；Tank 离线不应阻塞普通聊天。

## 已具备的可靠性能力

- Alembic 是生产 Schema 的唯一变更入口。
- Message Ledger、Agent Turn Journal 和 Outbox 保存消息、工具效果与投递状态。
- Outbox 使用幂等键、租约和 `ambiguous` 状态避免发送超时后盲目重试。
- 模型网关支持能力匹配、自动降级、熔断和半开探测。
- PostgreSQL 节点支持显式 fence、enroll 和受保护 rejoin。
- 两个数据库节点分别执行容量感知的 custom-format 逻辑备份，并每周做一次隔离还原
  校验。

## 本轮新增的观测基线

- `/metrics` 暴露低基数 Prometheus 指标，不包含 QQ 号、群号或消息正文。
- AI 回合使用 128 位 `trace_id`；模型、上下文、工具和发送阶段建立嵌套 Span。
- 配置 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 后通过 OTLP/HTTP 输出 Trace。
- 首轮部署后的 7 天数据用于确定 P50/P95/P99、工具成功率和模型降级率，未采样前
  不在文档中虚构 SLO 数字。

## 上下文准确率基线

- 引用消息在当前会话范围内组成关系图；显式引用优先级最高，省略式追问再按话题
  连续性、问题状态、时间和参与者重排。
- 群时间线、群长期记忆、当前用户长期记忆、Historian 摘要和 pgvector 语义结果
  使用统一 Reranker，但采用独立阈值与数量上限。
- 语义结果必须重新通过当前 Ledger 可见性和精确 scope 校验；不能跨群、跨用户，
  `/clear` 前已经不可见的消息也不能借向量索引重新进入提示词。
- 焦点、群现场、群记忆、个人记忆和语义历史使用独立 Token 分区，避免任何单一
  来源挤占完整上下文。
- `tests/fixtures/context_accuracy_cases.json` 保存追问回归场景与提示词变体。
  原文覆盖率和预设检索结果的单元测试只证明组装逻辑，不能证明焦点命中率。
- `tools/context_eval.py` 单独评分模型实际回答、关联原文、Recall@5 和跨群/用户错误。
  未运行真实模型基准前，不宣称达到 90% 或 100% 的回答准确率。运行方法与限制见
  [上下文评测](context-evaluation.md)。

## 尚未完成

- 通用 Inbox 和持久化 Agent Job。
- 完整 ChatOrchestrator 与 Platform Adapter。
- 管理后台 RBAC。
- PostgreSQL 第三方仲裁节点、第三份离机备份和完整 PITR。
- Bot Active/Standby 与 Leader Election。
