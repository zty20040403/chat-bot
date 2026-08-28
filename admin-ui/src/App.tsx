import { useEffect, useState, type ComponentType } from 'react'
import {
  Activity,
  BarChart3,
  Bot,
  BookOpen,
  Boxes,
  BrainCircuit,
  Database,
  FileClock,
  Gauge,
  Image,
  KeyRound,
  ListChecks,
  Menu,
  PanelLeftClose,
  RefreshCw,
  Users,
  Wrench,
  X,
} from 'lucide-react'
import { StatusBadge } from './components'
import { DETAIL_META, DetailView, type DetailViewId } from './detailViews'
import { useControlPlane } from './useControlPlane'
import {
  AuditView,
  DatabasesView,
  GroupsView,
  HelpView,
  MediaView,
  ObservabilityView,
  OverviewView,
  SandboxesView,
  TasksView,
  ToolsView,
  TracesView,
  UsageView,
} from './views'

const runtime = window.__KENNETHBOT_ADMIN__ ?? {
  prefix: '/bot-admin',
  apiBase: '/bot-admin/api/v1',
  version: 'dev',
  requiresToken: false,
}

type ViewId = 'overview' | 'observability' | 'usage' | 'groups' | 'tasks' | 'tools' | 'traces' | 'databases' | 'sandboxes' | 'media' | 'audit' | 'help'

const NAVIGATION: Array<{ id: ViewId; label: string; description: string; group: string; icon: ComponentType<{ size?: number }> }> = [
  { id: 'overview', label: '概览', description: '服务状态、Token 趋势与最近投递', group: '运行', icon: Gauge },
  { id: 'observability', label: '可观测性', description: 'Prometheus、告警、延迟与模型降级', group: '运行', icon: Activity },
  { id: 'tasks', label: '任务与投递', description: 'Agent、持久任务和 QQ 消息回执', group: '运行', icon: ListChecks },
  { id: 'usage', label: '模型用量', description: '按日期、会话和来源统计 Token', group: '运行', icon: BarChart3 },
  { id: 'traces', label: 'Trace 与上下文', description: '回放回答链路与上下文选择过程', group: '运行', icon: BrainCircuit },
  { id: 'sandboxes', label: '沙盒', description: '临时容器、命令和资源使用情况', group: '资源', icon: Boxes },
  { id: 'media', label: '媒体审核', description: '表情候选、识图和分享内容记录', group: '资源', icon: Image },
  { id: 'groups', label: '模型与群友', description: '配置群开关、统一模型和个人模型', group: '配置', icon: Users },
  { id: 'tools', label: '工具权限', description: '控制 Agent 可见工具与执行策略', group: '配置', icon: Wrench },
  { id: 'databases', label: '数据库', description: '主备节点、连接池与复制状态', group: '基础设施', icon: Database },
  { id: 'audit', label: '审计记录', description: '查询所有控制面修改及资源版本', group: '基础设施', icon: FileClock },
  { id: 'help', label: '使用说明', description: '每个功能的用途、操作方法和影响', group: '帮助', icon: BookOpen },
]

interface RouteState {
  active: ViewId
  detail: DetailViewId | null
}

