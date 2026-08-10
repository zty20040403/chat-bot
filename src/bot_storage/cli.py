from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from .database import PostgresDatabase
from .legacy_migration import (
    capture_legacy_snapshot,
    migrate_legacy_snapshot,
    snapshot_report,
    verify_legacy_snapshot,
)
from .schema import HEAD_REVISION


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qq-deepseek-bot-db",
        description="Manage the bot PostgreSQL schema and legacy data migration.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("upgrade", help="upgrade PostgreSQL to the schema head")
    subcommands.add_parser("current", help="show the current Alembic revision")
    subcommands.add_parser("check", help="check connectivity and schema revision")

    inspect_parser = subcommands.add_parser(
        "inspect-legacy",
        help="inspect SQLite and JSON state without writing PostgreSQL",
    )
    inspect_parser.add_argument("--state-dir", type=Path, required=True)
    inspect_parser.add_argument("--report", type=Path)

    migrate_parser = subcommands.add_parser(
        "migrate-legacy",
        help="atomically import SQLite and JSON state into PostgreSQL",
    )
    migrate_parser.add_argument("--state-dir", type=Path, required=True)
    migrate_parser.add_argument("--apply", action="store_true")
    migrate_parser.add_argument("--resume", action="store_true")
    migrate_parser.add_argument("--report", type=Path)

    verify_parser = subcommands.add_parser(
        "verify-legacy",
        help="compare legacy state with PostgreSQL without writing",
    )
    verify_parser.add_argument("--state-dir", type=Path, required=True)
    verify_parser.add_argument("--report", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "upgrade":
            command.upgrade(_alembic_config(), "head")
            return 0
        if args.command == "current":
            command.current(_alembic_config(), verbose=True)
            return 0
        if args.command == "check":
            database = PostgresDatabase(_dsn(), schema=_schema())
            try:
                database.healthcheck()
                database.require_revision(HEAD_REVISION)
            finally:
                database.close()
            print(f"PostgreSQL ready: schema={_schema()} revision={HEAD_REVISION}")
            return 0

        snapshot = capture_legacy_snapshot(args.state_dir)
        if args.command == "inspect-legacy":
            report = snapshot_report(snapshot)
        elif args.command == "migrate-legacy":
            if not args.apply:
                report = snapshot_report(snapshot)
                report["dry_run"] = True
            else:
                report = migrate_legacy_snapshot(
                    snapshot,
                    _dsn(),
                    schema=_schema(),
                    resume=args.resume,
                )
        elif args.command == "verify-legacy":
            report = verify_legacy_snapshot(snapshot, _dsn(), schema=_schema())
        else:
            parser.error("unknown command")
            return 2
        _emit_report(report, args.report)
        return 0 if report.get("verified", True) else 1
    except Exception as exc:
        print(f"database command failed: {exc}", file=sys.stderr)
        return 1


def _alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _dsn() -> str:
    value = os.getenv("AI_POSTGRES_DSN", "").strip()
    if not value:
        raise RuntimeError("AI_POSTGRES_DSN is required")
    return value


def _schema() -> str:
    return os.getenv("AI_POSTGRES_SCHEMA", "qq_bot").strip() or "qq_bot"


def _emit_report(report: dict[str, object], path: Path | None) -> None:
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(encoded)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(encoded + "\n", encoding="utf-8")
        temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
