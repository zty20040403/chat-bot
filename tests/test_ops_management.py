from __future__ import annotations

import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from src.cluster_control.adapters.ops import OpsClient, OpsError
from src.cluster_control.ops_management import OpsManagementService


class MemoryStore:
    def __init__(self):
        self.records = {}

    def find_operation(self, actor, origin, key):
        return next((r for r in self.records.values() if (r['actor_id'], r['origin_scope'], r['idempotency_key']) == (actor, origin, key)), None)

    def prepare_operation(self, record):
        self.records[record['operation_id']] = copy.deepcopy(record)
        return copy.deepcopy(record)

    def get_operation(self, key):
        return copy.deepcopy(self.records.get(key))

    def approve_operation(self, key, **kwargs):
        record = self.records[key]
        if record['contract_hash'] != kwargs['expected_hash'] or record['resource_version'] != kwargs['expected_version']:
            raise ValueError('Changed intent')
        if record['status'] != 'awaiting_approval':
            raise ValueError('Not awaiting approval')
        record['status'] = 'queued'
        return copy.deepcopy(record)

    def claim_managed_operation(self, owner):
        for record in self.records.values():
            if record['status'] in {'queued', 'running', 'cancelling', 'reconciling'}:
                record['status'] = 'cancelling' if record['status'] == 'cancelling' else 'running'
                record['fence'] = record.get('fence', 0) + 1
                return copy.deepcopy(record)

    def finish_managed_operation(self, key, **kwargs):
        self.records[key].update(status=kwargs['status'], result=kwargs['result'],
            backend_operation_id=kwargs['backend_id'], error_code=kwargs['error'])
        return True


class ManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.token = Path(self.tmp.name) / 'token'
        self.token.write_text('test-credential-' + 'a' * 40)
        self.calls = []
        self.state = 'running'
        self.fail_submission = False
        self.fail_poll = False
        self.definitions = [{
            'name': name, 'read_only': readonly, 'kind': kind,
            'idempotency': 'none' if readonly else 'required',
            'summary': 'Test operation', 'params_schema': {
                'type': 'object', 'properties': {'host': {'type': 'string'}},
                'required': ['host'], 'additionalProperties': False,
            },
        } for name, readonly, kind in [('host.facts', True, 'observation'), ('exec.run', False, 'job_submission')]]
        def handle(request):
            self.calls.append(request)
            if request.method == 'GET':
                return httpx.Response(200, json={'version': 2, 'operations': self.definitions})
            op = json.loads(request.content)['op']
            if op == 'jobs.status':
                if self.fail_poll:
                    raise httpx.ReadTimeout('unavailable', request=request)
                return httpx.Response(200, json={'handle': {'job_id': 'job_test', 'revision': 2, 'state': self.state}})
            if op == 'jobs.cancel':
                return httpx.Response(200, json={'handle': {'job_id': 'job_test', 'revision': 3, 'state': 'running'}})
            if op == 'exec.run':
                if self.fail_submission:
                    raise httpx.ReadTimeout('lost receipt', request=request)
                return httpx.Response(200, json={'job_id': 'job_test', 'revision': 1, 'state': 'queued'})
            return httpx.Response(200, json={'host': 'h610'})
        self.store = MemoryStore()
        client = OpsClient('http://ops.test', self.token, transport=httpx.MockTransport(handle))
        self.manager = OpsManagementService(client, self.store, hosts=('h610', 'h310', 'tank'), actors=('qq:3526452465', 'admin:kenneth'))

    async def asyncTearDown(self):
        await self.manager.close()
        self.tmp.cleanup()

    async def propose(self, key='test-intent-0001', host='h610'):
        return await self.manager.call('exec.run', {'host': host}, actor='qq:3526452465', origin='group:123', idempotency_key=key)

    async def approve(self, proposal):
        r = proposal['operation']
        return await self.manager.approve(r['operation_id'], actor='admin:kenneth', expected_hash=r['contract_hash'], expected_version=r['resource_version'])

    def posts(self, operation):
        return [r for r in self.calls if r.method == 'POST' and json.loads(r.content)['op'] == operation]

    async def test_catalog_and_actor_boundary(self):
        with self.assertRaises(PermissionError):
            await self.manager.catalog('qq:other')
        self.assertEqual(self.calls, [])
        catalog = await self.manager.catalog('admin:kenneth')
        self.assertNotIn('params_schema', catalog['operations'][0])
        detail = await self.manager.catalog('admin:kenneth', 'exec.run')
        self.assertIn('params_schema', detail['operations'][0])

    async def test_reads_direct_and_other_hosts_blocked(self):
        result = await self.manager.call('host.facts', {'host': 'h610'}, actor='admin:kenneth', origin='admin-console')
        self.assertEqual(result['result']['host'], 'h610')
        self.assertFalse(self.store.records)
        with self.assertRaises(PermissionError):
            await self.propose(host='b650')
        self.assertFalse(self.posts('exec.run'))

    async def test_schema_and_unknown_operation_rejected(self):
        with self.assertRaises(ValueError):
            await self.manager.call('exec.run', {'host': 'h610', 'arbitrary': True}, actor='admin:kenneth', origin='admin-console')
        with self.assertRaises(PermissionError):
            await self.manager.call('invented', {}, actor='admin:kenneth', origin='admin-console')
        self.assertFalse(self.posts('exec.run'))

    async def test_deployment_target_uses_the_same_host_grant(self):
        self.definitions.append({
            'name': 'deploy.prepare', 'read_only': False, 'kind': 'job_submission',
            'idempotency': 'required', 'params_schema': {
                'type': 'object', 'properties': {'target_host': {'type': 'string'}},
                'required': ['target_host'], 'additionalProperties': False,
            },
        })
        with self.assertRaises(PermissionError):
            await self.manager.call('deploy.prepare', {'target_host': 'b650'},
                actor='admin:kenneth', origin='admin-console', idempotency_key='deploy-wrong-host')
        self.assertFalse(self.store.records)
        self.assertFalse(self.posts('deploy.prepare'))

    async def test_write_requires_approval_and_runs_once(self):
        proposal = await self.propose()
        self.assertTrue(proposal['approval_required'])
        self.assertFalse(await self.manager.run_once())
        self.assertFalse(self.posts('exec.run'))
        await self.approve(proposal)
        await self.manager.run_once()
        self.assertEqual(self.posts('exec.run')[0].headers['Idempotency-Key'], proposal['operation']['operation_id'])
        await self.manager.run_once()
        self.state = 'succeeded'
        await self.manager.run_once()
        again = await self.propose()
        self.assertTrue(again['executed'])
        self.assertFalse(again['approval_required'])
        self.assertEqual(len(self.posts('exec.run')), 1)

    async def test_same_key_is_bound_to_same_parameters(self):
        first = await self.propose()
        second = await self.propose()
        self.assertEqual(first['operation']['operation_id'], second['operation']['operation_id'])
        with self.assertRaises(ValueError):
            await self.propose(host='tank')

    async def test_rotation_and_schema_change_invalidate_approval(self):
        proposal = await self.propose()
        self.token.write_text('test-credential-' + 'b' * 40)
        with self.assertRaises(PermissionError):
            await self.approve(proposal)
        proposal = await self.propose('test-intent-0002')
        self.definitions[1]['params_schema']['properties']['host']['minLength'] = 1
        with self.assertRaises(PermissionError):
            await self.approve(proposal)
        self.assertFalse(self.posts('exec.run'))

    async def test_missing_receipt_never_replays(self):
        proposal = await self.propose()
        await self.approve(proposal)
        self.fail_submission = True
        await self.manager.run_once()
        self.assertFalse(await self.manager.run_once())
        record = self.store.get_operation(proposal['operation']['operation_id'])
        self.assertEqual(record['status'], 'needs_attention')
        self.assertEqual(len(self.posts('exec.run')), 1)
        self.assertTrue(record['result']['submission_started'])

    async def test_read_only_validation_retries_before_one_submission(self):
        proposal = await self.propose()
        await self.approve(proposal)
        self.store.records[proposal['operation']['operation_id']]['arguments']['deployment'] = {'host_id': 'h610'}
        self.manager.deployment_validator = AsyncMock(side_effect=[
            OpsError('upstream_error', 'HTTP 503 while reading workspace', retryable=True), None,
        ])
        with patch('src.cluster_control.ops_management.asyncio.sleep', new_callable=AsyncMock):
            await self.manager.run_once()
        self.assertEqual(self.manager.deployment_validator.await_count, 2)
        self.assertEqual(len(self.posts('exec.run')), 1)
        self.assertEqual(self.store.get_operation(proposal['operation']['operation_id'])['status'], 'running')

    async def test_validation_outage_is_known_not_submitted(self):
        proposal = await self.propose()
        await self.approve(proposal)
        self.store.records[proposal['operation']['operation_id']]['arguments']['deployment'] = {'host_id': 'h610'}
        self.manager.deployment_validator = AsyncMock(side_effect=OpsError('upstream_error', '503', retryable=True))
        with patch('src.cluster_control.ops_management.asyncio.sleep', new_callable=AsyncMock):
            await self.manager.run_once()
        record = self.store.get_operation(proposal['operation']['operation_id'])
        self.assertEqual(self.manager.deployment_validator.await_count, 3)
        self.assertFalse(self.posts('exec.run'))
        self.assertEqual(record['status'], 'failed')
        self.assertIs(record['result']['submission_started'], False)

    async def test_cancellation_during_validation_prevents_submission(self):
        proposal = await self.propose()
        await self.approve(proposal)
        key = proposal['operation']['operation_id']
        self.store.records[key]['arguments']['deployment'] = {'host_id': 'h610'}
        async def cancelled(_):
            self.store.records[key]['status'] = 'cancelling'
        self.manager.deployment_validator = cancelled
        await self.manager.run_once()
        self.assertFalse(self.posts('exec.run'))
        self.assertIs(self.store.get_operation(key)['result']['submission_started'], False)

    async def test_poll_outage_keeps_remote_job(self):
        proposal = await self.propose()
        await self.approve(proposal)
        await self.manager.run_once()
        self.fail_poll = True
        await self.manager.run_once()
        record = self.store.get_operation(proposal['operation']['operation_id'])
        self.assertEqual(record['status'], 'reconciling')
        self.assertEqual(record['backend_operation_id'], 'job_test')
        self.assertEqual(len(self.posts('exec.run')), 1)

    async def test_cancel_waits_for_target_confirmation(self):
        proposal = await self.propose()
        await self.approve(proposal)
        await self.manager.run_once()
        self.store.records[proposal['operation']['operation_id']]['status'] = 'cancelling'
        await self.manager.run_once()
        self.assertEqual(self.store.get_operation(proposal['operation']['operation_id'])['status'], 'cancelling')
        self.state = 'cancelled'
        await self.manager.run_once()
        self.assertEqual(self.store.get_operation(proposal['operation']['operation_id'])['status'], 'cancelled')

    async def test_admin_api_fails_closed_without_token(self):
        import nonebot
        nonebot.init()
        from fastapi import FastAPI
        from src.plugins.ai_chat.admin import AdminServices, register_admin
        for token, header, expected in [('', '', 503), ('secret', '', 401), ('secret', 'wrong', 401)]:
            app = FastAPI()
            register_admin(app, AdminServices(version='test', started_at=1), token=token)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post('/bot-admin/api/v1/fleet/operations/op_test/approve',
                    headers={'Authorization': f'Bearer {header}'}, json={'contract_hash': 'a' * 64, 'resource_version': 1})
                self.assertEqual(response.status_code, expected)


