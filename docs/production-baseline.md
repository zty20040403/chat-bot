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

## 尚未完成

- 通用 Inbox 和持久化 Agent Job。
- 完整 ChatOrchestrator 与 Platform Adapter。
- 管理后台 RBAC。
- PostgreSQL 第三方仲裁节点、第三份离机备份和完整 PITR。
- Bot Active/Standby 与 Leader Election。
