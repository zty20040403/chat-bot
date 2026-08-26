import { useEffect, useState, type ComponentType } from 'react'
import {
  Activity,
  Bot,
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
  ShieldCheck,
  Users,
  Wrench,
  X,
} from 'lucide-react'
import { StatusBadge } from './components'
import { useControlPlane } from './useControlPlane'
import {
  AuditView,
  DatabasesView,
  GroupsView,
  MediaView,
  ObservabilityView,
  OverviewView,
  SandboxesView,
  TasksView,
  ToolsView,
  TracesView,
} from './views'

const runtime = window.__KENNETHBOT_ADMIN__ ?? {
  prefix: '/bot-admin',
  apiBase: '/bot-admin/api/v1',
  version: 'dev',
  requiresToken: false,
}

type ViewId = 'overview' | 'observability' | 'groups' | 'tasks' | 'tools' | 'traces' | 'databases' | 'sandboxes' | 'media' | 'audit'

const NAVIGATION: Array<{ id: ViewId; label: string; group: string; icon: ComponentType<{ size?: number }> }> = [
  { id: 'overview', label: '概览', group: '运行', icon: Gauge },
  { id: 'observability', label: '可观测性', group: '运行', icon: Activity },
  { id: 'tasks', label: '任务与投递', group: '运行', icon: ListChecks },
  { id: 'traces', label: 'Trace 与上下文', group: '运行', icon: BrainCircuit },
  { id: 'groups', label: '模型与群友', group: '配置', icon: Users },
  { id: 'tools', label: '工具权限', group: '配置', icon: Wrench },
  { id: 'media', label: '媒体审核', group: '数据', icon: Image },
  { id: 'sandboxes', label: '沙盒', group: '数据', icon: Boxes },
  { id: 'databases', label: '数据库', group: '基础设施', icon: Database },
  { id: 'audit', label: '审计记录', group: '基础设施', icon: FileClock },
]

export function App() {
  const plane = useControlPlane(runtime)
  const [active, setActive] = useState<ViewId>(() => {
    const requested = window.location.hash.replace('#', '') as ViewId
    return NAVIGATION.some((item) => item.id === requested) ? requested : 'overview'
  })
  const [sidebarOpen, setSidebarOpen] = useState(false)

  useEffect(() => {
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#${active}`)
  }, [active])

  if (!plane.authenticated) {
    return <TokenGate onSubmit={plane.setToken} />
  }

  const openView = (view: ViewId) => {
    setActive(view)
    setSidebarOpen(false)
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="brand">
          <span className="brand-icon"><Bot size={22} /></span>
          <div><strong>Kennethbot</strong><span>Control Plane</span></div>
          <button className="mobile-close" title="关闭导航" onClick={() => setSidebarOpen(false)}><PanelLeftClose size={18} /></button>
        </div>
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
        <div className="sidebar-foot"><ShieldCheck size={16} /><span>API v1 · 审计已启用</span></div>
      </aside>
      {sidebarOpen && <button className="sidebar-scrim" aria-label="关闭导航" onClick={() => setSidebarOpen(false)} />}
      <div className="workspace">
        <header className="topbar">
          <button className="menu-button" title="打开导航" onClick={() => setSidebarOpen(true)}><Menu size={19} /></button>
          <div className="crumb"><span>Kennethbot</span><b>/</b><strong>{NAVIGATION.find((item) => item.id === active)?.label}</strong></div>
          <div className="topbar-status"><StatusBadge value={plane.online ? 'online' : 'offline'} label={plane.online ? '实时' : '断开'} /><span>v{runtime.version}</span>{runtime.requiresToken && <button className="text-button" onClick={() => plane.setToken('')}>退出</button>}</div>
        </header>
        {plane.error && <div className="error-banner"><span>{plane.error}</span><button title="关闭错误提示" onClick={plane.clearError}><X size={16} /></button></div>}
        <main>
          {active === 'overview' && <OverviewView plane={plane} />}
          {active === 'observability' && <ObservabilityView plane={plane} />}
          {active === 'groups' && <GroupsView plane={plane} />}
          {active === 'tasks' && <TasksView plane={plane} />}
          {active === 'tools' && <ToolsView plane={plane} />}
          {active === 'traces' && <TracesView plane={plane} />}
          {active === 'databases' && <DatabasesView plane={plane} />}
          {active === 'sandboxes' && <SandboxesView plane={plane} />}
          {active === 'media' && <MediaView plane={plane} />}
          {active === 'audit' && <AuditView plane={plane} />}
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
