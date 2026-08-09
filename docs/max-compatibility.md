# Max 兼容性与改良范围

本文记录 `qq-deepseek-bot 0.3.0` 对
[HCHogan/max](https://github.com/HCHogan/max) 及其五份 ADR 的参考范围。对照基准为
Max 提交 `d80be22a29dbf35222bdb09699db0f54f737e370`（2026-08-09）。

目标是把 Max 已验证的交互和架构思想移植到本项目，同时保留 NoneBot2、
OneBot V11、NapCat、DeepSeek 和本机优先的运行方式。实现不是逐行翻译；平台协议、
模型和部署条件不同，因此不能保证回答文字、音色或每个原生消息段完全一致。

## 已移植能力

| 能力 | 本项目实现 |
| --- | --- |
| 规范消息账本 | OneBot、Matrix、iMessage 事件先转成 Message IR，再幂等写入 SQLite 事实层 |
| 精确上下文 | token 水位、原文尾部、可重建 `episode#`、来源范围与哈希、Scope 隔离 |
| 混合召回 | 词面搜索始终可用；可选 OpenAI-compatible embedding + PostgreSQL/pgvector HNSW |
| Historian | 后台按连续证据生成 P1/P2/P3 摘要，用 cursor CAS 发布，失败不推进覆盖范围 |
| Dream | 按版本 CAS 合并、更新或归档长期记忆，操作与理由进入原有 mutation 审计 |
| 规范身份 | 模型只见 `msg#`、`episode#`、`t#`、`@#principal`；原生账号留在适配层 |
| 工作回合 | durable turn journal、工具效果状态、fork、digest、条件 replay、崩溃恢复 |
| 持久投递 | SQLite outbox、幂等键、租约、尝试日志、OneBot echo 对账、超时结果不明停放 |
| 跨平台镜像 | OneBot/Matrix/BlueBubbles iMessage bundle、来源去重、原生引用映射、循环抑制 |
| 输出规划 | 空行/`[split]`、代码块保护、`[reply#]`、`[silence]`、反应与分段延迟 |
| 原生流式 | 完整段落到达即发送；代码围栏和单段回答暂存；`/停止` 取消底层 HTTP stream |
| 富消息渲染 | fenced code 与 Markdown 表格通过 Playwright/Pygments 渲染成 PNG，失败退回文本 |
| 持久浏览器 | 每个发起者独立 Playwright profile、稳定元素句柄、会话上限、空闲回收、SSRF 检查 |
| 媒体展开 | Bilibili 视频资料与热评、NapCat 合并转发子消息；返回值不暴露原生 QQ ID |
| 管理与配额 | `/bot-admin` 后台、投递重试/取消、任务停止、平台状态、按会话每日 token 配额 |
| Pins/提醒/技能 | Scope 固定消息、持久提醒、渐进式技能、白名单源码自省 |
| QQ 既有能力 | OCR、语音输入输出、表情、搜索、群文件、Docker 沙箱、主动接话和暖场 |

## 五份 ADR 的落点

1. ADR 001：规范账本是事实源；摘要、向量和长期记忆都是可重建或可审计投影。
2. ADR 002：工具由宿主注册与校验，效果进入回合日志；当前使用有界 horizon-1
   tool loop，没有伪装成 ADR 中仍属未来工作的完整 Hole/Plan 执行机。
3. ADR 003：消息只保存一份富 IR；OneBot、Matrix 和 iMessage 在边缘按能力降级，
   投递证据与规范内容分开保存。
4. ADR 004：模型可引用对象都使用 Scope 绑定句柄；每次解析重新做权限检查。
5. ADR 005：新消息可以 fork 旧回合；满足环境指纹时回放 provider 片段，否则使用
   永久 digest，旧 system prompt 永不恢复。

## 有意保留的差异

- SQLite 仍是单机事实层；PostgreSQL 只负责可删除重建的语义向量，不会成为第二份
  消息事实源。
- 镜像 bundle 里只要包含 OneBot，OneBot 必须是 canonical endpoint。这样现有
  NoneBot 命令、群权限和 QQ 原生工具不会在跨平台时产生两套身份。
- Matrix 与 iMessage 当前承担持久镜像、引用映射和回执对账；独立平台上的 `@机器人`
  不会直接启动完整 NoneBot 工具回合。完整 AI 入口仍在 QQ/NapCat。
- Matrix/iMessage 出站媒体目前按能力安全降级为文字说明；QQ 保留原生图片、文件、
  语音和表情。不会为了看起来一致而发送平台不支持的伪消息段。
- 持久浏览器默认关闭，并且不承诺是对敌意网页的完整安全沙箱。默认阻断本机、
  私网和非 HTTP(S) 地址；高风险浏览任务仍应放进隔离容器。
- 模型选择仍针对同一个 OpenAI-compatible DeepSeek endpoint；没有照搬 Max 的生产
  provider、人格、私有部署和 iMessage IMCore 环境。

## 默认启用边界

outbox、流式发送、配额记录和富消息渲染代码默认启用；若未安装 Chromium，富消息
会明确退回普通文本。pgvector、Historian、Dream、管理页、浏览器和跨平台桥接都
需要显式配置。完整步骤见 [`operations-v3.md`](operations-v3.md)。

## 安全边界

模型没有宿主文件系统任意读取权。源码自省采用固定仓库根与白名单；Docker 沙箱
不挂载宿主目录；工具参数经过宿主 JSON Schema 和策略校验。Pins、提醒、消息、
回合、浏览器 profile 和长期记忆按 ConversationScope 或发起者隔离。桥接 webhook
没有 token 时返回 503，不会静默开放。

参考实现受 MIT License 许可；完整声明见仓库根目录
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。
