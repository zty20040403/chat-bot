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
        assert.equal(body.contract_hash, operation.contract_hash);
        assert.equal(body.resource_version, 1);
        approved++; operation.status = 'queued'; operation.updated_at++;
        res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(operation));
      }); return;
    }
    const payload = resource === 'fleet' ? { configured: true, fleet: { inventory: [] },
      execution_capabilities: { ops_management: { available: true, hosts: ['h310', 'h610', 'tank'] } }, operations: { items: [operation] } }
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
    assert.deepEqual(errors, []);
    console.log('Desktop/mobile approval layout, stable SSE review and exact approval binding passed.');
  } finally {
    await browser.close();
    for (const stream of streams) stream.end();
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
