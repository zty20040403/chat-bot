# Bot PostgreSQL 迁移手册

## 最终结构

```text
QQ / NapCat
    |
    v
h610: qq-deepseek-bot
    |
    | Tailscale 100.64.0.3 -> 100.64.0.4:5432
    v
Tank: PostgreSQL + pgvector
      /data/lib/postgresql/...
      每日 pg_dump + Tank 文件系统备份
```

Tank 是唯一权威数据源。h610 不再运行 SQLite，也不保存消息、记忆、提醒或工具
轨迹的权威副本。浏览器 profile、沙盒工作目录和下载缓存仍留在 h610，因为它们是
主机相关或可重建数据。

PostgreSQL 只允许 `qq_bot` 用户从 h610 的 Tailscale 地址连接。密码放在 sops 和
systemd `EnvironmentFile`，不能写进 Git、Nix store 或命令行历史。

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

Tank 每天把 `qq_bot` 备份为带 UTC 时间戳的 PostgreSQL custom-format 文件，生成后
立即运行 `pg_restore --list` 做结构检查，并自动删除超过 30 天的日备份。至少再复制
一份到 Tank 之外。恢复演练要落到临时数据库，运行 Alembic revision、逐表行数和
Bot 只读冒烟检查，不能只看 `pg_dump` 退出码。

当前向量列固定为 `vector(1536)`；若以后更换为其他维度的 embedding 模型，必须新增
Alembic migration 修改列类型，不能只改 `AI_EMBEDDING_DIMENSIONS`。
