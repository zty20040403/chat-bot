# Bot PostgreSQL 迁移手册

## 最终结构

```text
QQ / NapCat
    |
    v
h610: qq-deepseek-bot
    |
    | libpq 自动选择当前 read-write 节点
    v
Tank  100.64.0.4:55432  <==同步复制==>  h610 100.64.0.3:55432
首选节点，14T 冷存储                    故障接管，小盘热数据
```

两个 PostgreSQL 节点保存相同的结构化热数据，由 `pg_auto_failover` 决定唯一可写
主库。Tank 正常时是首选主库；Tank 离线时 h610 接管。连接串同时列出两个地址并
使用 `target_session_attrs=read-write`，不会误写只读副本。

Tank 的 14T 空间用于媒体、群文件、沙盒产物和旧数据归档，这些大对象不进入双机
PostgreSQL。h610 实际只有约 476.9G，总数据库规模必须按 h610 的容量设计。浏览器
profile、沙盒工作目录和下载缓存仍留在 h610，因为它们是主机相关或可重建数据。

Tank 离线期间 monitor 暂时位于 h610。正式生产应迁移到第三台常在线的小机器；
monitor 只保存集群状态，不保存机器人业务数据。

PostgreSQL 只允许 `qq_bot` 用户从 h610 的 Tailscale 地址连接。密码放在 sops 和
systemd `EnvironmentFile`，不能写进 Git、Nix store 或命令行历史。Alembic 在线
迁移把多主机 DSN 原样交给 Psycopg，因此与机器人运行时采用相同的自动选主逻辑。

## 数据库命令

以下命令都读取 `AI_POSTGRES_DSN` 和 `AI_POSTGRES_SCHEMA`：

```bash
qq-deepseek-bot-db upgrade
qq-deepseek-bot-db current
qq-deepseek-bot-db check
```

运行时本身不建表、不改表。NixOS module 默认在启动 Bot 前执行 `upgrade`；数据库
不可达或版本不匹配时，Bot 会停止启动，而不是回退到本地 SQLite。

## 首次回填

迁移必须在 Bot 停止后进行，避免扫描完成后又产生新消息。

```bash
sudo systemctl stop qq-deepseek-bot
sudo cp -a /var/lib/qq-deepseek-bot/state \
  /var/lib/qq-deepseek-bot/state.pre-postgres
```

先升级空的 PostgreSQL schema，再只读扫描旧目录：

```bash
qq-deepseek-bot-db upgrade
qq-deepseek-bot-db inspect-legacy \
  --state-dir /var/lib/qq-deepseek-bot/state \
  --report /tmp/qq-bot-legacy.json
```

不带 `--apply` 的 `migrate-legacy` 也是 dry-run。确认表数和总行数后执行原子回填：

```bash
qq-deepseek-bot-db migrate-legacy \
  --state-dir /var/lib/qq-deepseek-bot/state \
  --apply \
  --report /var/lib/qq-deepseek-bot/postgres-migration.json
```

回填会完成这些工作：

1. 对全部 SQLite 表和 JSON 文件生成 SHA-256 逻辑指纹。
2. 获取 PostgreSQL advisory lock，阻止两次迁移同时运行。
3. 在单个 PostgreSQL 事务中保留原主键回填所有表。
4. 重置 identity sequence，确保新记录不会撞旧 ID。
5. 逐表重新读取 PostgreSQL，比较行数和规范化内容摘要。
6. 只有所有校验通过才提交；任一表不同则整个事务回滚。

再单独复核并启动：

```bash
qq-deepseek-bot-db verify-legacy \
  --state-dir /var/lib/qq-deepseek-bot/state \
  --report /tmp/qq-bot-verify.json
sudo systemctl start qq-deepseek-bot
sudo journalctl -u qq-deepseek-bot -n 100 --no-pager
```

## 回滚

不要删除旧目录。切换后的观察期至少保留
`state.pre-postgres` 7 天。若启动前验证失败，PostgreSQL 事务已经自动回滚；恢复旧
Bot 包并临时设置 `AI_ALLOW_LEGACY_SQLITE=true` 即可读取原目录。不要让 PostgreSQL
版和 SQLite 版同时处理 QQ 消息，否则两边都会继续产生数据，无法无损合并。

## 备份与恢复

当前可写节点每天把 `qq_bot` 备份为带 UTC 时间戳的 PostgreSQL custom-format
文件，生成后立即运行 `pg_restore --list` 做结构检查；只读副本会自动跳过备份。
Tank 最多保留 30 份、30 天，h610 只保留最新 1 份且预留至少 30G 空间。至少再复制
一份到第三台机器或对象存储。恢复演练要落到临时数据库，运行 Alembic revision、
逐表行数和 Bot 只读冒烟检查，不能只看 `pg_dump` 退出码。

当前向量列固定为 `vector(1536)`；若以后更换为其他维度的 embedding 模型，必须新增
Alembic migration 修改列类型，不能只改 `AI_EMBEDDING_DIMENSIONS`。
