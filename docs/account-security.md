# 账户密码与 QQ 手机授权

控制台登录不再使用共享 Token。管理员可以读取管理信息，普通成员只能查看版本、运行时间和 QQ 连接状态；成员无法读取群消息、Trace、日志、配置或审计。

管理员在网页或 QQ 发起变更时，机器人把具体操作及完整参数私聊到该账户绑定的 QQ。本人回复 `确认 AP-操作编号 6位口令` 后自动执行，电脑无需保持页面打开。普通聊天回答、状态查询和固定目标诊断不要求口令；管理命令、具有写入效果的工具与配置更改需要口令。自动守护只能提出修复，每次实际修复仍须单独确认。

## 一次性口令

- 6 位随机数字，保留前导零；3 分钟有效，输错 5 次作废。
- 同时绑定账户版本、本人 QQ、机器人 QQ、登录会话（网页发起时）和本次完整参数。
- 只接受 QQ 私聊确认；模型、群消息和网页都不能代为确认。
- 回复 `重发 AP-操作编号` 生成新口令，所有历史口令失效；回复 `取消 AP-操作编号` 取消尚未开始的操作。
- 确认和排队在同一数据库事务中完成。并发确认、重启、重发都不会让已使用的口令再次执行。
- 执行中断或回执不确定时标记“需要核对”，不自动重放。通知重试最多重复通知，不重复操作。
- 账户停用、改密码、改角色、重新绑定 QQ 会使该账户旧会话及尚未执行的批准失效；退出网页登录也会取消从该会话发起的未执行批准。

## Worker 任务回执

手机确认只是批准提交任务，不代表远程计算已经完成。`cluster_job_submit` 返回排队、运行或验收中时，私聊回执显示“已提交，正在等待服务器执行结果”。

系统持久跟踪该 `job_id`，任务结束后向发起管理员的绑定 QQ 私聊发送最终状态、实际 Worker、结果及错误代码；静态预览结果包含访问链接。h310、h610 和 tank 使用同一流程。通知失败只重试通知，不重新提交任务；机器人进程重启后继续读取原任务状态，账户撤权仍会停止投递。

这是任务状态通知，不是群告警，也不等于文件已经上传 QQ。文件交付仍须独立核对发送回执。

## 首次部署

先备份 PostgreSQL，再运行项目原有的 `gaoji-db upgrade`（源码环境：`python -m src.bot_storage.cli upgrade`），升级到 `0026_admin_accounts_otp`。机器人与集群控制服务应一起升级，以启用守护修复的逐次授权。

1. 在持久的私有目录生成授权密钥：`gaoji-admin init-secret /run/secrets/gaoji/admin-authorization-key`。命令不覆盖已有文件，密钥不打印到终端。若 `/run/secrets` 由秘密管理工具生成，应把生成的密钥导入该工具，保证重启后内容一致。
2. 配置 `AI_POSTGRES_DSN`、`AI_POSTGRES_SCHEMA`（默认 `qq_bot`）、`AI_ADMIN_SECRET_FILE`、`AI_ADMIN_BOT_ID`、`AI_ADMIN_ORIGIN`（例如 `https://bot.example.com`，不含路径）。控制台开关仍是 `AI_ADMIN_ENABLED=true`。
3. 在机器人和 OneBot 发送端配置匹配的 `ONEBOT_ACCESS_TOKEN` 与 `ONEBOT_SECRET`：反向 WebSocket 使用访问凭据，HTTP 事件使用签名。事件入口必须由这些凭据认证，不能暴露允许匿名伪造 QQ 身份的入口。机器人须在线且能私聊管理员 QQ。
4. 在交互终端运行 `gaoji-admin bootstrap`，按提示输入账户名、QQ 和密码。密码 12～128 个字符，隐藏输入，两次校验，不通过命令行参数传递。可用 `gaoji-admin --env-file /私有路径/bot.env bootstrap --secret-file /私有路径/key` 加载环境。
5. 启动机器人，以账户密码登录。创建成员、创建其他管理员或修改账号均在“账户与权限”页面完成，并需要当前管理员在 QQ 私聊确认。

源码执行时把 `gaoji-admin` 替换为 `python -m src.bot_security.cli`。初始化只允许数据库中还没有账户时执行，不能覆盖已有管理员。请保管首个管理员密码和 QQ；丢失全部管理员访问权时需要数据库维护人员走线下恢复，网页没有绕过口令的后门。

受控部署也可使用 `bootstrap --username kenneth --qq-id 管理员QQ --generate-password-file /私有路径/initial-password`。
这会生成随机密码并写入新的 0600 文件；不会覆盖文件、打印密码或覆盖已有账户。读取后请妥善保管，通过手机确认更换密码后删除初始密码文件。
NapCat 已配置反向 WebSocket 时，可设置 `services.gaoji.napcat.reverseWebsocketTokenFile` 与机器人使用同一凭据；模块只更新对应连接的 token，保留其他配置。

NixOS 示例（仅填写文件路径，不将密钥内容放入 Nix store）：

```nix
services.gaoji.admin = {
  secretFile = "/run/secrets/gaoji/admin-authorization-key";
  origin = "https://bot.example.com";
  botId = "机器人QQ号";
};
```

systemd 通过 `LoadCredential` 给机器人读取密钥。启动迁移仍遵循 `services.gaoji.database.migrateOnStart`。
集群 Ops 的 `actors` 白名单需包含获准管理员的 `admin:账户名`（例如 `admin:kenneth`）；账户权限不会扩大上游服务原有的主机或操作范围。服务之间的 Fleet/OneBot 凭据继续使用，不是网页登录 Token。

## 会话与运维

密码只存 Argon2id 哈希，会话有效期 8 小时，使用 HttpOnly、SameSite=Strict Cookie；外部访问必须 HTTPS，写请求验证 Origin 和 CSRF。反向代理应保持配置的公开 Origin；只信任实际代理的转发头。
口令仅以带服务器密钥的哈希保存，确认消息在进入 NoneBot 普通消息日志和模型流程前拦截。QQ 客户端及 OneBot 桥接端可能保留私聊内容，应限制其日志与数据目录访问；本系统不把口令写入会话历史或安全审计。
备份数据库和持久授权密钥；不要在日志中打印环境文件、数据库密码或密钥。没有配置密钥时管理 API 返回 503，旧 Token 无法解锁。首次部署未登录时成员和管理员页面均不返回管理数据。
