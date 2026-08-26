import {
  Ban,
  Check,
  CircleAlert,
  Clock3,
  Database,
  ExternalLink,
  Play,
  RotateCcw,
  ShieldCheck,
  Square,
  X,
} from 'lucide-react'
import { TokenUsageChart } from './TokenUsageChart'
import {
  DataTable,
  DraftSelect,
  EmptyState,
  Metric,
  PageHeader,
  RefreshButton,
  Section,
  StatusBadge,
  Toggle,
  fmtBytes,
  fmtDuration,
  fmtNumber,
  fmtTime,
} from './components'
import type { useControlPlane } from './useControlPlane'

type Plane = ReturnType<typeof useControlPlane>

function rows(value: unknown): any[] {
  return Array.isArray(value) ? value : []
}

function jsonRows(value: unknown): any[] {
  if (Array.isArray(value)) return value
  try {
    return rows(JSON.parse(String(value ?? '[]')))
  } catch {
    return []
  }
}

export function OverviewView({ plane }: { plane: Plane }) {
  const overview = plane.data.overview ?? {}
  const observability = plane.data.observability ?? {}
  const totals = observability.process?.totals ?? {}
  const usage = rows(plane.data.usage?.items)
  const deliveries = rows(plane.data.deliveries?.items)
  const sandboxes = plane.data.sandboxes ?? {}
  const media = plane.data.media ?? {}
  const usageTotals = usage.reduce((sum, item) => ({
    input: sum.input + Number(item.input_tokens ?? 0),
    output: sum.output + Number(item.output_tokens ?? 0),
    calls: sum.calls + Number(item.calls ?? 0),
  }), { input: 0, output: 0, calls: 0 })
  return (
    <>
      <PageHeader
        title="运行概览"
        description="服务状态、模型请求、Token 与后台任务的实时视图"
        action={<RefreshButton loading={plane.loading.has('overview')} onClick={() => void plane.refreshMany(['overview', 'observability'])} />}
      />
      <div className="metric-grid overview-metrics">
        <Metric label="服务状态" value={plane.online ? '运行中' : '连接断开'} hint={`v${overview.version ?? window.__KENNETHBOT_ADMIN__.version} · 已运行 ${fmtDuration(overview.uptime_seconds)}`} />
        <Metric label="运行任务" value={fmtNumber(overview.running_tasks)} hint={`${fmtNumber(overview.durable_jobs?.running)} 个持久任务 · ${fmtNumber(totals.turns)} 个 Agent 回合`} />
        <Metric label="沙盒活动" value={fmtNumber(sandboxes.active_commands)} hint={`${fmtNumber(rows(sandboxes.items).length)} 个沙盒 · 任务结束后自动销毁`} />
        <Metric label="90 天 Token" value={fmtNumber(usageTotals.input + usageTotals.output)} hint={`${fmtNumber(usageTotals.calls)} 次调用 · 输入 ${fmtNumber(usageTotals.input)} / 输出 ${fmtNumber(usageTotals.output)}`} />
      </div>
      <TokenUsageChart rows={usage} />
      <Section title="最近消息投递" description="消息发往 QQ 后的回执状态；失败项可在“任务与投递”中重试">
        <DataTable>
          <thead><tr><th>投递</th><th>目标</th><th>状态</th><th>尝试</th><th>最后错误</th></tr></thead>
          <tbody>{deliveries.slice(0, 8).map((delivery) => <tr key={delivery.delivery_id}><td><code>{delivery.handle ?? `delivery#${delivery.delivery_id}`}</code></td><td>{delivery.scope_key ?? delivery.conversation_id ?? '-'}</td><td><StatusBadge value={delivery.status} /></td><td>{fmtNumber(delivery.attempts)}</td><td className="truncate">{delivery.last_error || '-'}</td></tr>)}</tbody>
        </DataTable>
        {!deliveries.length && <EmptyState>还没有消息投递记录</EmptyState>}
      </Section>
      <div className="overview-footnote">当前表情库存 {fmtNumber(media.counts?.stickers)} 个 · 工具调用 {fmtNumber(totals.tool_calls)} 次 · 工具失败 {fmtNumber(totals.tool_failures)} 次</div>
    </>
  )
}

