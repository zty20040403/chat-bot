from __future__ import annotations

import json
from html import escape


def dashboard_html(prefix: str, version: str, requires_token: bool) -> str:
    encoded_prefix = (
        json.dumps(prefix)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return (
        _DASHBOARD_TEMPLATE.replace("__DASHBOARD_CSS__", _DASHBOARD_CSS)
        .replace("__DASHBOARD_SCRIPT__", _DASHBOARD_SCRIPT)
        .replace("__ADMIN_PREFIX__", encoded_prefix)
        .replace("__BOT_VERSION__", escape(version))
        .replace("__TOKEN_REQUIRED__", "true" if requires_token else "false")
    )


_DASHBOARD_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>QQ Bot Control</title>
  <style>__DASHBOARD_CSS__</style>
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar" id="sidebar">
      <div class="brand">
        <span class="brand-mark" data-icon="bot"></span>
        <div><strong>QQ Bot</strong><span>Control Center</span></div>
      </div>

      <button class="quick-refresh" id="sidebar-refresh" type="button">
        <span data-icon="refresh"></span><span>刷新数据</span>
      </button>

      <nav class="side-nav" aria-label="管理导航">
        <div class="nav-group">
          <span class="nav-label">运行</span>
          <button class="nav-item active" type="button" data-view="overview"
            data-title="运行概览" data-subtitle="实时状态与模型消耗">
            <span data-icon="dashboard"></span><span>概览</span>
          </button>
          <button class="nav-item" type="button" data-view="tasks"
            data-title="运行任务" data-subtitle="正在处理的模型请求">
            <span data-icon="activity"></span><span>任务</span><b id="nav-task-count">0</b>
          </button>
          <button class="nav-item" type="button" data-view="deliveries"
            data-title="消息投递" data-subtitle="发送状态与失败重试">
            <span data-icon="send"></span><span>投递</span><b id="nav-delivery-count">0</b>
          </button>
          <button class="nav-item" type="button" data-view="usage"
            data-title="模型用量" data-subtitle="调用次数与 Token 消耗">
            <span data-icon="chart"></span><span>用量</span>
          </button>
          <button class="nav-item" type="button" data-view="context-plans"
            data-title="上下文决策" data-subtitle="群聊焦点、候选评分与隔离范围">
            <span data-icon="focus"></span><span>上下文</span>
          </button>
        </div>

        <div class="nav-group">
          <span class="nav-label">资源</span>
          <button class="nav-item" type="button" data-view="sandboxes"
            data-title="沙盒" data-subtitle="容器、任务与工作区文件">
            <span data-icon="box"></span><span>沙盒</span><b id="nav-sandbox-count">0</b>
          </button>
          <button class="nav-item" type="button" data-view="stickers"
            data-title="表情包" data-subtitle="本地资源与 QQ 学习库存">
            <span data-icon="smile"></span><span>表情包</span><b id="nav-sticker-count">0</b>
          </button>
          <button class="nav-item" type="button" data-view="media"
            data-title="媒体库" data-subtitle="图片识别、存储与后台任务">
            <span data-icon="image"></span><span>媒体库</span><b id="nav-media-count">0</b>
          </button>
          <button class="nav-item" type="button" data-view="group-models"
            data-title="群模型" data-subtitle="各群默认配置与用户覆盖">
            <span data-icon="users"></span><span>群模型</span>
          </button>
          <button class="nav-item" type="button" data-view="models"
            data-title="模型配置" data-subtitle="Provider、能力与可用状态">
            <span data-icon="cpu"></span><span>模型配置</span>
          </button>
        </div>
      </nav>

      <div class="sidebar-foot">
        <div class="node-state">
          <span class="health-dot" id="health-dot"></span>
          <div><strong id="health-label">正在连接</strong><span>OneBot V11</span></div>
        </div>
        <span class="version">v__BOT_VERSION__</span>
      </div>
    </aside>

    <button class="sidebar-overlay" id="sidebar-overlay" type="button"
      aria-label="关闭导航"></button>

    <div class="workspace">
      <header class="topbar">
        <button class="icon-button menu-button" id="menu-button" type="button"
          title="打开导航" aria-label="打开导航"><span data-icon="menu"></span></button>
        <div class="page-heading">
          <h1 id="page-title">运行概览</h1>
          <p id="page-subtitle">实时状态与模型消耗</p>
        </div>
        <div class="topbar-actions">
          <label class="token-control" id="token-wrap">
            <span data-icon="key"></span>
            <input id="token" type="password" autocomplete="off" placeholder="管理 Token"
              aria-label="管理 Token">
          </label>
          <div class="update-state"><span data-icon="clock"></span><span id="last-updated">尚未刷新</span></div>
          <button class="icon-button" id="top-refresh" type="button"
            title="刷新数据" aria-label="刷新数据"><span data-icon="refresh"></span></button>
        </div>
      </header>

      <main>
        <div class="error-banner" id="error" hidden></div>

        <section class="view" id="overview">
          <div class="metrics">
            <article class="metric-card">
              <div class="metric-top"><span>运行任务</span><span class="metric-tag">实时</span></div>
              <strong id="metric-tasks">--</strong>
              <div class="metric-foot"><span class="trend-icon" data-icon="activity"></span><span id="metric-tasks-note">等待数据</span></div>
            </article>
            <article class="metric-card">
              <div class="metric-top"><span>沙盒活动</span><span class="metric-tag">Docker</span></div>
              <strong id="metric-sandbox">--</strong>
              <div class="metric-foot"><span class="trend-icon" data-icon="box"></span><span id="metric-sandbox-note">等待数据</span></div>
            </article>
            <article class="metric-card">
              <div class="metric-top"><span>表情库存</span><span class="metric-tag">资源</span></div>
              <strong id="metric-stickers">--</strong>
              <div class="metric-foot"><span class="trend-icon" data-icon="smile"></span><span id="metric-stickers-note">等待数据</span></div>
            </article>
            <article class="metric-card">
              <div class="metric-top"><span>已启用群</span><span class="metric-tag">模型</span></div>
              <strong id="metric-groups">--</strong>
              <div class="metric-foot"><span class="trend-icon" data-icon="users"></span><span id="metric-groups-note">等待数据</span></div>
            </article>
          </div>

          <div class="chart-panel">
            <div class="panel-heading">
              <div><h2>模型调用趋势</h2><p id="usage-summary">等待用量数据</p></div>
              <div class="segmented" aria-label="用量统计周期">
                <button type="button" data-days="90">90 天</button>
                <button type="button" class="active" data-days="30">30 天</button>
                <button type="button" data-days="14">14 天</button>
              </div>
            </div>
            <div class="chart-wrap">
              <canvas id="usage-chart" aria-label="模型调用趋势图"></canvas>
              <div class="chart-empty" id="chart-empty" hidden>暂无用量数据</div>
            </div>
            <div class="chart-legend">
              <span><i class="legend-line solid"></i>Token</span>
              <span><i class="legend-line dashed"></i>调用次数（独立刻度）</span>
            </div>
          </div>

          <div class="section-heading overview-table-heading">
            <div><h2>最近投递</h2><p>最新消息发送状态</p></div>
            <button class="secondary-button" type="button" data-open-view="deliveries">
              查看全部<span data-icon="chevron-right"></span>
            </button>
          </div>
          <div class="table-wrap">
            <table><thead><tr>
              <th>ID</th><th>目标</th><th>状态</th><th>尝试</th><th>更新时间</th>
            </tr></thead><tbody id="recent-delivery-body"></tbody></table>
          </div>
        </section>

        <section class="view" id="tasks" hidden>
          <div class="section-heading"><div><h2>运行任务</h2><p>当前 Agent Loop 和模型请求</p></div><span class="count-label" id="task-count">0 条</span></div>
          <div class="table-wrap"><table><thead><tr>
            <th>任务</th><th>会话</th><th>摘要</th><th>耗时</th><th>操作</th>
          </tr></thead><tbody id="task-body"></tbody></table></div>
        </section>

        <section class="view" id="deliveries" hidden>
          <div class="section-heading"><div><h2>消息投递</h2><p>最近 100 条投递记录</p></div><span class="count-label" id="delivery-heading-count">0 条</span></div>
          <div class="table-wrap"><table><thead><tr>
            <th>ID</th><th>目标</th><th>状态</th><th>尝试</th><th>更新时间</th><th>操作</th>
          </tr></thead><tbody id="delivery-body"></tbody></table></div>
          <div class="table-footer" data-pager-wrap="deliveries"></div>
        </section>

        <section class="view" id="usage" hidden>
          <div class="section-heading"><div><h2>模型用量</h2><p>按日期、会话与来源统计</p></div><span class="count-label" id="usage-count">0 条</span></div>
          <div class="table-wrap"><table><thead><tr>
            <th>日期</th><th>Scope</th><th>来源</th><th>调用</th><th>输入 Token</th><th>输出 Token</th>
          </tr></thead><tbody id="usage-body"></tbody></table></div>
          <div class="table-footer" data-pager-wrap="usage"></div>
        </section>

        <section class="view" id="context-plans" hidden>
          <div class="section-heading"><div><h2>上下文决策</h2><p>每轮使用的群聊焦点与可解释评分</p></div><span class="count-label" id="context-plan-count">0 条</span></div>
          <div class="table-wrap"><table class="wide-table"><thead><tr>
            <th>回合</th><th>群 Scope</th><th>当前 / 焦点消息</th><th>置信度</th><th>判断依据</th><th>候选</th><th>时间</th>
          </tr></thead><tbody id="context-plan-body"></tbody></table></div>
          <div class="table-footer" data-pager-wrap="contextPlans"></div>
        </section>

        <section class="view" id="sandboxes" hidden>
          <div class="section-heading"><div><h2>沙盒</h2><p>容器资源、活动任务与工作区</p></div><span class="count-label" id="sandbox-count">0 个</span></div>
          <div class="table-wrap"><table class="wide-table"><thead><tr>
            <th>沙盒</th><th>所属会话</th><th>状态</th><th>当前或最近任务</th><th>资源</th><th>工作区</th>
          </tr></thead><tbody id="sandbox-body"></tbody></table></div>
        </section>

        <section class="view" id="stickers" hidden>
          <div class="section-heading"><div><h2>表情包</h2><p>机器人本地资源与 QQ 学习库存</p></div><span class="count-label" id="sticker-count">0 个</span></div>
          <div class="table-wrap"><table><thead><tr>
            <th>来源</th><th>类型</th><th>名称</th><th>保存的引用</th><th>大小</th>
          </tr></thead><tbody id="sticker-body"></tbody></table></div>
          <div class="table-footer" data-pager-wrap="stickers"></div>
        </section>

        <section class="view" id="media" hidden>
          <div class="section-heading"><div><h2>媒体库</h2><p>h610 Blob、Luna 识图与任务队列</p></div><span class="count-label" id="media-count">0 张</span></div>
          <div class="table-wrap"><table class="wide-table"><thead><tr>
            <th>Media</th><th>识图标签</th><th>类型 / 大小</th><th>安全状态</th><th>视觉模型</th><th>发送</th>
          </tr></thead><tbody id="media-body"></tbody></table></div>
          <div class="table-footer" data-pager-wrap="media"></div>
          <div class="section-heading compact-heading"><div><h2>后台任务</h2><p>等待、运行和最终失败任务</p></div><span class="count-label" id="media-job-count">0 条</span></div>
          <div class="table-wrap"><table><thead><tr>
            <th>任务</th><th>类型</th><th>状态</th><th>尝试</th><th>错误</th><th>更新时间</th>
          </tr></thead><tbody id="media-job-body"></tbody></table></div>
        </section>

        <section class="view" id="group-models" hidden>
          <div class="section-heading"><div><h2>群模型</h2><p>群级默认配置与群友单独选择</p></div><span class="count-label" id="group-count">0 个群</span></div>
          <div class="table-wrap"><table><thead><tr>
            <th>群号</th><th>启用状态</th><th>默认配置</th><th>默认底层模型</th><th>群友单独选择</th>
          </tr></thead><tbody id="group-model-body"></tbody></table></div>
        </section>

        <section class="view" id="models" hidden>
          <div class="section-heading"><div><h2>模型配置</h2><p>Provider、协议与模型能力</p></div><span class="count-label" id="model-count">0 个</span></div>
          <div class="table-wrap"><table><thead><tr>
            <th>Profile</th><th>Provider</th><th>协议</th><th>模型</th><th>能力</th><th>状态</th>
          </tr></thead><tbody id="model-body"></tbody></table></div>
        </section>
      </main>
    </div>
  </div>
  <script>__DASHBOARD_SCRIPT__</script>
</body>
</html>
"""


_DASHBOARD_CSS = r"""
:root {
  color-scheme: light;
  --background: #f7f7f8;
  --surface: #ffffff;
  --sidebar: #fbfbfc;
  --foreground: #18181b;
  --muted-foreground: #71717a;
  --border: #e4e4e7;
  --border-strong: #d4d4d8;
  --muted: #f4f4f5;
  --accent: #efeff1;
  --primary: #18181b;
  --primary-foreground: #fafafa;
  --green: #16a34a;
  --green-soft: #ecfdf3;
  --amber: #a16207;
  --amber-soft: #fffbeb;
  --red: #dc2626;
  --red-soft: #fef2f2;
  --blue: #2563eb;
  --blue-soft: #eff6ff;
  --radius: 8px;
  --sidebar-width: 252px;
  --topbar-height: 74px;
}

* { box-sizing: border-box; }

html { min-width: 320px; background: var(--background); }

body {
  margin: 0;
  color: var(--foreground);
  background: var(--background);
  font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
    "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  letter-spacing: 0;
  -webkit-font-smoothing: antialiased;
}

button, input, select { font: inherit; letter-spacing: 0; }
button { color: inherit; }
button:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible {
  outline: 2px solid #18181b;
  outline-offset: 2px;
}

.app-shell {
  display: grid;
  grid-template-columns: var(--sidebar-width) minmax(0, 1fr);
  min-height: 100vh;
}

.sidebar {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  flex-direction: column;
  width: var(--sidebar-width);
  height: 100vh;
  padding: 18px 14px 14px;
  overflow-y: auto;
  background: var(--sidebar);
  border-right: 1px solid var(--border);
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  min-height: 42px;
  padding: 0 8px;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  flex: 0 0 30px;
  color: var(--primary-foreground);
  background: var(--primary);
  border-radius: 50%;
}

.brand-mark .icon { width: 17px; height: 17px; }
.brand div { display: grid; min-width: 0; }
.brand strong { font-size: 15px; font-weight: 650; }
.brand span:last-child { color: var(--muted-foreground); font-size: 11px; }

.quick-refresh {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  height: 36px;
  margin: 16px 0 9px;
  padding: 0 12px;
  color: var(--primary-foreground);
  background: var(--primary);
  border: 1px solid var(--primary);
  border-radius: 7px;
  cursor: pointer;
}

.quick-refresh:hover { background: #27272a; }
.quick-refresh:disabled { cursor: wait; opacity: .68; }

.side-nav { display: grid; gap: 18px; }
.nav-group { display: grid; gap: 3px; }

.nav-label {
  padding: 8px 10px 4px;
  color: var(--muted-foreground);
  font-size: 11px;
  font-weight: 600;
}

.nav-item {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  width: 100%;
  min-height: 36px;
  padding: 7px 10px;
  color: #3f3f46;
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: 6px;
  cursor: pointer;
}

.nav-item:hover { color: var(--foreground); background: var(--muted); }
.nav-item.active { color: var(--foreground); background: #ececee; font-weight: 600; }
.nav-item b {
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  color: #52525b;
  font-size: 11px;
  font-weight: 600;
  line-height: 20px;
  text-align: center;
  background: #dedee1;
  border-radius: 10px;
}

.sidebar-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: auto;
  padding: 14px 8px 4px;
  border-top: 1px solid var(--border);
}

.node-state { display: flex; align-items: center; gap: 9px; min-width: 0; }
.node-state div { display: grid; min-width: 0; }
.node-state strong { font-size: 12px; font-weight: 600; }
.node-state span:last-child, .version { color: var(--muted-foreground); font-size: 11px; }

.health-dot {
  width: 8px;
  height: 8px;
  flex: 0 0 8px;
  background: #a1a1aa;
  border-radius: 50%;
  box-shadow: 0 0 0 3px #e4e4e7;
}

.health-dot.online { background: var(--green); box-shadow: 0 0 0 3px #dcfce7; }
.health-dot.error { background: var(--red); box-shadow: 0 0 0 3px #fee2e2; }

.sidebar-overlay { display: none; }
.workspace { min-width: 0; }

.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: var(--topbar-height);
  padding: 12px 24px;
  background: rgba(255, 255, 255, .96);
  border-bottom: 1px solid var(--border);
}

.page-heading { min-width: 0; }
.page-heading h1 { margin: 0; font-size: 18px; font-weight: 650; }
.page-heading p { margin: 1px 0 0; color: var(--muted-foreground); font-size: 12px; }
.topbar-actions { display: flex; align-items: center; gap: 10px; margin-left: auto; }

.icon-button {
  display: inline-grid;
  place-items: center;
  width: 36px;
  height: 36px;
  flex: 0 0 36px;
  padding: 0;
  color: #52525b;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}

.icon-button:hover { color: var(--foreground); border-color: var(--border-strong); background: var(--muted); }
.icon-button:disabled { cursor: wait; opacity: .58; }
.menu-button { display: none; }

.update-state {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--muted-foreground);
  font-size: 12px;
  white-space: nowrap;
}

.token-control {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 210px;
  height: 36px;
  padding: 0 9px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
}

.token-control[hidden] { display: none; }
.token-control input { width: 100%; min-width: 0; padding: 0; outline: 0; border: 0; background: transparent; }

main { width: 100%; max-width: 1500px; margin: 0 auto; padding: 24px; }
.view[hidden] { display: none; }

.error-banner {
  margin-bottom: 16px;
  padding: 10px 12px;
  color: #991b1b;
  background: var(--red-soft);
  border: 1px solid #fecaca;
  border-radius: 6px;
}

.metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 20px;
}

.metric-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 164px;
  padding: 18px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: 0 1px 2px rgba(0, 0, 0, .03);
}

.metric-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--muted-foreground); }
.metric-tag {
  padding: 2px 8px;
  color: #3f3f46;
  font-size: 11px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
}

