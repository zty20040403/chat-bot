// Synthetic fixtures only. No production requests, accounts, or group history.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '../admin-ui/dist');
const now = Math.floor(Date.now() / 1000);
const task = { task_id: 1, handle: 'task#1', trace_id: 'fixture', status: 'completed',
  objective: '制作前后端分离的商城，分别实现商品、购物车和结算，再集成验收。'.repeat(6),
  scope_key: 'onebot-v11:group:123', created_at: now - 60, updated_at: now, finished_at: now,
  result: { execution_state: 'succeeded', validation: { acceptance: { status: 'passed' } }, delivery_state: 'acknowledged' },
  plan: { steps: [{ id: 'frontend', agent: 'coder', depends_on: [] }, { id: 'backend', agent: 'coder', depends_on: [] }, { id: 'integration', agent: 'coder', depends_on: ['frontend', 'backend'] }] } };
const runs = task.plan.steps.map((s, i) => ({ run_id: i + 1, handle: `agent#${i + 1}`, step_key: s.id, role: s.agent, dependencies: s.depends_on,
  objective: task.objective, model_profile: 'gpt-5.6-terra', status: 'succeeded', started_at: now - 40, finished_at: now - 10 }));
runs.push({ ...runs[0], run_id: 4, handle: 'agent#4', step_key: 'acceptance_r1_test', dependencies: ['integration'] });
const roles = [{ role: 'supervisor', title: '主控', allowed_tools: [] }, { role: 'coder', title: '代码', allowed_tools: ['sandbox_exec'] }];
const options = ['qwen-local', 'gpt-5.6-luna', 'gpt-5.6-terra'].map(name => ({ name, model: name, vision: true }));
const detail = { task, runs, control: { version: 1, revision: 1, policy: { mode: 'auto', roles: {} } }, background: true, run_contexts: [], artifact_deliveries: [],
  events: runs.map((r, i) => ({ event_id: i + 1, run_id: r.run_id, event_type: 'agent.model_completed', created_at: now,
    payload: { selected_profile: 'gpt-5.6-terra', actual_profile: 'gpt-5.6-luna', actual_model: 'luna', input_tokens: 12345, output_tokens: 1234 } })) };
let sequence = 0;
const streams = new Set();
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.endsWith('/events')) {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write(`data: ${JSON.stringify({ type: 'ready', sequence: ++sequence, resources: [], timestamp: now })}\n\n`);
    streams.add(res); req.on('close', () => streams.delete(res)); return;
  }
  if (url.pathname.includes('/api/')) {
    const resource = url.pathname.split('/api/v1/')[1];
    const payload = resource === 'subagents' ? { items: [task], roles, model_options: options, configured: true }
      : resource === 'subagents/1' ? detail : resource === 'resource-versions' ? { versions: { subagents: 1 } } : { items: [], counts: {} };
    res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(payload)); return;
  }
  if (url.pathname === '/app.js' || url.pathname === '/app.css') {
    res.writeHead(200, { 'Content-Type': url.pathname.endsWith('.js') ? 'text/javascript' : 'text/css' });
    res.end(fs.readFileSync(path.join(root, url.pathname.slice(1)))); return;
  }
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/app.css"></head><body><div id="root"></div><script type="module" src="/app.js"></script></body></html>');
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE || undefined });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`http://127.0.0.1:${server.address().port}/#tasks`);
    const mode = page.getByLabel('Agent 模型策略');
    await mode.selectOption('locked');
    await page.getByLabel('Agent 指定模型').selectOption('gpt-5.6-luna');
    await page.locator('.agent-revision summary').click();
    await page.getByLabel('追加修改要求').fill('把商品页面改成两列，保留已有接口');
    const dropdown = await mode.elementHandle();
    task.updated_at += 1;
    for (const stream of streams) stream.write(`data: ${JSON.stringify({ type: 'resources.changed', sequence: ++sequence, resources: ['subagents'], timestamp: now + 1 })}\n\n`);
    await page.waitForResponse(response => response.url().endsWith('/subagents/1'));
    assert.equal(await mode.inputValue(), 'locked');
    assert.equal(await page.getByLabel('追加修改要求').inputValue(), '把商品页面改成两列，保留已有接口');
    assert.equal(await mode.evaluate((node, previous) => node === previous, dropdown), true);
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.evaluate(() => scrollTo(0, 0));
      if (width < 700) await page.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().right <= 1);
      await page.screenshot({ path: `/tmp/kennethbot-subagents-${width}.png`, fullPage: true, animations: 'disabled' });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
      assert.equal(overflow, false, `page overflow at ${width}`);
      const nodes = await page.locator('.agent-flow-node').count();
      assert.ok(nodes > 0, 'expected graph nodes');
    }
    assert.deepEqual(errors, []);
    console.log('Desktop/mobile screenshots and SSE draft/DOM persistence passed.');
  } finally {
    await browser.close(); for (const stream of streams) stream.end(); server.close();
  }
})().catch(error => { console.error(error); server.closeAllConnections(); server.close(); process.exitCode = 1; });