@unittest.skipUnless(os.getenv('TEST_OPS_POSTGRES_DSN'), 'isolated PostgreSQL test not configured')
class ManagementPostgresTests(unittest.TestCase):
    def test_migration_approval_and_fenced_recovery(self):
        import uuid
        from alembic import command
        from alembic.config import Config
        import psycopg
        from psycopg import sql
        from src.bot_storage.database import PostgresDatabase
        from src.cluster_control.execution_storage import ClusterExecutionStore
        dsn = os.environ['TEST_OPS_POSTGRES_DSN']
        schema = 'gaoji_ops_test_' + uuid.uuid4().hex[:12]
        db = None
        with tempfile.TemporaryDirectory() as root:
            try:
                with patch.dict(os.environ, AI_POSTGRES_DSN=dsn, AI_POSTGRES_SCHEMA=schema):
                    command.upgrade(Config('alembic.ini'), 'head')
                db = PostgresDatabase(dsn, schema=schema, min_size=1, max_size=5)
                store = ClusterExecutionStore(db, Path(root))
                fake = SimpleNamespace(base_url='http://test', _credential=lambda: b'test')
                manager = OpsManagementService(fake, store, hosts=('h610',), actors=('qq:3526452465', 'admin:kenneth'))
                async def definitions():
                    return [{'name': 'exec.run', 'read_only': False, 'kind': 'job_submission', 'idempotency': 'required', 'params_schema': {'type': 'object'}}]
                manager.definitions = definitions
                result = __import__('asyncio').run(manager.call('exec.run', {'host': 'h610'}, actor='qq:3526452465', origin='test', idempotency_key='database-intent'))
                r = result['operation']
                self.assertIsNone(store.claim_managed_operation('worker'))
                store.approve_operation(r['operation_id'], actor_id='admin:kenneth', expected_hash=r['contract_hash'], expected_version=1, expires_at=int(time.time()) + 300)
                claim = store.claim_managed_operation('worker')
                self.assertEqual(claim['attempt'], 1)
                self.assertIsNone(store.claim_managed_operation('other'))
                self.assertFalse(store.finish_managed_operation(r['operation_id'], owner='other', fence=claim['fence'], status='succeeded', result={}, backend_id=None, error=''))
                with psycopg.connect(dsn) as conn:
                    conn.execute(sql.SQL('UPDATE {}.fleet_operations SET lease_expires_at=0 WHERE operation_id=%s').format(sql.Identifier(schema)), (r['operation_id'],))
                self.assertIsNone(store.claim_managed_operation('restarted'))
                self.assertEqual(store.get_operation(r['operation_id'])['status'], 'needs_attention')
            finally:
                if db:
                    db.close()
                with psycopg.connect(dsn) as conn:
                    conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
