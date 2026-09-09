"""Run against an explicitly supplied, isolated PostgreSQL instance."""
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql

from src.bot_security.store import SecurityError, SecurityStore
from src.bot_storage.database import PostgresDatabase
from src.bot_storage.schema import HEAD_REVISION
from tests.test_account_security import PASSWORD, QQ, BOT


@unittest.skipUnless(os.getenv("TEST_SECURITY_POSTGRES_DSN"), "isolated PostgreSQL not configured")
class SecurityPostgresTests(unittest.TestCase):
    def test_migration_atomic_confirmation_rollback_and_restart(self):
        dsn = os.environ["TEST_SECURITY_POSTGRES_DSN"]
        schema = "gaoji_security_test_" + uuid.uuid4().hex[:12]
        databases = []
        try:
            with patch.dict(os.environ, AI_POSTGRES_DSN=dsn, AI_POSTGRES_SCHEMA=schema):
                command.upgrade(Config("alembic.ini"), "0025_ops_management")
                command.upgrade(Config("alembic.ini"), "head")
                command.upgrade(Config("alembic.ini"), "head")
            databases = [PostgresDatabase(dsn, schema=schema, min_size=1, max_size=6) for _ in range(2)]
            databases[0].require_revision(HEAD_REVISION)
            first, second = [SecurityStore(db, b"test-secret" * 4) for db in databases]
            account = first.bootstrap("kenneth", PASSWORD, QQ)
            login = second.login("kenneth", PASSWORD, "test")
            session = first.session(login["session"], login["csrf"])
            request, code = first.propose(session, bot_id=BOT, kind="test", payload={"unit": "测试.service"}, summary="重启测试服务")
            first.delivered(request["approval_id"], 1, True)
            def confirm(index):
                try:
                    (first if index % 2 else second).confirm(request["approval_id"], QQ, BOT, code)
                    return True
                except SecurityError:
                    return False
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(sum(pool.map(confirm, range(12))), 1)
            with ThreadPoolExecutor(max_workers=2) as pool:
                claimed = list(pool.map(lambda i: (first if i else second).claim(str(i)), range(2)))
            self.assertEqual(sum(item is not None for item in claimed), 1)
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with first.transaction() as cursor:
                    cursor.execute("UPDATE admin_accounts SET enabled=0")
                    raise RuntimeError("rollback")
            self.assertIsNotNone(second.account_for_qq(QQ))
            with first.transaction() as cursor:
                cursor.execute("UPDATE admin_approvals SET lease_until=0 WHERE approval_id=?", (request["approval_id"],))
            self.assertIsNone(second.claim("restarted"))
            self.assertEqual(second.get(request["approval_id"], account["account_id"])["status"], "needs_attention")
            with self.assertRaises(SecurityError):
                first.confirm(request["approval_id"], QQ, BOT, code)
            self.assertNotIn(code, str(first.audit()))
        finally:
            for database in databases:
                database.close()
            with psycopg.connect(dsn) as connection:
                connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