export function UsageView({ plane }: { plane: Plane }) {
  const usage = rows(plane.data.usage?.items)
  const totals = usage.reduce((sum, item) => ({
    calls: sum.calls + Number(item.calls ?? 0),
    input: sum.input + Number(item.input_tokens ?? 0),
    output: sum.output + Number(item.output_tokens ?? 0),
    cached: sum.cached + Number(item.cached_tokens ?? 0),
  }), { calls: 0, input: 0, output: 0, cached: 0 })
  return (
    <>
      <PageHeader title="模型用量" description="按日期、会话和来源查看模型调用与 Token 消耗" action={<RefreshButton loading={plane.loading.has('usage')} onClick={() => void plane.refresh('usage')} />} />
      <div className="metric-grid compact">
        <Metric label="总 Token" value={fmtNumber(totals.input + totals.output)} hint="输入 + 输出，不重复计算缓存命中" />
        <Metric label="输入 Token" value={fmtNumber(totals.input)} />
        <Metric label="输出 Token" value={fmtNumber(totals.output)} />
        <Metric label="模型调用" value={fmtNumber(totals.calls)} hint={`缓存命中 ${fmtNumber(totals.cached)}`} />
      </div>
      <TokenUsageChart rows={usage} />
      <Section title="用量明细" description="同一天可能按群、私聊和系统任务拆成多条记录">
        <DataTable><thead><tr><th>日期</th><th>Scope</th><th>来源</th><th>调用</th><th>输入 Token</th><th>输出 Token</th><th>缓存命中</th></tr></thead><tbody>{usage.map((item, index) => <tr key={`${item.day}-${item.scope_key}-${item.source}-${index}`}><td>{item.day}</td><td><code>{item.scope_key || '-'}</code></td><td>{item.source || '-'}</td><td>{fmtNumber(item.calls)}</td><td>{fmtNumber(item.input_tokens)}</td><td>{fmtNumber(item.output_tokens)}</td><td>{fmtNumber(item.cached_tokens)}</td></tr>)}</tbody></DataTable>
        {!usage.length && <EmptyState>当前统计周期没有模型用量</EmptyState>}
      </Section>
    </>
  )
}

export function GroupsView({ plane }: { plane: Plane }) {
  const payload = plane.data.groups ?? {}
  const profiles = rows(payload.profiles)
  const groups = rows(payload.items)
  const options = [
    { value: '', label: `跟随全局 (${payload.default?.profile ?? '-'})` },
    ...profiles.filter((profile) => profile.configured).map((profile) => ({ value: profile.name, label: `${profile.name} · ${profile.model}` })),
  ]
  const setGroupModel = (groupId: number, profile: string) => plane.mutate('groups', `/group-models/${groupId}/default`, 'PUT', { profile: profile || null }, ['groups', 'overview'])
  const setUserModel = (groupId: number, userId: number, profile: string) => plane.mutate('groups', `/group-models/${groupId}/users/${userId}`, 'PUT', { profile: profile || null }, ['groups', 'overview'])
  return (
    <>
      <PageHeader title="模型、群与用户" description="统一调配群友模型，同时保留管理员个人配置" action={<RefreshButton loading={plane.loading.has('groups')} onClick={() => void plane.refresh('groups')} />} />
      <Section title="可用模型" description="密钥只在服务端使用，控制台不会返回凭据">
        <div className="model-strip">
          {profiles.map((profile) => <div className="model-item" key={profile.name}><div><strong>{profile.name}</strong><code>{profile.model}</code></div><StatusBadge value={profile.configured ? 'configured' : 'unconfigured'} label={profile.configured ? '可用' : '未配置'} /></div>)}
        </div>
      </Section>
      <div className="group-list">
        {groups.map((group) => (
          <section className="group-card" key={group.group_id}>
            <div className="group-head">
              <div><span className="eyebrow">QQ群</span><h2>{group.group_id}</h2><p>当前生效：{group.default_profile} · {group.default_model}</p></div>
              <div className="group-actions">
                <Toggle checked={Boolean(group.enabled)} label={group.enabled ? '群已启用' : '群已关闭'} onChange={(enabled) => void plane.mutate('groups', `/group-models/${group.group_id}/enabled`, 'PUT', { enabled }, ['groups', 'overview'])} />
                <Toggle checked={Boolean(group.vision_auto_describe)} label="自动识图" onChange={(enabled) => void plane.mutate('groups', `/group-models/${group.group_id}/vision-auto-describe`, 'PUT', { enabled }, ['groups', 'media'])} />
              </div>
            </div>
            <div className="control-row">
              <label><span>其他群友统一模型</span><DraftSelect ariaLabel={`群 ${group.group_id} 统一模型`} value={group.dynamic_group_profile ?? ''} options={options} onCommit={(value) => setGroupModel(group.group_id, value)} /></label>
              <span className="source-note">来源：{group.group_default_source}</span>
            </div>
            <MemberTable title="我自己" members={rows(group.admins)} groupId={group.group_id} options={options} onCommit={setUserModel} />
            <MemberTable title="其他群友" members={rows(group.members)} groupId={group.group_id} options={options} onCommit={setUserModel} collapsed />
          </section>
        ))}
        {!groups.length && <EmptyState>还没有发现任何群</EmptyState>}
      </div>
    </>
  )
}

