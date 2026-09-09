"""Local, interactive first-account initialization; no passwords in arguments."""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path

from src.bot_storage.database import PostgresDatabase
from src.bot_storage.schema import HEAD_REVISION
from .store import SecurityError, SecurityStore


def read_secret(path: Path) -> bytes:
    if not path.is_file() or path.stat().st_mode & 0o007:
        raise SecurityError("授权密钥必须是不可被其他用户读取的文件")
    secret = path.read_bytes()
    if len(secret) < 32:
        raise SecurityError("授权密钥至少需要 32 字节")
    return secret


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gaoji-admin", description="初始化账户密码登录与 QQ 手机授权")
    parser.add_argument("--env-file", type=Path, help="可选：从私有环境文件读取 PostgreSQL 配置")
    sub = parser.add_subparsers(dest="command", required=True)
    secret_cmd = sub.add_parser("init-secret", help="生成私有授权密钥，不覆盖已有文件")
    secret_cmd.add_argument("path", type=Path)
    account_cmd = sub.add_parser("bootstrap", help="交互创建第一个管理员；已有账户时拒绝")
    account_cmd.add_argument("--secret-file", type=Path)
    account_cmd.add_argument("--username")
    account_cmd.add_argument("--qq-id")
    account_cmd.add_argument("--generate-password-file", type=Path,
        help="部署初始化：随机生成密码，写入新的 0600 文件；不打印密码")
    args = parser.parse_args(argv)
    try:
        if args.command == "init-secret":
            descriptor = os.open(args.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(secrets.token_bytes(32))
            print("授权密钥已生成，权限为 0600。请保留此文件，重启时继续使用。")
            return 0
        if args.env_file:
            from dotenv import load_dotenv
            load_dotenv(args.env_file, override=True)
        dsn = os.environ.get("AI_POSTGRES_DSN", "")
        secret_path = args.secret_file or os.environ.get("AI_ADMIN_SECRET_FILE", "")
        if not dsn or not secret_path:
            raise SecurityError("请提供 AI_POSTGRES_DSN 和 AI_ADMIN_SECRET_FILE（或 --secret-file）")
        secret = read_secret(Path(secret_path))
        if not args.generate_password_file and not sys.stdin.isatty():
            raise SecurityError("请在交互终端运行，密码只允许通过隐藏输入读取")
        if args.generate_password_file:
            if not args.username or not args.qq_id:
                raise SecurityError("生成初始密码时需要 --username 和 --qq-id")
            username, qq = args.username.strip().lower(), args.qq_id.strip()
            SecurityStore.validate_account(username, "admin", qq)
            password = secrets.token_urlsafe(24)
        else:
            username = args.username or input("管理员账户名：").strip()
            qq = args.qq_id or input("绑定管理员 QQ：").strip()
            password = getpass.getpass("密码（12～128 个字符）：")
            if password != getpass.getpass("再次输入密码："):
                raise SecurityError("两次密码不一致")
        database = PostgresDatabase(dsn, schema=os.environ.get("AI_POSTGRES_SCHEMA", "qq_bot"))
        try:
            database.require_revision(HEAD_REVISION)
            store = SecurityStore(database, secret)
            if store.accounts():
                raise SecurityError("已有账户，初始化命令不能覆盖现有管理员")
            if args.generate_password_file:
                descriptor = os.open(args.generate_password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w") as stream:
                    stream.write(password + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            store.bootstrap(username, password, qq)
        finally:
            database.close()
        print("管理员已创建。可使用账户密码登录；管理操作需在绑定 QQ 私聊中输入 6 位口令。")
        return 0
    except (SecurityError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
    except Exception:
        # Database errors can embed a DSN; keep credentials out of terminal logs.
        print("初始化失败，请检查文件权限、数据库连接与迁移版本。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