.metric-card > strong {
  display: block;
  min-height: 42px;
  margin-top: 8px;
  font-size: 29px;
  font-weight: 690;
  line-height: 1.35;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

.metric-foot { display: flex; align-items: center; gap: 7px; margin-top: auto; color: #52525b; font-size: 12px; }
.trend-icon { display: grid; place-items: center; color: var(--foreground); }

.chart-panel {
  margin-bottom: 22px;
  padding: 20px 20px 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: 0 1px 2px rgba(0, 0, 0, .03);
}

.panel-heading, .section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.panel-heading h2, .section-heading h2 { margin: 0; font-size: 15px; font-weight: 650; }
.panel-heading p, .section-heading p { margin: 3px 0 0; color: var(--muted-foreground); font-size: 12px; }

.segmented {
  display: grid;
  grid-auto-flow: column;
  padding: 3px;
  background: var(--muted);
  border-radius: 7px;
}

.segmented button {
  height: 31px;
  padding: 0 14px;
  color: var(--muted-foreground);
  background: transparent;
  border: 0;
  border-radius: 5px;
  cursor: pointer;
  white-space: nowrap;
}

.segmented button:hover { color: var(--foreground); }
.segmented button.active { color: var(--foreground); background: var(--surface); box-shadow: 0 1px 2px rgba(0, 0, 0, .08); }

.chart-wrap { position: relative; width: 100%; height: 292px; margin-top: 14px; }
#usage-chart { display: block; width: 100%; height: 100%; }
.chart-empty { position: absolute; inset: 0; display: grid; place-items: center; color: var(--muted-foreground); }
.chart-empty[hidden] { display: none; }
.chart-legend { display: flex; justify-content: flex-end; gap: 16px; color: var(--muted-foreground); font-size: 11px; }
.chart-legend span { display: flex; align-items: center; gap: 6px; }
.legend-line { display: inline-block; width: 22px; height: 0; border-top: 2px solid #27272a; }
.legend-line.dashed { border-top-color: #71717a; border-top-style: dashed; }

.section-heading { min-height: 50px; margin-bottom: 10px; }
.overview-table-heading { margin-top: 0; }
.count-label { color: var(--muted-foreground); font-size: 12px; white-space: nowrap; }

.secondary-button, .table-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 34px;
  padding: 0 11px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
}

.secondary-button:hover, .table-action:hover { border-color: var(--border-strong); background: var(--muted); }

.table-wrap {
  width: 100%;
  overflow: auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: 0 1px 2px rgba(0, 0, 0, .02);
}

table { width: 100%; min-width: 760px; border-collapse: collapse; }
.wide-table { min-width: 1080px; }
th, td { padding: 11px 13px; text-align: left; vertical-align: middle; border-bottom: 1px solid var(--border); }
th { color: var(--muted-foreground); background: #f4f4f5; font-size: 12px; font-weight: 550; white-space: nowrap; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:hover { background: #fafafa; }

code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}

.status-badge, .capability {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 24px;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  border: 1px solid var(--border);
  border-radius: 999px;
}

.status-badge::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: #a1a1aa; }
.status-badge.success { color: #166534; background: var(--green-soft); border-color: #bbf7d0; }
.status-badge.success::before { background: var(--green); }
.status-badge.warning { color: #854d0e; background: var(--amber-soft); border-color: #fde68a; }
.status-badge.warning::before { background: #d97706; }
.status-badge.danger { color: #991b1b; background: var(--red-soft); border-color: #fecaca; }
.status-badge.danger::before { background: var(--red); }
.status-badge.info { color: #1d4ed8; background: var(--blue-soft); border-color: #bfdbfe; }
.status-badge.info::before { background: var(--blue); }

.capability-list { display: flex; flex-wrap: wrap; gap: 5px; }
.capability { color: #52525b; background: var(--muted); font-weight: 500; }
.muted { color: var(--muted-foreground); }
.subtle { color: var(--muted-foreground); font-size: 12px; }
.stack { display: grid; gap: 4px; min-width: 160px; }
.command { max-width: 360px; white-space: pre-wrap; overflow-wrap: anywhere; }
.truncate-cell { display: block; max-width: 340px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty { padding: 34px 14px; color: var(--muted-foreground); text-align: center; }

details summary { color: #3f3f46; cursor: pointer; white-space: nowrap; }
.file-list { display: grid; gap: 4px; max-width: 340px; margin-top: 8px; }
.file-list code { overflow-wrap: anywhere; }

.table-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  min-height: 52px;
  color: var(--muted-foreground);
  font-size: 12px;
}

.pager-controls { display: flex; align-items: center; gap: 8px; }
.pager-controls label { display: flex; align-items: center; gap: 7px; white-space: nowrap; }
.pager-controls select { height: 32px; padding: 0 26px 0 9px; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; }
.pager-buttons { display: flex; gap: 5px; }
.pager-buttons .icon-button { width: 32px; height: 32px; flex-basis: 32px; }

.icon { display: block; width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.is-loading [data-icon="refresh"] .icon { animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 1120px) {
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric-card { min-height: 150px; }
}

@media (max-width: 820px) {
  .app-shell { display: block; }
  .sidebar {
    position: fixed;
    left: 0;
    transform: translateX(-100%);
    transition: transform .18s ease;
    box-shadow: 10px 0 28px rgba(0, 0, 0, .12);
  }
  body.nav-open .sidebar { transform: translateX(0); }
  .sidebar-overlay {
    position: fixed;
    inset: 0;
    z-index: 25;
    display: none;
    padding: 0;
    background: rgba(24, 24, 27, .28);
    border: 0;
  }
  body.nav-open .sidebar-overlay { display: block; }
  .menu-button { display: inline-grid; }
  .topbar { padding: 10px 16px; }
  .update-state { display: none; }
  main { padding: 18px 16px; }
}

@media (max-width: 620px) {
  .topbar { min-height: 66px; }
  .page-heading p { display: none; }
  .token-control { width: 150px; }
  .metrics { grid-template-columns: 1fr; gap: 10px; }
  .metric-card { min-height: 134px; padding: 15px; }
  .metric-card > strong { font-size: 25px; }
  .panel-heading, .section-heading { align-items: flex-start; }
  .panel-heading { display: grid; }
  .segmented { width: 100%; grid-template-columns: repeat(3, 1fr); }
  .segmented button { padding: 0 8px; }
  .chart-panel { padding: 16px 12px 12px; }
  .chart-wrap { height: 238px; }
  .chart-legend { justify-content: flex-start; flex-wrap: wrap; }
  .table-footer { align-items: flex-start; }
  .pager-controls label { display: none; }
  .secondary-button { height: 32px; }
}
"""


_DASHBOARD_SCRIPT = r"""
const prefix = __ADMIN_PREFIX__;
const requiresToken = __TOKEN_REQUIRED__;

const ICONS = {
  bot: '<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>',
  refresh: '<path d="M21 12a9 9 0 0 0-15-6.7L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 15 6.7l3-2.7"/><path d="M16 16h5v5"/>',
  dashboard: '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>',
  activity: '<path d="M3 12h4l3-9 4 18 3-9h4"/>',
  send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  chart: '<path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>',
  box: '<path d="m21 8-9 5-9-5"/><path d="m3 8 9-5 9 5v8l-9 5-9-5Z"/><path d="M12 13v8"/>',
  smile: '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01"/>',
  image: '<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
  cpu: '<rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/>',
  menu: '<path d="M4 12h16M4 6h16M4 18h16"/>',
  key: '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15 7l2 2M18 4l2 2"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  'chevron-right': '<path d="m9 18 6-6-6-6"/>',
  'chevron-left': '<path d="m15 18-6-6 6-6"/>',
  square: '<rect width="14" height="14" x="5" y="5" rx="1"/>',
  focus: '<circle cx="12" cy="12" r="3"/><path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3"/>'
};

const icon = (name) => (
  `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ''}</svg>`
);

function mountIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach((element) => {
    element.innerHTML = icon(element.dataset.icon);
  });
}

const tokenInput = document.querySelector('#token');
const errorBanner = document.querySelector('#error');
const refreshButtons = [
  document.querySelector('#sidebar-refresh'),
  document.querySelector('#top-refresh')
];

const state = {
  overview: {},
  deliveries: [],
  usage: [],
  tasks: [],
  sandboxes: { items: [] },
  stickers: { counts: {}, items: [] },
  media: { counts: {}, items: [], jobs: [] },
  groups: { items: [] },
  contextPlans: { items: [] },
  usageDays: 30,
  pages: { deliveries: 0, usage: 0, stickers: 0, media: 0, contextPlans: 0 },
  pageSizes: { deliveries: 10, usage: 10, stickers: 10, media: 10, contextPlans: 10 }
};

let loading = false;
let chartRows = [];

tokenInput.value = localStorage.getItem('qqbot-admin-token') || '';
document.querySelector('#token-wrap').hidden = !requiresToken;

const esc = (value) => String(value ?? '').replace(
  /[&<>"']/g,
  (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]
);

const number = (value) => Number(value || 0).toLocaleString('zh-CN');

function fmt(timestamp) {
  const value = Number(timestamp);
  if (!Number.isFinite(value) || value <= 0) return '-';
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false });
}

function fmtBytes(value) {
  if (value === null || value === undefined || value === '') return '-';
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return '-';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let size = bytes;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  const formatted = index === 0 ? Math.round(size) : size.toFixed(size >= 10 ? 1 : 2);
  return `${formatted} ${units[index]}`;
}

function fmtDuration(value) {
  const seconds = Math.max(Number(value) || 0, 0);
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`;
}

function compact(value) {
  return new Intl.NumberFormat('zh-CN', {
    notation: 'compact',
    maximumFractionDigits: 1
  }).format(Number(value) || 0);
}

function requestHeaders() {
  return tokenInput.value ? { Authorization: `Bearer ${tokenInput.value}` } : {};
}

async function api(path, options = {}) {
  localStorage.setItem('qqbot-admin-token', tokenInput.value);
  const response = await fetch(`${prefix}/api${path}`, {
    ...options,
    headers: { ...requestHeaders(), ...(options.headers || {}) }
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `请求失败（HTTP ${response.status}）`);
  }
  return response.json();
}

const STATUS_LABELS = {
  pending: '等待发送',
  sending: '发送中',
  ambiguous: '结果待确认',
  committed: '已送达',
  failed: '失败',
  cancelled: '已取消',
  completed: '已完成',
  running: '运行中'
};

function statusTone(value) {
  const status = String(value || '').toLowerCase();
  if (['committed', 'completed', 'succeeded', 'running', 'up', 'enabled'].includes(status)) return 'success';
  if (status.startsWith('up ')) return 'success';
  if (['failed', 'cancelled', 'disabled', 'error'].includes(status)) return 'danger';
  if (['ambiguous', 'pending'].includes(status)) return 'warning';
  if (status === 'sending') return 'info';
  return '';
}

function statusBadge(value, label = '') {
  const raw = String(value || 'unknown');
  return `<span class="status-badge ${statusTone(raw)}">${esc(label || STATUS_LABELS[raw] || raw)}</span>`;
}

function capabilityHtml(capabilities) {
  const labels = { tools: 'Tools', streaming: 'Streaming', json_mode: 'JSON', vision: 'Vision' };
  const enabled = Object.entries(capabilities || {}).filter(([, value]) => value);
  if (!enabled.length) return '<span class="muted">Text</span>';
  return `<div class="capability-list">${enabled.map(([name]) => (
    `<span class="capability">${esc(labels[name] || name)}</span>`
  )).join('')}</div>`;
}

function ownerHtml(item) {
  const owner = String(item.owner || '');
  const matched = /^group:(\d+):user:(\d+)$/.exec(owner);
  if (matched) {
    return `<div class="stack"><span>群 <code>${esc(matched[1])}</code></span><span>QQ <code>${esc(matched[2])}</code></span></div>`;
  }
  return `<code>${esc(owner || item.owner_hash || '-')}</code>`;
}

function activityHtml(item) {
  const active = item.activities || [];
  const agents = item.agent_tasks || [];
  if (active.length) {
    const commands = active.map((activity) => (
      `<div><span class="status-badge info">执行中 · ${esc(fmtDuration(activity.elapsed_seconds))}</span>` +
      `<div class="command"><code>${esc(activity.command)}</code></div></div>`
    )).join('');
    const agent = agents.length
      ? `<div class="subtle">群聊任务：${esc(agents[0].summary)}</div>`
      : '';
    return `<div class="stack">${commands}${agent}</div>`;
  }
  if (agents.length) {
    return `<div class="stack"><span class="status-badge warning">模型处理中 · ${esc(fmtDuration(agents[0].elapsed_seconds))}</span><span>${esc(agents[0].summary)}</span></div>`;
  }
  const last = item.last_activity;
  if (last) {
    return `<div class="stack">${statusBadge(last.status, `最近：${last.status}`)}` +
      `<div class="command"><code>${esc(last.command)}</code></div>` +
      `<span class="subtle">${esc(fmtDuration(last.elapsed_seconds))} · ${esc(fmt(last.finished_at))}</span></div>`;
  }
  return '<span class="muted">等待任务</span>';
}

function workspaceHtml(item) {
  if (item.workspace_error) return `<span class="status-badge danger">${esc(item.workspace_error)}</span>`;
  const files = item.workspace_files || [];
  const summary = `${number(item.workspace_file_count)} 个文件 · ${fmtBytes(item.workspace_size_bytes)}`;
  if (!files.length) return `<span class="muted">${summary}</span>`;
  return `<details><summary>${summary}</summary><div class="file-list">${files.map((file) => (
    `<span><code>${esc(file.path)}</code> <span class="subtle">${fmtBytes(file.size_bytes)}</span></span>`
  )).join('')}</div></details>`;
}

function showError(message) {
  errorBanner.textContent = String(message || '未知错误');
  errorBanner.hidden = false;
}

function clearError() {
  errorBanner.textContent = '';
  errorBanner.hidden = true;
}

function setLoading(value) {
  document.body.classList.toggle('is-loading', value);
  refreshButtons.forEach((button) => { button.disabled = value; });
}

function setHealth(online) {
  document.querySelector('#health-dot').className = `health-dot ${online ? 'online' : 'error'}`;
  document.querySelector('#health-label').textContent = online ? '管理服务在线' : '连接异常';
}

function emptyRow(columns, text) {
  return `<tr><td colspan="${columns}" class="empty">${esc(text)}</td></tr>`;
}

function deliveryRow(item, actions = true) {
  let actionHtml = '';
  if (actions && ['ambiguous', 'failed'].includes(item.status)) {
    actionHtml += `<button class="table-action" type="button" data-retry="${esc(item.delivery_id)}">${icon('refresh')}重试</button>`;
  }
  if (actions && ['pending', 'ambiguous', 'failed'].includes(item.status)) {
    actionHtml += `<button class="table-action" type="button" data-cancel-delivery="${esc(item.delivery_id)}">${icon('square')}取消</button>`;
  }
  return `<tr><td><code>${esc(item.handle)}</code></td>` +
    `<td><div class="stack"><span>${esc(item.target_platform)} · ${esc(item.target_kind)}</span><code>${esc(item.target_native_conversation_id)}</code></div></td>` +
    `<td>${statusBadge(item.status)}</td><td>${number(item.attempts)}</td>` +
    `<td>${esc(fmt(item.updated_at))}</td>${actions ? `<td><div class="capability-list">${actionHtml || '<span class="muted">-</span>'}</div></td>` : ''}</tr>`;
}

function renderPager(key, total, page, pages, start, end) {
  const wrap = document.querySelector(`[data-pager-wrap="${key}"]`);
  if (!wrap) return;
  const size = state.pageSizes[key];
  const first = total ? start + 1 : 0;
  wrap.innerHTML = `<span>${number(first)}-${number(Math.min(end, total))} / ${number(total)} 条</span>` +
    `<div class="pager-controls"><label>每页` +
    `<select data-page-size="${key}">${[10, 25, 50].map((value) => (
      `<option value="${value}"${value === size ? ' selected' : ''}>${value}</option>`
    )).join('')}</select></label><span>第 ${page + 1} / ${pages} 页</span>` +
    `<div class="pager-buttons">` +
    `<button class="icon-button" type="button" data-page="${key}" data-direction="-1" title="上一页" aria-label="上一页"${page <= 0 ? ' disabled' : ''}>${icon('chevron-left')}</button>` +
    `<button class="icon-button" type="button" data-page="${key}" data-direction="1" title="下一页" aria-label="下一页"${page >= pages - 1 ? ' disabled' : ''}>${icon('chevron-right')}</button>` +
    `</div></div>`;
}

function renderPaged(key, items, bodyId, columns, rowRenderer, emptyText) {
  const size = state.pageSizes[key];
  const pages = Math.max(1, Math.ceil(items.length / size));
  state.pages[key] = Math.min(Math.max(state.pages[key], 0), pages - 1);
  const page = state.pages[key];
  const start = page * size;
  const end = start + size;
  document.querySelector(`#${bodyId}`).innerHTML = items.length
    ? items.slice(start, end).map(rowRenderer).join('')
    : emptyRow(columns, emptyText);
  renderPager(key, items.length, page, pages, start, end);
}

function renderOverview() {
  const overview = state.overview;
  const sandboxItems = state.sandboxes.items || [];
  const stickerCounts = state.stickers.counts || {};
  const groupItems = state.groups.items || [];
  const overrides = groupItems.reduce((total, item) => total + (item.overrides || []).length, 0);
  const enabledGroups = groupItems.filter((item) => item.enabled).length;

  document.querySelector('#metric-tasks').textContent = number(overview.running_tasks);
  document.querySelector('#metric-tasks-note').textContent = overview.running_tasks
    ? '模型请求正在处理'
    : '当前任务队列空闲';
  document.querySelector('#metric-sandbox').textContent = number(state.sandboxes.active_commands);
  document.querySelector('#metric-sandbox-note').textContent = `${number(sandboxItems.length)} 个容器可见`;
  document.querySelector('#metric-stickers').textContent = number(stickerCounts.total);
  document.querySelector('#metric-stickers-note').textContent = `${number(stickerCounts.learned_images + stickerCounts.learned_faces)} 个从 QQ 学习 · ${number(stickerCounts.local_images)} 个本地`;
  document.querySelector('#metric-groups').textContent = number(enabledGroups);
  document.querySelector('#metric-groups-note').textContent = `${number(overrides)} 个群友模型覆盖`;

  const recent = state.deliveries.slice(0, 8);
  document.querySelector('#recent-delivery-body').innerHTML = recent.length
    ? recent.map((item) => deliveryRow(item, false)).join('')
    : emptyRow(5, '尚无投递记录');

  document.querySelector('#nav-task-count').textContent = number(state.tasks.length);
  document.querySelector('#nav-delivery-count').textContent = number(state.deliveries.length);
  document.querySelector('#nav-sandbox-count').textContent = number(sandboxItems.length);
  document.querySelector('#nav-sticker-count').textContent = number(stickerCounts.total);
  document.querySelector('#nav-media-count').textContent = number((state.media.counts || {}).total);
}

function renderTasks() {
  document.querySelector('#task-count').textContent = `${number(state.tasks.length)} 条`;
  document.querySelector('#task-body').innerHTML = state.tasks.length
    ? state.tasks.map((item) => `<tr><td><code>${esc(item.task_id)}</code></td>` +
      `<td><code>${esc(item.conversation_id)}</code></td><td><span class="truncate-cell" title="${esc(item.summary)}">${esc(item.summary)}</span></td>` +
      `<td>${esc(fmtDuration(item.elapsed_seconds))}</td><td><button class="table-action" type="button" data-kill="${esc(item.task_id)}">${icon('square')}停止</button></td></tr>`).join('')
    : emptyRow(5, '当前没有运行任务');
}

function renderDeliveries() {
  document.querySelector('#delivery-heading-count').textContent = `${number(state.deliveries.length)} 条`;
  renderPaged('deliveries', state.deliveries, 'delivery-body', 6, (item) => deliveryRow(item), '尚无投递记录');
}

function renderUsage() {
  document.querySelector('#usage-count').textContent = `${number(state.usage.length)} 条`;
  renderPaged('usage', state.usage, 'usage-body', 6, (item) => (
    `<tr><td>${esc(item.day)}</td><td><code>${esc(item.scope_key)}</code></td>` +
    `<td>${esc(item.source)}</td><td>${number(item.calls)}</td>` +
    `<td>${number(item.input_tokens)}</td><td>${number(item.output_tokens)}</td></tr>`
  ), '尚无用量记录');
}

function renderContextPlans() {
  const data = state.contextPlans;
  const items = data.items || [];
  document.querySelector('#context-plan-count').textContent = `${number(items.length)} 条`;
  if (!data.available) {
    document.querySelector('#context-plan-body').innerHTML = emptyRow(7, data.error || '上下文决策记录不可用');
    document.querySelector('[data-pager-wrap="contextPlans"]').innerHTML = '';
    return;
  }
  renderPaged('contextPlans', items, 'context-plan-body', 7, (item) => {
    const candidates = (item.candidates || []).slice(0, 3).map((candidate) => (
      `<span><code>msg#${esc(candidate.message_id)}</code> · ${Number(candidate.score || 0).toFixed(1)}</span>`
    )).join('') || '<span class="muted">独立问题</span>';
    const reasons = (item.reason_codes || []).map((reason) => `<span class="capability">${esc(reason)}</span>`).join('') || '<span class="muted">-</span>';
    return `<tr><td><code>${esc(item.turn_handle)}</code><div class="subtle">${esc(item.status)} · ${esc(item.profile)}</div></td>` +
      `<td><code>${esc(item.scope_key)}</code></td>` +
      `<td><div class="stack"><span>当前 <code>msg#${esc(item.current_message_id)}</code></span><span>焦点 ${item.focus_message_id ? `<code>msg#${esc(item.focus_message_id)}</code>` : '<span class="muted">未锁定</span>'}</span></div></td>` +
      `<td>${statusBadge(Number(item.confidence) >= 0.7 ? 'enabled' : (Number(item.confidence) > 0 ? 'pending' : 'disabled'), `${Math.round(Number(item.confidence || 0) * 100)}%`)}</td>` +
      `<td><div class="capability-list">${reasons}</div></td><td><div class="stack">${candidates}</div></td><td>${fmt(item.created_at)}</td></tr>`;
  }, '还没有上下文决策记录');
}

function renderSandboxes() {
  const data = state.sandboxes;
  const items = data.items || [];
  document.querySelector('#sandbox-count').textContent = `${number(items.length)} 个`;
  if (!data.available) {
    document.querySelector('#sandbox-body').innerHTML = emptyRow(6, data.error || '沙盒功能未启用');
    return;
  }
  document.querySelector('#sandbox-body').innerHTML = items.length
    ? items.map((item) => `<tr><td><code>${esc(item.sandbox_id)}</code><div class="subtle">${esc(item.runtime || '-')}</div></td>` +
      `<td>${ownerHtml(item)}</td><td>${statusBadge(item.running ? 'running' : 'disabled', item.status || (item.running ? '运行中' : '已停止'))}</td>` +
      `<td>${activityHtml(item)}</td><td><div class="stack"><span>CPU ${esc(item.cpu_percent || '-')}</span>` +
      `<span>内存 ${esc(item.memory_usage || '-')}</span><span class="subtle">${esc(item.memory_percent || '-')}</span></div></td>` +
      `<td>${workspaceHtml(item)}</td></tr>`).join('')
    : emptyRow(6, '当前没有沙盒');
}

function renderStickers() {
  const data = state.stickers;
  const items = data.items || [];
  document.querySelector('#sticker-count').textContent = `${number(items.length)} 个`;
  if (!data.available) {
    document.querySelector('#sticker-body').innerHTML = emptyRow(5, data.error || '表情库存不可用');
    document.querySelector('[data-pager-wrap="stickers"]').innerHTML = '';
    return;
  }
  renderPaged('stickers', items, 'sticker-body', 5, (item) => (
    `<tr><td>${item.source === 'local' ? '机器人本地' : '从 QQ 学习'}</td>` +
    `<td>${item.kind === 'qq-face' ? 'QQ 自带表情' : '图片表情'}</td>` +
    `<td>${esc(item.name)}</td><td><code class="truncate-cell" title="${esc(item.reference)}">${esc(item.reference)}</code></td>` +
    `<td>${fmtBytes(item.size_bytes)}</td></tr>`
  ), '还没有保存表情包');
}

function renderMedia() {
  const data = state.media;
  const items = data.items || [];
  const jobs = data.jobs || [];
  const counts = data.counts || {};
  document.querySelector('#media-count').textContent = `${number(counts.total)} 张 · ${fmtBytes(counts.bytes)}`;
  document.querySelector('#media-job-count').textContent = `${number(jobs.length)} 条 · ${number(counts.queued)} 处理中 · ${number(counts.failed)} 失败`;
  if (!data.available) {
    document.querySelector('#media-body').innerHTML = emptyRow(6, data.error || '媒体库未启用');
    document.querySelector('#media-job-body').innerHTML = emptyRow(6, '没有任务数据');
    document.querySelector('[data-pager-wrap="media"]').innerHTML = '';
    return;
  }
  renderPaged('media', items, 'media-body', 6, (item) => (
    `<tr><td><code>media#${esc(item.media_id)}</code><div class="subtle">${esc((item.sha256 || '').slice(0, 12))}</div></td>` +
    `<td><span class="truncate-cell" title="${esc(item.summary || '')}">${esc(item.summary || '等待识图')}</span></td>` +
    `<td>${esc(item.mime_type)}<div class="subtle">${fmtBytes(item.byte_size)}</div></td>` +
    `<td>${statusBadge(item.safety === 'safe' ? 'enabled' : (item.safety === 'blocked' ? 'danger' : 'pending'), item.safety || '等待')}</td>` +
    `<td><code>${esc(item.vision_model || '-')}</code></td><td>${number(item.times_sent)}</td></tr>`
  ), '还没有保存图片');
  document.querySelector('#media-job-body').innerHTML = jobs.length
    ? jobs.map((job) => `<tr><td><code>#${esc(job.job_id)}</code></td><td>${esc(job.job_type)}</td>` +
      `<td>${statusBadge(job.status === 'running' ? 'running' : (job.status === 'failed' ? 'danger' : 'pending'), job.status)}</td>` +
      `<td>${number(job.attempts)}</td><td><span class="truncate-cell" title="${esc(job.last_error || '')}">${esc(job.last_error || '-')}</span></td><td>${fmt(job.updated_at)}</td></tr>`).join('')
    : emptyRow(6, '当前没有等待或失败任务');
}

function renderGroups() {
  const items = state.groups.items || [];
  document.querySelector('#group-count').textContent = `${number(items.length)} 个群`;
  document.querySelector('#group-model-body').innerHTML = items.length
    ? items.map((item) => {
      const overrides = (item.overrides || []).map((override) => (
        `<div><code>QQ ${esc(override.user_id)}</code> → <code>${esc(override.profile)}</code> / ${esc(override.model)}` +
        `${override.recognized ? '' : ' <span class="status-badge danger">配置失效</span>'}</div>`
      )).join('') || '<span class="muted">全部使用默认配置</span>';
      return `<tr><td><code>${esc(item.group_id)}</code></td><td>${statusBadge(item.enabled ? 'enabled' : 'disabled', item.enabled ? '已启用' : '已禁用')}</td>` +
        `<td><code>${esc(item.default_profile)}</code></td><td>${esc(item.default_provider)} / <code>${esc(item.default_model)}</code></td>` +
        `<td><div class="stack">${overrides}</div></td></tr>`;
    }).join('')
    : emptyRow(5, '还没有观察到 QQ 群');
}

function renderModels() {
  const modelState = state.overview.models || {};
  const items = modelState.profiles || [];
  document.querySelector('#model-count').textContent = `${number(items.length)} 个`;
  document.querySelector('#model-body').innerHTML = items.length
    ? items.map((item) => `<tr><td><code>${esc(item.name)}</code>${item.name === modelState.default ? ' <span class="capability">默认</span>' : ''}</td>` +
      `<td>${esc(item.provider)}</td><td><code>${esc(item.protocol)}</code></td><td><code>${esc(item.model)}</code></td>` +
      `<td>${capabilityHtml(item.capabilities)}</td><td>${statusBadge(item.configured ? 'enabled' : 'disabled', item.configured ? '已配置' : '缺少密钥')}</td></tr>`).join('')
    : emptyRow(6, '没有模型配置');
}

function dateKey(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function aggregateUsage(rows) {
  const buckets = new Map();
  rows.forEach((item) => {
    const bucket = buckets.get(item.day) || { calls: 0, tokens: 0 };
    bucket.calls += Number(item.calls) || 0;
    bucket.tokens += (Number(item.input_tokens) || 0) + (Number(item.output_tokens) || 0);
    buckets.set(item.day, bucket);
  });
  const points = [];
  const now = new Date();
  for (let offset = state.usageDays - 1; offset >= 0; offset -= 1) {
    const date = new Date(now.getFullYear(), now.getMonth(), now.getDate() - offset);
    const day = dateKey(date);
    points.push({ day, ...(buckets.get(day) || { calls: 0, tokens: 0 }) });
  }
  return points;
}

function drawChart() {
  const canvas = document.querySelector('#usage-chart');
  const empty = document.querySelector('#chart-empty');
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(rect.width * ratio);
  canvas.height = Math.round(rect.height * ratio);
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, rect.width, rect.height);

  const points = aggregateUsage(chartRows);
  const tokenTotal = points.reduce((sum, item) => sum + item.tokens, 0);
  const callTotal = points.reduce((sum, item) => sum + item.calls, 0);
  document.querySelector('#usage-summary').textContent = `${state.usageDays} 天内 ${number(callTotal)} 次调用 · ${number(tokenTotal)} Token`;
  empty.hidden = tokenTotal > 0 || callTotal > 0;

  const padding = { top: 14, right: 18, bottom: 28, left: 48 };
  const width = rect.width - padding.left - padding.right;
  const height = rect.height - padding.top - padding.bottom;
  const tokenMax = Math.max(...points.map((item) => item.tokens), 1);
  const callMax = Math.max(...points.map((item) => item.calls), 1);
  const x = (index) => padding.left + (points.length === 1 ? width / 2 : index * width / (points.length - 1));
  const y = (value, max) => padding.top + height - value / max * height;

  context.font = '11px ui-sans-serif, system-ui, sans-serif';
  context.fillStyle = '#71717a';
  context.strokeStyle = '#e4e4e7';
  context.lineWidth = 1;
  for (let line = 0; line <= 4; line += 1) {
    const lineY = padding.top + line * height / 4;
    context.beginPath();
    context.moveTo(padding.left, lineY);
    context.lineTo(rect.width - padding.right, lineY);
    context.stroke();
    const label = compact(tokenMax * (4 - line) / 4);
    context.fillText(label, 4, lineY + 4);
  }

  const labelCount = Math.min(5, points.length);
  const labelIndexes = new Set();
  for (let index = 0; index < labelCount; index += 1) {
    labelIndexes.add(Math.round(index * (points.length - 1) / Math.max(labelCount - 1, 1)));
  }
  context.textAlign = 'center';
  labelIndexes.forEach((index) => {
    context.fillText(points[index].day.slice(5).replace('-', '/'), x(index), rect.height - 6);
  });
  context.textAlign = 'left';

  context.beginPath();
  points.forEach((item, index) => {
    const pointX = x(index);
    const pointY = y(item.tokens, tokenMax);
    if (index === 0) context.moveTo(pointX, pointY);
    else context.lineTo(pointX, pointY);
  });
  context.lineTo(x(points.length - 1), padding.top + height);
  context.lineTo(x(0), padding.top + height);
  context.closePath();
  context.fillStyle = 'rgba(39, 39, 42, .12)';
  context.fill();

  context.beginPath();
  points.forEach((item, index) => {
    if (index === 0) context.moveTo(x(index), y(item.tokens, tokenMax));
    else context.lineTo(x(index), y(item.tokens, tokenMax));
  });
  context.strokeStyle = '#27272a';
  context.lineWidth = 2;
  context.setLineDash([]);
  context.stroke();

  context.beginPath();
  points.forEach((item, index) => {
    if (index === 0) context.moveTo(x(index), y(item.calls, callMax));
    else context.lineTo(x(index), y(item.calls, callMax));
  });
  context.strokeStyle = '#71717a';
  context.lineWidth = 1.5;
  context.setLineDash([5, 5]);
  context.stroke();
  context.setLineDash([]);
}

function renderAll() {
  renderOverview();
  renderTasks();
  renderDeliveries();
  renderUsage();
  renderContextPlans();
  renderSandboxes();
  renderStickers();
  renderMedia();
  renderGroups();
  renderModels();
  drawChart();
}

async function load() {
  if (loading) return;
  loading = true;
  setLoading(true);
  clearError();
  try {
    const [overview, deliveries, usage, tasks, sandboxes, stickers, media, groups, contextPlans] = await Promise.all([
      api('/overview'),
      api('/deliveries'),
      api(`/usage?days=${state.usageDays}`),
      api('/tasks'),
      api('/sandboxes'),
      api('/stickers'),
      api('/media'),
      api('/group-models'),
      api('/context-plans')
    ]);
    state.overview = overview;
    state.deliveries = deliveries.items || [];
    state.usage = usage.items || [];
    state.tasks = tasks.items || [];
    state.sandboxes = sandboxes;
    state.stickers = stickers;
    state.media = media;
    state.groups = groups;
    state.contextPlans = contextPlans;
    chartRows = state.usage;
    renderAll();
    setHealth(true);
    document.querySelector('#last-updated').textContent = `更新于 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
  } catch (error) {
    showError(error.message);
    setHealth(false);
  } finally {
    loading = false;
    setLoading(false);
  }
}

function openView(viewId) {
  const target = document.querySelector(`#${viewId}.view`);
  if (!target) return;
  document.querySelectorAll('.view').forEach((section) => {
    section.hidden = section !== target;
  });
  document.querySelectorAll('.nav-item').forEach((item) => {
    item.classList.toggle('active', item.dataset.view === viewId);
  });
  const navItem = document.querySelector(`.nav-item[data-view="${viewId}"]`);
  if (navItem) {
    document.querySelector('#page-title').textContent = navItem.dataset.title;
    document.querySelector('#page-subtitle').textContent = navItem.dataset.subtitle;
  }
  document.body.classList.remove('nav-open');
  if (viewId === 'overview') requestAnimationFrame(drawChart);
}

async function runAction(action) {
  clearError();
  try {
    await action();
    await load();
  } catch (error) {
    showError(error.message);
  }
}

document.addEventListener('click', (event) => {
  const navItem = event.target.closest('.nav-item[data-view]');
  if (navItem) openView(navItem.dataset.view);

  const openButton = event.target.closest('[data-open-view]');
  if (openButton) openView(openButton.dataset.openView);

  const dayButton = event.target.closest('[data-days]');
  if (dayButton) {
    state.usageDays = Number(dayButton.dataset.days);
    state.pages.usage = 0;
    document.querySelectorAll('[data-days]').forEach((button) => {
      button.classList.toggle('active', button === dayButton);
    });
    load();
  }

  const pageButton = event.target.closest('[data-page][data-direction]');
  if (pageButton && !pageButton.disabled) {
    const key = pageButton.dataset.page;
    state.pages[key] += Number(pageButton.dataset.direction);
    if (key === 'deliveries') renderDeliveries();
    if (key === 'usage') renderUsage();
    if (key === 'stickers') renderStickers();
    if (key === 'media') renderMedia();
    if (key === 'contextPlans') renderContextPlans();
  }

  const retry = event.target.closest('[data-retry]');
  if (retry) runAction(() => api(`/deliveries/${encodeURIComponent(retry.dataset.retry)}/retry`, { method: 'POST' }));

  const cancelDelivery = event.target.closest('[data-cancel-delivery]');
  if (cancelDelivery) runAction(() => api(`/deliveries/${encodeURIComponent(cancelDelivery.dataset.cancelDelivery)}/cancel`, { method: 'POST' }));

  const kill = event.target.closest('[data-kill]');
  if (kill) runAction(() => api(`/tasks/${encodeURIComponent(kill.dataset.kill)}/cancel`, { method: 'POST' }));
});

document.addEventListener('change', (event) => {
  const select = event.target.closest('[data-page-size]');
  if (select) {
    const key = select.dataset.pageSize;
    state.pageSizes[key] = Number(select.value);
    state.pages[key] = 0;
    if (key === 'deliveries') renderDeliveries();
    if (key === 'usage') renderUsage();
    if (key === 'stickers') renderStickers();
    if (key === 'media') renderMedia();
    if (key === 'contextPlans') renderContextPlans();
  }
});

refreshButtons.forEach((button) => button.addEventListener('click', load));
tokenInput.addEventListener('change', load);
document.querySelector('#menu-button').addEventListener('click', () => document.body.classList.add('nav-open'));
document.querySelector('#sidebar-overlay').addEventListener('click', () => document.body.classList.remove('nav-open'));
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') document.body.classList.remove('nav-open');
});

let resizeTimer = 0;
window.addEventListener('resize', () => {
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(drawChart, 120);
});

mountIcons();
setInterval(() => {
  if (!document.hidden) load();
}, 5000);
load();
"""