function MemberTable({ title, members, groupId, options, onCommit, collapsed = false }: { title: string; members: any[]; groupId: number; options: Array<{ value: string; label: string }>; onCommit: (groupId: number, userId: number, value: string) => Promise<unknown>; collapsed?: boolean }) {
  return (
    <details className="member-panel" open={!collapsed}>
      <summary>{title}<span>{members.length} 人</span></summary>
      <DataTable>
        <thead><tr><th>用户</th><th>QQ</th><th>个人模型</th><th>当前生效</th></tr></thead>
        <tbody>{members.map((member) => <tr key={member.user_id}><td><strong>{member.display_name || member.nickname || `QQ ${member.user_id}`}</strong></td><td><code>{member.user_id}</code></td><td><DraftSelect ariaLabel={`${member.user_id} 个人模型`} value={member.explicit_profile ?? ''} options={[{ value: '', label: '跟随群统一模型' }, ...options.filter((item) => item.value)]} onCommit={(value) => onCommit(groupId, member.user_id, value)} /></td><td><StatusBadge value="active" label={member.effective_profile} /></td></tr>)}</tbody>
      </DataTable>
      {!members.length && <EmptyState>暂无已观察到的用户</EmptyState>}
    </details>
  )
}

export function TasksView({ plane }: { plane: Plane }) {
  const tasks = rows(plane.data.tasks?.items)
  const jobs = rows(plane.data.jobs?.items)
  const deliveries = rows(plane.data.deliveries?.items)
  return (
    <>
      <PageHeader title="任务与投递" description="前台 Agent、持久任务和消息投递的统一操作面" action={<RefreshButton onClick={() => void plane.refreshMany(['tasks', 'jobs', 'deliveries'])} />} />
      <Section title="运行中的 Agent" description="取消会触发当前任务的取消路径">
        <DataTable><thead><tr><th>任务</th><th>会话</th><th>摘要</th><th>耗时</th><th></th></tr></thead><tbody>{tasks.map((task) => <tr key={task.task_id}><td><code>{task.task_id}</code></td><td>{task.conversation_id}</td><td>{task.summary}</td><td>{fmtDuration(task.elapsed_seconds)}</td><td className="actions"><button className="icon-button danger" title="取消任务" onClick={() => void plane.mutate('tasks', `/tasks/${task.task_id}/cancel`, 'POST', {}, ['tasks'])}><Square size={15} /></button></td></tr>)}</tbody></DataTable>
        {!tasks.length && <EmptyState>当前没有正在运行的 Agent</EmptyState>}
      </Section>
      <Section title="持久任务" description="后台执行、重试和租约状态">
        <DataTable><thead><tr><th>任务</th><th>类型</th><th>范围</th><th>状态</th><th>尝试</th><th>更新时间</th><th></th></tr></thead><tbody>{jobs.map((job) => <tr key={job.job_id}><td><code>{job.handle}</code></td><td>{job.kind}</td><td>{job.scope_key || '-'}</td><td><StatusBadge value={job.status} /></td><td>{job.attempts}/{job.max_attempts}</td><td>{fmtTime(job.updated_at)}</td><td className="actions"><button className="icon-button" title="重试任务" onClick={() => void plane.mutate('jobs', `/jobs/${job.job_id}/retry`, 'POST', {}, ['jobs'])}><RotateCcw size={15} /></button><button className="icon-button danger" title="取消任务" onClick={() => void plane.mutate('jobs', `/jobs/${job.job_id}/cancel`, 'POST', {}, ['jobs'])}><Ban size={15} /></button></td></tr>)}</tbody></DataTable>
      </Section>
      <Section title="消息投递" description="失败投递可重试，未知结果不会自动重复发送">
        <DataTable><thead><tr><th>ID</th><th>目标</th><th>状态</th><th>尝试</th><th>错误</th><th></th></tr></thead><tbody>{deliveries.map((delivery) => <tr key={delivery.delivery_id}><td><code>{delivery.handle ?? `delivery#${delivery.delivery_id}`}</code></td><td>{delivery.scope_key ?? delivery.conversation_id ?? '-'}</td><td><StatusBadge value={delivery.status} /></td><td>{delivery.attempts ?? 0}</td><td className="truncate">{delivery.last_error || '-'}</td><td className="actions"><button className="icon-button" title="重试投递" onClick={() => void plane.mutate('deliveries', `/deliveries/${delivery.delivery_id}/retry`, 'POST', {}, ['deliveries'])}><Play size={15} /></button><button className="icon-button danger" title="取消投递" onClick={() => void plane.mutate('deliveries', `/deliveries/${delivery.delivery_id}/cancel`, 'POST', {}, ['deliveries'])}><X size={15} /></button></td></tr>)}</tbody></DataTable>
      </Section>
    </>
  )
}

