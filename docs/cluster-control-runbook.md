# Kennethbot 集群控制运行手册

本文只覆盖 `0.12.0` 的 P1 只读能力。它不能重启服务、执行 SSH、运行任意命令、部署 NixOS 或借用远程算力。

## 1. 进程与信任边界

```text
QQ / React 管理台
        |
        v
qq-deepseek-bot.service
        |  独立内部 bearer 凭据
        v
kennethbot-cluster-control.service (127.0.0.1:8091)
        |  Kennethbot 专用 MaxOps bearer 凭据
        v
MaxOps hub -> 获准的只读 agents / Prometheus / Alertmanager
```

Bot 进程只拿内部控制 API 凭据。控制服务才拿 MaxOps 凭据。模型、浏览器和 Docker 沙盒两份都拿不到。

## 2. 配置原则

- `inventory` 是 Kennethbot 自己的最大可见范围，`observe = true` 才允许查询。
- `readable_units` 使用完整的 `.service` 名称；空列表表示不能读取任何服务状态或日志。
- MaxOps grant 必须不宽于维护者批准的范围。Kennethbot 本地清单和 MaxOps 都通过，查询才执行。
- 普通集群状态和日志分别配置群权限；管理员用户仍受主机和服务范围约束。
- 控制 API 默认只监听回环地址，不向局域网或 Tailnet 开放。

NixOS 配置示意：

```nix
services.kennethbot-cluster-control = {
  enable = true;
  environmentFile = config.sops.templates."qq-deepseek-bot-postgres.env".path;
  apiTokenFile = config.sops.secrets."kennethbot/cluster_control_token".path;
  maxops = {
    enable = true;
    baseUrl = "http://100.64.0.3:9721";
    tokenFile = config.sops.secrets."maxops/kennethbot_token".path;
  };
  inventory = [{
    host_id = "h610";
    label = "h610";
    architecture = "x86_64-linux";
    site = "home";
    maintainer = "kenneth";
    permission_source = "shared Nix registry and maintainer approval";
    roles = ["bot" "control"];
    observe = true;
    operate = false;
    compute = false;
    readable_units = ["qq-deepseek-bot.service"];
  }];
};

services.qq-deepseek-bot.cluster = {
  enable = true;
  tokenFile = config.sops.secrets."kennethbot/cluster_control_token".path;
  allowedGroups = [611798505];
  logAllowedGroups = [];
};
```

示例中的主机、群和服务不是自动授权。生产配置必须来自真实 registry 和维护者确认。

## 3. 数据库

升级前备份 PostgreSQL，然后执行：

```bash
qq-deepseek-bot-db upgrade
qq-deepseek-bot-db check
```

迁移 `0019_cluster_control` 新增后端状态和最小查询投影。普通状态结果可用于控制服务重启后的 `stale` 回退；服务日志只保存操作、目标、状态和耗时，正文不落库。

## 4. 上线顺序

1. 从共享 Nix registry 生成并人工核对八节点清单。
2. 创建两份随机且不同的 SOPS 凭据：内部控制 API、Kennethbot MaxOps 身份。
3. 在 MaxOps 添加 `kennethbot` 客户端，只授予确认过的只读主机、能力和服务。
4. 先评估 NixOS 配置，再升级数据库。
5. 启动 `kennethbot-cluster-control.service`，确认健康、目录和拒绝测试。
6. 启动或重启 `qq-deepseek-bot.service`。
7. 从管理员私聊和获准群各做一次工具验收，再检查控制台与 Prometheus。

共享仓库每次 rebuild 前先执行 `git fetch origin` 并检查 incoming commits，不能覆盖其他维护者的更新。

## 5. 验收

```bash
systemctl is-active kennethbot-cluster-control.service qq-deepseek-bot.service
curl --fail --silent http://127.0.0.1:8091/health
journalctl -u kennethbot-cluster-control.service -n 50 --no-pager
```

携带运行时凭据的 API 验收应在服务器本机完成，命令历史和输出不得打印 token。必须检查：

- 已登记主机可以查询，未登记主机返回 `forbidden`。
- allowlist 内服务可读，其他服务返回 `forbidden`。
- 错误 token 返回 401，MaxOps 401/403 不会改用其他人的凭据。
- MaxOps 超时时显示控制链路不可用；不能把八台机器都判成离线。
- 非敏感旧结果只以 `stale` 返回，带原观测时间；日志不会从持久投影恢复正文。
- 控制台下拉选择在 SSE 更新时保持，节点详情和日志只有点击后读取。

## 6. 监控

控制服务暴露 `http://127.0.0.1:8091/metrics/`：

- `kennethbot_cluster_queries_total`：按能力、状态和来源统计查询。
- `kennethbot_cluster_query_duration_seconds`：控制服务到上游的端到端延迟。

告警应区分控制服务离线、MaxOps 不可用、单节点观测失败和真实主机告警。一个查询入口故障不能展开成八条主机故障。

## 7. 回滚

先关闭 Bot 的 `cluster.enable`，再撤销 Kennethbot 的 MaxOps client 和凭据，最后停止控制服务。不要停止共享 MaxOps hub、删除其他客户端凭据或清除 Prometheus 数据。

数据库迁移只新增表。应用回滚后这些表可以暂时保留；确认没有新版本使用后再单独执行数据库降级，不能把 NixOS generation 回滚当作数据库回滚。
