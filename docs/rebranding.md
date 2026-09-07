# 0.18 命名与升级

产品显示名：`gaoji`。GitHub 仓库及 Python 分发名：`gaojibot`。维护者：Kenneth。

本次保留 Git 历史。此前提交、历史任务、审计和聊天内容不会为了改品牌而被改写。
主许可证为 Kenneth 署名的 MIT；第三方库使用各自许可证。
本地已有 `bot/` 工作目录不需要改名；新 clone 的默认目录为 `gaojibot/`。

## 新命名

| 用途 | 名称 |
| --- | --- |
| 主程序与 systemd 服务 | `gaoji` / `gaoji.service` |
| 数据库维护命令 | `gaoji-db` |
| 控制面 | `gaoji-cluster-control` |
| 计算 Worker | `gaoji-cluster-worker` |
| 部署器 | `gaoji-cluster-deployer` |
| 模型管理服务 | `gaoji-qwen-control` |
| NixOS 主服务选项 | `services.gaoji` |
| 沙盒镜像与 PDF helper | `gaoji-sandbox:latest` / `gaoji-pdf` |
| 指标前缀 | `gaoji_` |
| 默认新安装状态目录 | `/var/lib/gaoji` |

## 升级现有部署

这不是直接替换旧 input 后就能自动切换的兼容版本。部署仓库必须同时更新：

1. 获取最新 Git 远端，合并共享机器上其他人的更新。
2. 把 input URL 改为 `github:zty20040403/gaojibot`，锁定已验证提交。
3. 使用 `nixosModules.gaoji` 和 `services.gaoji`，同步集群服务选项和 unit 引用。
4. 将外部只读接口配置放在控制服务的 `ops` 选项中；非 Nix 部署使用 `KC_OPS_*`。
   地址、凭据内容和上游操作协议不变，本项目只改变适配器名称。
5. 显式保留现有 `user`、`stateDirectory`、`cacheDirectory`、环境文件、数据库 DSN、
   NapCat 容器及数据目录、Nix 缓存卷。不要让默认新目录掩盖原有数据。
6. 同步 Prometheus scrape job、指标查询、告警规则和只读服务名单。
7. 预检系统配置，然后在获准的维护窗口切换；确认旧进程停止、新进程连接 QQ、任务和产物可见。

旧工作区卷按容器实际挂载识别；旧完成检查点和未过期预览仍可读取。
这些兼容标识只用于保护既有数据，不再用于新建资源或产品展示。
镜像初始化使用 `gaoji-cache-seed`，将固定镜像缺少的 store 路径导入已有缓存并登记，
不删除正在使用缓存的容器，也不因标记名变化删除缓存卷。新程序需要配套的 v3 沙盒镜像。
已存在的沙盒不会自动替换其文件系统；其中的旧 PDF helper 在重建沙盒前仍使用原名称。

## 不自动做的事情

- 不改写 Git 历史，不删除旧审计，也不重建 PostgreSQL schema。
- 不删除、移动或重复初始化旧的 Docker 缓存卷。
- 不修改部署者已经自定义的 system prompt、QQ 账号昵称及群名片。
  默认提示词改为 gaoji；部署者的自定义提示词需要同步改自称。
- 不擅自申请新域名或移除已有访问域名；域名与反向代理由部署仓库管理。

## 验证命令

```bash
systemctl is-active gaoji gaoji-cluster-control gaoji-cluster-worker
journalctl -u gaoji -n 50 --no-pager
```

没有执行系统切换前，旧服务名仍然是现网入口。GitHub 改名不会自行重启服务器。
