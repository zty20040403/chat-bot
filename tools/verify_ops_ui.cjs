// Synthetic approval fixture; never contacts production or executes commands.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../admin-ui/dist');
const operation = { operation_id: 'op_fixture', status: 'awaiting_approval', host_id: 'h610',
  actor_id: 'qq:3526452465', contract_hash: 'a'.repeat(64), resource_version: 1,
  resource_ref: 'exec.run', operation: 'maxops.execute', updated_at: 1788771000,
  arguments: { op: 'exec.run', params: { host: 'h610', profile: 'operator',
    command: { argv: ['/run/current-system/sw/bin/printf', 'LONG_PARAMETER_'.repeat(60)] } } }, events: [] };
let approved = 0;
let guardianApproved = 0;
const guardianTarget = { target_id: 'tank-worker', host_id: 'tank',
  service_ref: 'gaoji-cluster-worker.service', target_hash: 'b'.repeat(64) };
const streams = new Set();
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.endsWith('/events')) {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write(`data: ${JSON.stringify({ type: 'ready', sequence: 1, resources: [], timestamp: Date.now() / 1000 })}\n\n`);
    streams.add(res); req.on('close', () => streams.delete(res)); return;
  }
  if (url.pathname.includes('/api/')) {
    const resource = url.pathname.split('/api/v1/')[1];
    if (req.method === 'POST') {
      let raw = ''; req.on('data', c => raw += c); req.on('end', () => {
        const body = JSON.parse(raw);
        if (resource === 'fleet/guardians') {
          assert.equal(body.confirm_remediation, true);
          assert.equal(body.expected_target_hash, guardianTarget.target_hash);
          assert.equal(body.authorized_action.host_id, 'tank');
          assert.equal(body.authorized_action.resource_ref, guardianTarget.service_ref);
          assert.equal(body.max_actions, 2);
          guardianApproved++;
          res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ guardian_id: 'guardian_fixture', status: 'scheduled' })); return;
        }
        assert.equal(body.contract_hash, operation.contract_hash);
        assert.equal(body.resource_version, 1);
        approved++; operation.status = 'queued'; operation.updated_at++;
        res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(operation));
      }); return;
    }
    const payload = resource === 'fleet' ? { configured: true, fleet: { inventory: [] },
      execution_capabilities: { ops_management: { available: true, hosts: ['h310', 'h610', 'tank'] },
        guardians: { targets: [guardianTarget.target_id], target_details: [guardianTarget], remediation_available: true } }, operations: { items: [operation] } }
      : resource === 'fleet/operations/op_fixture' ? operation
      : resource === 'fleet/ops/catalog' ? { operations: [{ name: 'exec.run', read_only: false }] }
      : resource === 'resource-versions' ? { versions: {} } : { items: [], counts: {} };
    res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(payload)); return;
  }
  if (url.pathname === '/app.js' || url.pathname === '/app.css') {
    res.setHeader('Content-Type', url.pathname.endsWith('.js') ? 'text/javascript' : 'text/css');
    res.end(fs.readFileSync(path.join(root, url.pathname.slice(1)))); return;
  }
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"></head><body><div id="root"></div><script type="module" src="/app.js"></script></body></html>');
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE || undefined });
  try {
    const page = await browser.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/#fleet`);
    await page.getByRole('button', { name: '审阅 op_fixture' }).click();
    const dialog = page.getByRole('dialog', { name: '服务器操作审阅' });
    await dialog.waitFor();
    assert.equal(await dialog.getByRole('button', { name: '批准执行' }).isEnabled(), false);
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.screenshot({ path: `/tmp/gaoji-ops-${width}.png`, animations: 'disabled' });
      const bounds = await dialog.boundingBox();
      assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width);
      assert.equal(await dialog.evaluate(el => el.scrollWidth > el.clientWidth + 1), false);
    }
    await dialog.getByRole('checkbox').check();
    for (const stream of streams) stream.write(`data: ${JSON.stringify({ type: 'resources.changed', sequence: 2, resources: ['fleet'], timestamp: Date.now() / 1000 })}\n\n`);
    await page.waitForResponse(response => response.url().endsWith('/fleet'));
    assert.equal(await dialog.getByRole('checkbox').isChecked(), true);
    await dialog.getByRole('button', { name: '批准执行' }).click();
    await page.waitForResponse(response => response.url().endsWith('/approve'));
    assert.equal(approved, 1);
    await dialog.getByRole('button', { name: '关闭审阅' }).click();
    await page.getByLabel('守护目标', { exact: true }).selectOption('tank-worker');
    await page.getByLabel('守护模式', { exact: true }).selectOption('remediate');
    await page.getByLabel('修复次数', { exact: true }).fill('2');
    await page.getByRole('button', { name: '审阅修复授权' }).click();
    const guardianDialog = page.getByRole('dialog', { name: '有限修复授权' });
    await guardianDialog.waitFor();
    assert.equal(await guardianDialog.getByRole('button', { name: '确认授权' }).isEnabled(), false);
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.screenshot({ path: `/tmp/gaoji-guardian-${width}.png`, animations: 'disabled' });
      const bounds = await guardianDialog.boundingBox();
      assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width);
      assert.equal(await guardianDialog.evaluate(el => el.scrollWidth > el.clientWidth + 1), false);
    }
    await guardianDialog.getByRole('checkbox').check();
    for (const stream of streams) stream.write(`data: ${JSON.stringify({ type: 'resources.changed', sequence: 3, resources: ['fleet'], timestamp: Date.now() / 1000 })}\n\n`);
    await page.waitForResponse(response => response.url().endsWith('/fleet'));
    assert.equal(await guardianDialog.getByRole('checkbox').isChecked(), true);
    await guardianDialog.getByRole('button', { name: '确认授权' }).click();
    await page.waitForResponse(response => response.url().endsWith('/guardians'));
    assert.equal(guardianApproved, 1);
    assert.deepEqual(errors, []);
    console.log('Desktop/mobile operation and guardian reviews, stable SSE state and exact approvals passed.');
  } finally {
    await browser.close();
    for (const stream of streams) stream.end();
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