export function ToolsView({ plane }: { plane: Plane }) {
  const tools = rows(plane.data.tools?.items)
  return (
    <>
      <PageHeader title="工具权限" description="开关直接控制下一轮 Agent 可见的 Tool Call 清单" action={<RefreshButton onClick={() => void plane.refresh('tools')} />} />
      <Section title="宿主工具策略" description="风险、幂等、副作用、超时和人工批准由宿主控制">
        <DataTable><thead><tr><th>工具</th><th>启用</th><th>风险</th><th>幂等</th><th>执行方式</th><th>超时</th><th>副作用</th></tr></thead><tbody>{tools.map((tool) => <tr key={tool.name}><td><strong>{tool.name}</strong></td><td><Toggle checked={Boolean(tool.enabled)} label={tool.enabled ? '已启用' : '已停用'} onChange={(enabled) => void plane.mutate('tools', `/tools/${tool.name}/enabled`, 'PUT', { enabled }, ['tools'])} /></td><td><StatusBadge value={tool.risk} /></td><td>{tool.idempotency}</td><td>{tool.execution_mode}</td><td>{tool.timeout_seconds}s</td><td className="capabilities">{rows(tool.side_effects).map((effect) => <span key={effect}>{effect}</span>)}</td></tr>)}</tbody></DataTable>
      </Section>
    </>
  )
}