function parseRoute(value: string): RouteState {
  const requested = value.replace(/^#/, '')
  const detail = (Object.entries(DETAIL_META) as Array<[DetailViewId, (typeof DETAIL_META)[DetailViewId]]>)
    .find(([, meta]) => meta.route === requested)
  if (detail) return { active: detail[1].parent, detail: detail[0] }
  const view = requested as ViewId
  return {
    active: NAVIGATION.some((item) => item.id === view) ? view : 'overview',
    detail: null,
  }
}

function routeValue(route: RouteState): string {
  return route.detail ? DETAIL_META[route.detail].route : route.active
}

export function App() {
  const plane = useControlPlane(runtime)
  const [route, setRoute] = useState<RouteState>(() => parseRoute(window.location.hash))
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const active = route.active
  const activeEntry = NAVIGATION.find((item) => item.id === active) ?? NAVIGATION[0]
  const detailEntry = route.detail ? DETAIL_META[route.detail] : null

  useEffect(() => {
    const syncRoute = () => setRoute(parseRoute(window.location.hash))
    window.addEventListener('popstate', syncRoute)
    window.addEventListener('hashchange', syncRoute)
    if (!window.location.hash) {
      window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#overview`)
    }
    return () => {
      window.removeEventListener('popstate', syncRoute)
      window.removeEventListener('hashchange', syncRoute)
    }
  }, [])

  if (!plane.authenticated) {
    return <TokenGate onSubmit={plane.setToken} />
  }

  const openView = (view: ViewId) => {
    const next = { active: view, detail: null }
    window.history.pushState(null, '', `${window.location.pathname}${window.location.search}#${routeValue(next)}`)
    setRoute(next)
    setSidebarOpen(false)
  }

  const openDetail = (detail: DetailViewId) => {
    const next = { active: DETAIL_META[detail].parent, detail }
    window.history.pushState(null, '', `${window.location.pathname}${window.location.search}#${routeValue(next)}`)
    setRoute(next)
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="brand">
          <span className="brand-icon"><Bot size={21} /></span>
          <div><strong>QQ Bot</strong><span>Control Center</span></div>
          <button className="mobile-close" title="关闭导航" onClick={() => setSidebarOpen(false)}><PanelLeftClose size={18} /></button>
        </div>
        <button className="quick-refresh" type="button" onClick={() => void plane.refreshAll()}><RefreshCw className={plane.loading.size ? 'spin' : ''} size={16} /><span>刷新数据</span></button>
        <nav aria-label="管理导航">
          {[...new Set(NAVIGATION.map((item) => item.group))].map((group) => (
            <div className="nav-section" key={group}>
              <span className="nav-label">{group}</span>
              {NAVIGATION.filter((item) => item.group === group).map((item) => {
                const Icon = item.icon
                return <button type="button" key={item.id} className={active === item.id ? 'active' : ''} onClick={() => openView(item.id)}><Icon size={17} /><span>{item.label}</span></button>
              })}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot"><span className={`status-dot ${plane.online ? 'online' : ''}`} /><span><strong>{plane.online ? '节点运行正常' : '实时连接断开'}</strong><small>v{runtime.version} · API v1</small></span></div>
      </aside>
      {sidebarOpen && <button className="sidebar-scrim" aria-label="关闭导航" onClick={() => setSidebarOpen(false)} />}
      <div className="workspace">
        <header className="topbar">
          <button className="menu-button" title="打开导航" onClick={() => setSidebarOpen(true)}><Menu size={19} /></button>
          <div className="topbar-title"><h1>{detailEntry?.title ?? activeEntry.label}</h1><p>{detailEntry?.description ?? activeEntry.description}</p></div>
          <div className="topbar-status"><StatusBadge value={plane.online ? 'online' : 'offline'} label={plane.online ? '实时' : '断开'} /><span>{plane.updatedAt ? `更新于 ${new Date(plane.updatedAt).toLocaleTimeString('zh-CN', { hour12: false })}` : '正在加载'}</span><button className="icon-button topbar-refresh" title="刷新全部数据" onClick={() => void plane.refreshAll()}><RefreshCw className={plane.loading.size ? 'spin' : ''} size={16} /></button>{runtime.requiresToken && <button className="text-button" onClick={() => plane.setToken('')}>退出</button>}</div>
        </header>
        {plane.error && <div className="error-banner"><span>{plane.error}</span><button title="关闭错误提示" onClick={plane.clearError}><X size={16} /></button></div>}
        <main>
          {route.detail ? (
            <DetailView detail={route.detail} plane={plane} onBack={() => openView(DETAIL_META[route.detail!].parent)} />
          ) : (
            <>
              {active === 'overview' && <OverviewView plane={plane} onOpenDetail={openDetail} />}
              {active === 'observability' && <ObservabilityView plane={plane} onOpenDetail={openDetail} />}
              {active === 'groups' && <GroupsView plane={plane} />}
              {active === 'tasks' && <TasksView plane={plane} onOpenDetail={openDetail} />}
              {active === 'usage' && <UsageView plane={plane} onOpenDetail={openDetail} />}
              {active === 'tools' && <ToolsView plane={plane} />}
              {active === 'traces' && <TracesView plane={plane} onOpenDetail={openDetail} />}
              {active === 'databases' && <DatabasesView plane={plane} />}
              {active === 'sandboxes' && <SandboxesView plane={plane} />}
              {active === 'media' && <MediaView plane={plane} onOpenDetail={openDetail} />}
              {active === 'audit' && <AuditView plane={plane} onOpenDetail={openDetail} />}
              {active === 'help' && <HelpView />}
            </>
          )}
        </main>
      </div>
    </div>
  )
}

function TokenGate({ onSubmit }: { onSubmit: (token: string) => void }) {
  const [value, setValue] = useState('')
  return (
    <main className="token-gate">
      <form onSubmit={(event) => { event.preventDefault(); if (value.trim()) onSubmit(value) }}>
        <div className="token-mark"><KeyRound size={22} /></div>
        <h1>Kennethbot Control</h1>
        <p>输入管理 Token 以连接内网控制面。</p>
        <label><span>管理 Token</span><input type="password" autoFocus autoComplete="current-password" value={value} onChange={(event) => setValue(event.target.value)} /></label>
        <button className="primary-button" type="submit" disabled={!value.trim()}>进入控制台</button>
      </form>
    </main>
  )
}
