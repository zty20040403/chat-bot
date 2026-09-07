# gaoji 系统架构

gaoji 是多模型会话与任务运行时。QQ 是主要入口，平台适配、模型协议、工具执行和投递各自有边界。
架构取舍见[五份本项目 ADR](adr/README.md)。

```text
平台事件 -> 规范消息 -> 原文账本 -> 连续上下文 + 按需召回
                                      |
                               ModelProfile / Gateway
                                      |
                                  Agent Loop
                                      |
                           工具策略 / 子任务 / 证据
                                      |
                           产物登记 -> Outbox -> 平台回执

                   PostgreSQL：事实、状态、审计与派生索引
                   Admin API -> SSE -> React 控制台
                   集群控制面 -> 外部只读接口 / 隔离 Worker / 受限部署器
```

## 代码边界

| 位置 | 职责 |
| --- | --- |
| `bot.py` | 初始化 NoneBot 和平台适配器 |
| `src/plugins/ai_chat/runtime.py` | 唯一资源装配入口 `AppContext` |
| `src/plugins/ai_chat/matchers.py` | 注册消息和命令处理器 |
| `src/plugins/ai_chat/lifecycle.py` | 后台任务启动、停止与异常监督 |
| `src/plugins/ai_chat/ledger.py` | 原始消息事实 |
| `src/plugins/ai_chat/context_store.py` | 章节与覆盖游标 |
| `src/plugins/ai_chat/llm_gateway.py` | 供应商协议适配 |
| `src/plugins/ai_chat/tool_executor.py` | 工具实际执行入口 |
| `src/plugins/ai_chat/subagents.py` | 专业角色、依赖编排和任务恢复 |
| `src/plugins/ai_chat/delivery.py` | 独立的出站投递状态机 |
| `src/bot_storage/` | 数据库访问与迁移工具 |
| `src/cluster_control/` | 运维查询、授权、合同、资源队列 |
| `src/cluster_worker/` | 固定计算模板与租约回执 |
| `src/cluster_deployer/` | 精确版本预检、受批切换和验收 |
| `admin-ui/` | 模型、群友、任务、Trace、数据库及媒体管理 |

## 运行事实与开关

原文、turn、工具效果、投递、任务、资源授权等业务状态位于 PostgreSQL `qq_bot` schema。
数据库 schema 和表名不是产品显示名，重命名产品不应清空或重建这些数据。
图片 Blob、浏览器目录、沙盒工作区和产物有各自的存储及清理策略。

代码具备某个模块，不等于线上已经获准使用。Historian、embedding、桥接平台、外部运维、
Worker、自动修复和部署器均受配置和权限约束，控制台应显示真实状态。
目前仍有集中式编排文件及兼容导入；本次改名不声称完成了额外架构重构。
