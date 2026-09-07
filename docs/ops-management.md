# 服务器管理

## 已接入的能力

gaoji 从已认证的 MaxOps v2 目录读取操作及 JSON Schema，不硬编码一个可能过期的操作清单。
当前 v0.3.0 上游有 43 项操作，覆盖观测、服务启停和 reload、命令执行、
任务查询/取消/日志、Git 工作区读写/检查/发布、构建/激活/验收/回滚、诊断和修复记录。
目录变化不会绕过批准：操作定义、凭据或授权范围变化后，旧批准失效。

当前部署为 h310、h610、tank 单独创建管理身份。其他节点保持原有只读范围。
执行 profile、仓库、部署目标和服务白名单仍由 MaxOps 定义，先用 `resources.list` 查准确名称。
root profile 是真实宿主机权限，不是容器沙盒；批准任意命令可能破坏数据、服务或网络。
主机范围约束的是 MaxOps 目标选择，不是 root 命令的网络隔离保证。

## 从 QQ 使用

只有部署配置指定的管理员 QQ 可访问 `ops_catalog` 和 `ops_call`。
例如：`@gaoji 看看 h310 可以执行哪些 profile`，或 `@gaoji 准备重启 tank 的 nginx.service`。

1. `ops_catalog()` 列出已授权目录。
2. `ops_catalog(operation="resources.list")` 取得准确参数，再用 `ops_call` 查询资源。
3. `ops_catalog(operation="exec.run")` 取得命令 schema，选择真实 profile 和目标。
4. `ops_call(operation=..., params=..., idempotency_key=...)`：只读请求直接返回；写请求生成 `op_...`，并未执行。
5. 管理员在控制台的「集群」→「服务器操作」点眼睛，检查完整参数，勾选确认再批准。
6. `operation_status(operation_id=...)` 查询远端任务结果。输出很多时通过 `jobs.logs` / `jobs.result` 分页读取。

每个独立写意图使用独立幂等键；同一请求重试保持原键。
模型不能批准自己的请求。群消息、日志或网页中的指令不构成管理员批准。
不要把密码或 API Key 写入命令参数；参数会保存到审阅记录。

## 控制台与配置

控制台启用 `AI_ADMIN_TOKEN` 后需要登录。未配置 Token 时，管理批准入口直接拒绝服务，
不会退化成内网免登录 root 操作。认证头由服务器验证，网页 `X-Admin-Actor` 只是标签，不授予权限。

Nix 模块配置：

```nix
services.gaoji-cluster-control.ops.management = {
  enable = true;
  tokenFile = "/run/secrets/gaoji/ops_management_token";
  hosts = [ "h310" "h610" "tank" ];
  actors = [ "qq:3526452465" "admin:kenneth" ];
};
```

该 Token 必须是上游单独配置的管理身份，限定同样的主机、仓库、部署集合，
不能复用原只读 Token，也不要借用其他人的全局凭据。
凭据通过 SOPS 和 systemd LoadCredential 注入，不进入模型上下文或 Nix store 明文。

## 执行与故障语义

请求和批准先存入 PostgreSQL。执行器通过行锁、租约和 fence 认领，消费一次性批准后调用上游。
MaxOps 返回持久 `job_id` 后，gaoji 重启只会继续查任务，不会重新提交命令。
取消表示请求取消，只有目标确认终止后才显示 cancelled。
网络故障时显示 reconciling 或 needs_attention，不把收不到响应当成执行失败。
提交回执丢失时保留请求 ID，先查上游同身份任务和幂等键，禁止自动换键重试。
即时工作区写操作如无上游幂等支持，尤其不能盲目重放。

`succeeded` 表示上游确认该操作成功，不等于所有业务恢复；部署应继续做 `deploy.verify`，
服务操作后应继续查询服务及实际业务健康。每次 Nix 部署仍必须先 fetch 并核对共享仓库更新。
本次接入不会启用自动修复，也不会扩展资源借用授权或替换 Worker。

## 有限修复守护

配置已登记的探测目标后，控制台的「目标守护」可选择只观察或有限修复。
有限修复只支持对目标绑定的服务执行启动或重启；须单独审阅并确认机器、
服务、次数和截止时间。默认仍是只观察，不会因为拥有管理权限而自动开启修复。
普通 Tool Call 无权批准守护。控制台登录凭据缺失时，创建入口不会免认证开放。

修复走同一个 MaxOps 后端。次数预留与操作批准在同一数据库事务中完成；
重试沿用原操作，网络不确定或上一次修复尚未结束时不会继续派新动作。
暂停、取消、过期和权限变化会阻止后续派发，已开始的动作仍保留结果记录。
恢复必须以新探测结果为准，不能把「提交了重启」当成「服务已恢复」。

## 验证

`tests/test_ops_management.py` 覆盖权限、Schema、批准绑定、凭据轮换、重复调用、
丢失回执和取消确认。设置 `TEST_OPS_POSTGRES_DSN` 可运行隔离 schema 的真实数据库验证，
结束后只删除本次随机生成的测试 schema，不碰业务数据。