export function TracesView({ plane }: { plane: Plane }) {
  const traces = rows(plane.data.traces?.items)
  const plans = rows(plane.data.contextPlans?.items)
  return (
    <>
      <PageHeader title="Trace 与上下文" description="每个回合的模型路由、工具调用、Token 和上下文决策" action={<RefreshButton onClick={() => void plane.refreshMany(['traces', 'contextPlans', 'observability'])} />} />
      <Section title="最近 Trace" description="Trace ID 可以关联模型、工具和投递日志">
        <DataTable><thead><tr><th>Trace ID</th><th>回合</th><th>模型</th><th>状态</th><th>耗时</th><th>工具</th><th>Token</th></tr></thead><tbody>{traces.map((trace) => <tr key={trace.trace_id}><td><code>{String(trace.trace_id).slice(0, 12)}</code></td><td>{trace.turn_handle}</td><td>{trace.profile}<small className="cell-sub">{trace.model}</small></td><td><StatusBadge value={trace.status} /></td><td>{fmtDuration(trace.duration_seconds)}</td><td>{trace.tool_call_count} / {trace.tool_failures} 失败</td><td>{fmtNumber(trace.total_tokens)}</td></tr>)}</tbody></DataTable>
      </Section>
      <Section title="上下文决策" description="焦点消息、相关候选和 Reranker 置信度">
        <DataTable><thead><tr><th>回合</th><th>范围</th><th>焦点</th><th>置信度</th><th>理由</th><th>状态</th></tr></thead><tbody>{plans.map((plan) => <tr key={`${plan.scope_key}-${plan.turn_handle}`}><td>{plan.turn_handle}</td><td><code>{plan.scope_key}</code></td><td>msg#{plan.focus_message_id}</td><td>{Math.round(Number(plan.confidence ?? 0) * 100)}%</td><td className="capabilities">{rows(plan.reason_codes).map((reason) => <span key={reason}>{reason}</span>)}</td><td><StatusBadge value={plan.status} /></td></tr>)}</tbody></DataTable>
      </Section>
    </>
  )
}

export function DatabasesView({ plane }: { plane: Plane }) {
  const database = plane.data.databases ?? {}
  const nodes = rows(database.nodes)
  return (
    <>
      <PageHeader title="数据库" description="主库、备用节点、连接池和复制延迟" action={<RefreshButton onClick={() => void plane.refresh('databases')} />} />
      <div className="database-summary"><Metric label="整体状态" value={<StatusBadge value={database.overall} />} hint={`写节点：${database.writable_node ?? '-'}`} /><Metric label="连接池" value={`${database.pool?.available ?? 0}/${database.pool?.size ?? 0}`} hint={`${database.pool?.waiting ?? 0} 个等待`} /><Metric label="检查时间" value={fmtTime(database.checked_at)} /></div>
      <div className="database-grid">{nodes.map((node) => <section className={`database-card ${node.status}`} key={node.name}><div className="database-title"><Database size={19} /><div><h2>{node.name}</h2><code>{node.host}:{node.port}</code></div><StatusBadge value={node.status} /></div><dl><div><dt>角色</dt><dd>{node.role}</dd></div><div><dt>写入</dt><dd>{node.writable ? '可写' : '只读'}</dd></div><div><dt>连接延迟</dt><dd>{node.latency_ms ?? '-'} ms</dd></div><div><dt>数据库大小</dt><dd>{fmtBytes(node.database_size_bytes)}</dd></div><div><dt>复制延迟</dt><dd>{node.replication_lag_seconds ?? '-'} s</dd></div><div><dt>PostgreSQL</dt><dd>{node.server_version ?? '-'}</dd></div></dl>{node.error && <p className="error-text">{node.error}</p>}</section>)}</div>
    </>
  )
}

export function SandboxesView({ plane }: { plane: Plane }) {
  const payload = plane.data.sandboxes ?? {}
  const sandboxes = rows(payload.items)
  return (
    <>
      <PageHeader title="沙盒" description="临时工作区、资源使用和 Agent 当前任务" action={<RefreshButton onClick={() => void plane.refresh('sandboxes')} />} />
      <div className="metric-grid compact"><Metric label="沙盒数量" value={sandboxes.length} /><Metric label="活动命令" value={fmtNumber(payload.active_commands)} /><Metric label="Docker" value={<StatusBadge value={payload.available ? 'online' : 'offline'} />} /></div>
      <div className="sandbox-grid">{sandboxes.map((sandbox) => <section className="sandbox-card" key={sandbox.sandbox_id}><div className="sandbox-head"><div><h2>{sandbox.sandbox_id}</h2><code>{sandbox.owner}</code></div><StatusBadge value={rows(sandbox.activities).length ? 'running' : 'ready'} /></div><dl><div><dt>工作区</dt><dd>{sandbox.workspace ?? '/workspace'}</dd></div><div><dt>镜像</dt><dd>{sandbox.image ?? '-'}</dd></div><div><dt>内存</dt><dd>{fmtBytes(sandbox.memory_bytes)}</dd></div></dl>{rows(sandbox.activities).map((activity) => <div className="command-row" key={activity.command}><code>{activity.command}</code><span>{fmtDuration(activity.elapsed_seconds)}</span></div>)}{rows(sandbox.agent_tasks).map((task) => <div className="task-chip" key={task.task_id}>{task.summary}</div>)}</section>)}</div>
      {!sandboxes.length && <EmptyState>当前没有沙盒，任务结束后的沙盒会自动销毁</EmptyState>}
    </>
  )
}

export function MediaView({ plane }: { plane: Plane }) {
  const media = plane.data.media ?? {}
  const items = rows(media.items)
  const sources = rows(plane.data.sources?.items)
  const review = (mediaId: number, state: string) => plane.mutate('media', `/media/${mediaId}/review`, 'PUT', { state }, ['media', 'stickers'])
  return (
    <>
      <PageHeader title="媒体审核" description="普通图片不长期保存；表情候选经过识图、安全判断和人工审核" action={<RefreshButton onClick={() => void plane.refreshMany(['media', 'stickers', 'sources'])} />} />
      <div className="metric-grid compact"><Metric label="可发表情" value={fmtNumber(media.counts?.stickers)} /><Metric label="占用空间" value={fmtBytes(media.counts?.bytes)} /><Metric label="处理队列" value={fmtNumber(media.counts?.queued)} /><Metric label="失败任务" value={fmtNumber(media.counts?.failed)} /></div>
      <Section title="表情候选" description={`识图模型：${media.vision_profile ?? '-'}`}>
        <DataTable><thead><tr><th>媒体</th><th>标签</th><th>识图模型</th><th>安全</th><th>发送</th><th>审核</th></tr></thead><tbody>{items.map((item) => <tr key={item.media_id}><td><code>media#{item.media_id}</code><small className="cell-sub">{fmtBytes(item.byte_size)}</small></td><td><strong>{item.summary || '未命名'}</strong><div className="capabilities">{jsonRows(item.emotions_json).slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}</div></td><td>{item.vision_model}</td><td><StatusBadge value={item.safety} /></td><td>{item.enabled && !item.banned ? `已启用 · ${item.times_sent} 次` : item.banned ? '已拒绝' : '未启用'}</td><td className="actions"><button className="icon-button success" title="批准并允许发送" onClick={() => void review(item.media_id, 'approved')}><Check size={15} /></button><button className="icon-button" title="保留待审" onClick={() => void review(item.media_id, 'pending')}><Clock3 size={15} /></button><button className="icon-button danger" title="拒绝并禁止发送" onClick={() => void review(item.media_id, 'rejected')}><X size={15} /></button></td></tr>)}</tbody></DataTable>
      </Section>
      <Section title="分享内容" description="B站、小红书及其他平台的帖子和视频解析记录">
        <DataTable><thead><tr><th>平台</th><th>标题</th><th>类型</th><th>状态</th><th>更新时间</th></tr></thead><tbody>{sources.map((source) => <tr key={source.source_id}><td>{source.platform}</td><td>{source.title || source.canonical_url}<ExternalLink className="inline-icon" size={13} /></td><td>{source.content_kind}</td><td><StatusBadge value={source.status} /></td><td>{fmtTime(source.updated_at)}</td></tr>)}</tbody></DataTable>
      </Section>
    </>
  )
}

export function AuditView({ plane }: { plane: Plane }) {
  const payload = plane.data.audit ?? {}
  const items = rows(payload.items)
  return (
    <>
      <PageHeader title="审计记录" description="所有控制台写操作都带资源版本、操作者、目标和结果" action={<RefreshButton onClick={() => void plane.refreshMany(['audit', 'versions'])} />} />
      <div className="audit-banner"><ShieldCheck size={20} /><div><strong>{payload.persistent ? 'PostgreSQL 持久审计已启用' : '当前使用进程内审计'}</strong><p>乐观并发会拒绝基于旧版本提交的修改。</p></div></div>
      <Section title="最近修改">
        <DataTable><thead><tr><th>时间</th><th>资源版本</th><th>动作</th><th>目标</th><th>操作者</th><th>结果</th></tr></thead><tbody>{items.map((item) => <tr key={item.audit_id}><td>{fmtTime(item.created_at)}</td><td><code>{item.resource_key}@{item.resource_version}</code></td><td>{item.action}</td><td>{item.target || '-'}</td><td>{item.actor}</td><td><StatusBadge value={item.status} /></td></tr>)}</tbody></DataTable>
      </Section>
    </>
  )
}

export function ObservabilityView({ plane }: { plane: Plane }) {
  const data = plane.data.observability ?? {}
  const process = data.process ?? {}
  const alerts = rows(data.alertmanager?.items)
  return (
    <>
      <PageHeader title="可观测性" description="Prometheus、告警、阶段延迟、模型降级和工具表现" action={<RefreshButton onClick={() => void plane.refresh('observability')} />} />
      <div className="metric-grid"><Metric label="Prometheus" value={<StatusBadge value={data.prometheus?.available ? 'online' : 'offline'} />} hint={data.prometheus?.url ?? '未配置'} /><Metric label="活动告警" value={alerts.length} /><Metric label="模型请求" value={fmtNumber(process.totals?.model_requests)} /><Metric label="降级路由" value={fmtNumber(rows(process.fallback_routes).length)} /></div>
      <Section title="模型延迟"><DataTable><thead><tr><th>模型</th><th>请求</th><th>失败</th><th>P50</th><th>P95</th></tr></thead><tbody>{rows(process.models).map((model) => <tr key={model.profile ?? model.model}><td>{model.profile ?? model.model}</td><td>{fmtNumber(model.requests)}</td><td>{fmtNumber(model.failures)}</td><td>{model.p50_ms ?? '-'} ms</td><td>{model.p95_ms ?? '-'} ms</td></tr>)}</tbody></DataTable></Section>
      <Section title="活动告警">{alerts.length ? alerts.map((alert) => <div className="alert-row" key={alert.fingerprint ?? alert.name}><CircleAlert size={17} /><div><strong>{alert.name}</strong><p>{alert.summary || alert.description}</p></div><StatusBadge value={alert.severity} /></div>) : <EmptyState>当前没有活动告警</EmptyState>}</Section>
    </>
  )
}

const HELP_SECTIONS = [
  {
    title: '概览与用量',
    items: [
      ['概览', '查看服务、任务、沙盒、Token 趋势和最近投递。把鼠标停在趋势图某一天，即可看到输入、输出、缓存命中、总 Token 和调用次数。'],
      ['模型用量', '切换 90、30、14 天窗口；下方明细按日期、群聊或私聊 Scope、调用来源拆分，便于定位费用来自哪里。'],
      ['可观测性', '查看 Prometheus、活动告警、模型请求失败和 P50/P95 延迟。P95 很高通常说明少量请求明显变慢。'],
    ],
  },
  {
    title: '运行与排障',
    items: [
      ['任务与投递', '查看当前 Agent、后台持久任务和 QQ 消息投递。右侧方形按钮取消任务，旋转箭头重试任务，播放按钮重试失败投递。'],
      ['Trace 与上下文', '用 Trace ID 串起一次回答的模型、工具、Token 和耗时；上下文决策显示“你觉得呢”等追问最终关联了哪条消息及置信度。'],
      ['数据库', '查看 h610 主库和备用节点、连接池、延迟及复制状态。出现 offline 或 degraded 时先看节点错误，不要直接清数据。'],
      ['沙盒', '查看临时容器、正在执行的命令、内存和 Agent 任务。任务完成后沙盒自动销毁，因此这里为空通常是正常状态。'],
    ],
  },
  {
    title: '配置与内容',
    items: [
      ['模型与群友', '群开关决定机器人是否处理该群消息；“其他群友统一模型”设置默认模型，“我自己”和成员详情可覆盖到个人。下拉修改会立即提交并留下审计记录。'],
      ['工具权限', '开关决定下一轮 Agent 能否看到该工具。风险、幂等、副作用和超时由宿主执行器强制控制；关闭后不会中断已经开始的调用。'],
      ['媒体审核', '审核识图 worker 生成的表情候选：勾号批准发送，时钟保留待审，叉号拒绝。普通图片不长期保存；分享内容区展示各平台帖子与视频解析状态。'],
      ['审计记录', '查看谁在何时修改了哪个资源及版本。若提交时版本已经过期，控制台会拒绝覆盖并自动拉取最新版。'],
    ],
  },
  {
    title: '实时更新规则',
    items: [
      ['实时状态', '右上角“实时”表示 SSE 已连接。后台只增量更新发生变化的资源，编辑中的下拉框不会因刷新而关闭。'],
      ['手动刷新', '侧栏“刷新数据”或右上角刷新按钮会重新拉取全部面板；用于刚部署完成、网络恢复或怀疑数据未同步时。'],
      ['安全边界', '管理 Token 只保存在当前浏览器本地，模型密钥不会返回前端。危险工具仍需宿主批准，数据库和媒体操作都有审计。'],
    ],
  },
]

export function HelpView() {
  return (
    <>
      <PageHeader title="使用说明" description="控制台每个功能的用途、操作方法和影响范围" />
      <div className="guide-intro"><strong>先看概览，异常时沿 Trace 排查，修改配置后去审计确认。</strong><span>这是最短的日常管理路径，也能避免在故障时盲目重启。</span></div>
      <div className="guide-grid">
        {HELP_SECTIONS.map((section) => <section className="guide-section" key={section.title}><h2>{section.title}</h2>{section.items.map(([title, description], index) => <article key={title}><span>{String(index + 1).padStart(2, '0')}</span><div><h3>{title}</h3><p>{description}</p></div></article>)}</section>)}
      </div>
    </>
  )
}
