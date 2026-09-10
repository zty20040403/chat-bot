import { useEffect, useState } from 'react'
import { Eye, RefreshCw, ShieldCheck, Square, X } from 'lucide-react'
import { DataTable, EmptyState, Section, StatusBadge, fmtTime } from './components'
import type { useControlPlane } from './useControlPlane'
import './ops-management.css'

type Plane = ReturnType<typeof useControlPlane>

const hostPhases: Record<string, string> = {
  submitting: '正在提交', submitted: '已提交，等待目标机检查', checking: '检查程序与执行环境',
  executing: '正在执行', recovering_receipt: '正在找回原任务回执', waiting_for_reboot: '等待主机重新上线',
  verified: '已验收', preflight_failed: '执行前检查失败', reboot_command_failed: '重启命令失败',
  outcome_unknown: '结果尚未确认', finished: '执行已结束', cancelled_before_submission: '提交前已取消',
  submission_rejected: '提交被拒绝，未执行命令',
  verifying_service: '正在复查服务状态', service_failed: '服务操作或复查失败',
  command_completed: '命令已结束，业务目标未验收',
}

export function OpsManagementPanel({ plane }: { plane: Plane }) {
  const fleet = plane.data.fleet ?? {}
  const capability = fleet.execution_capabilities?.ops_management
  const operations: Record<string, any>[] = fleet.operations?.items ?? []
  const [selected, setSelected] = useState<Record<string, any> | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [all, setAll] = useState(false)
  const [catalog, setCatalog] = useState<Record<string, any> | null>(null)
  const [catalogOpen, setCatalogOpen] = useState(false)
  // SSE updates the list, never silently substitutes the parameters being approved.
  const current = operations.find((item) => item.operation_id === selected?.operation_id)
  const stale = current && selected && (current.status !== selected.status || current.contract_hash !== selected.contract_hash || current.resource_version !== selected.resource_version)
  const selectedId = selected?.operation_id
  const awaitingApproval = selected?.status === 'awaiting_approval'
  useEffect(() => {
    if (!selectedId || awaitingApproval) return
    const controller = new AbortController()
    void plane.query(`/fleet/operations/${encodeURIComponent(selectedId)}`, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setSelected((previous) => previous?.operation_id === selectedId ? result : previous) })
      .catch(() => { /* Keep the last confirmed result during a connection outage. */ })
    return () => controller.abort()
  }, [plane.query, selectedId, awaitingApproval, current?.updated_at])
  const load = async (id: string) => {
    setBusy(true)
    setError('')
    try { setSelected(await plane.query(`/fleet/operations/${encodeURIComponent(id)}`)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '读取失败') }
    finally { setBusy(false) }
  }
  const action = async (name: 'approve' | 'cancel') => {
    if (!selected || busy) return
    setBusy(true)
    setError('')
    try {
      const result = await plane.mutate('fleet', `/fleet/operations/${encodeURIComponent(selected.operation_id)}/${name}`, 'POST', name === 'approve' ? {
        contract_hash: selected.contract_hash, resource_version: selected.resource_version,
      } : {}, ['fleet'])
      setSelected(result)
      } catch (reason) { setError(reason instanceof Error ? reason.message : '操作失败') }
    finally { setBusy(false) }
  }
  useEffect(() => {
    if (!selected) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busy) setSelected(null) }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [selected, busy])
  return <Section title="服务器操作">
    <div className="ops-toolbar">
      <StatusBadge value={capability?.available ? 'available' : 'unavailable'} />
      <span>{capability?.hosts?.join(' · ') || '管理接口未启用'}</span>
      <button type="button" className="command-button" disabled={!capability?.available || busy} onClick={async () => {
        setCatalogOpen((open) => !open)
        if (!catalog) {
          setBusy(true)
          try { setCatalog(await plane.query('/fleet/ops/catalog')) }
          catch (reason) { setError(reason instanceof Error ? reason.message : '目录读取失败') }
          finally { setBusy(false) }
        }
      }}><Eye size={15} />操作目录</button>
    </div>
    {error && <p role="alert" className="ops-error">{error}</p>}
    {catalogOpen && catalog && <div className="ops-catalog">{catalog.operations.map((item: any) => <div key={item.name}><code>{item.name}</code><span>{item.read_only ? '只读' : '需批准'}</span></div>)}</div>}
    <DataTable><thead><tr><th>更新时间</th><th>操作</th><th>目标</th><th>发起者</th><th>状态</th><th></th></tr></thead>
      <tbody>{(all ? operations : operations.slice(0, 5)).map((item) => <tr key={item.operation_id}>
        <td>{fmtTime(item.updated_at)}</td><td><code>{item.arguments?.op || item.operation}</code><small className="cell-sub">{item.operation_id}</small></td>
        <td>{item.host_id}<small className="cell-sub">{item.arguments?.params?.unit || item.arguments?.params?.repository || item.resource_ref}</small></td>
        <td>{item.actor_id}</td><td><StatusBadge value={item.status} />{item.result?.phase && <small className="cell-sub">{hostPhases[item.result.phase] || item.result.phase}</small>}</td>
        <td><button type="button" className="icon-button" title="审阅参数与执行结果" aria-label={`审阅 ${item.operation_id}`} disabled={busy} onClick={() => void load(item.operation_id)}><Eye size={15} /></button></td>
      </tr>)}</tbody></DataTable>
    {!operations.length && <EmptyState>暂无远程操作请求</EmptyState>}
    {operations.length > 5 && <button type="button" className="command-button" onClick={() => setAll(!all)}>{all ? '收起' : `全部 ${operations.length} 条`}</button>}
    {selected && <div className="ops-modal-backdrop"><div className="ops-modal" role="dialog" aria-modal="true" aria-label="服务器操作审阅">
      <header><div><h3>{selected.arguments?.op || selected.operation}</h3><small>{selected.operation_id}</small></div><button type="button" className="icon-button" aria-label="关闭审阅" disabled={busy} onClick={() => setSelected(null)}><X size={18} /></button></header>
      <div className="ops-modal-body">
        <div className="ops-toolbar"><strong>{selected.host_id}</strong><span>{selected.actor_id}</span><StatusBadge value={selected.status} /></div>
        <p>请求参数 · 版本 {selected.resource_version}</p><pre>{JSON.stringify(selected.arguments?.params ?? selected.arguments, null, 2)}</pre>
        <small className="ops-hash">批准绑定：{selected.contract_hash}</small>
        {selected.status === 'awaiting_approval' && <p>等待本次操作授权。</p>}
        {stale && <p role="status">状态已更新，请重新读取后操作。</p>}
        {error && <p role="alert" className="ops-error">{error}</p>}
        <div className="ops-toolbar">
          <button type="button" className="icon-button" title="重新读取请求与结果" aria-label="重新读取请求与结果" disabled={busy} onClick={() => void load(selected.operation_id)}><RefreshCw size={16} /></button>
          {selected.status === 'awaiting_approval' && <button type="button" className="command-button" disabled={busy || Boolean(stale)} onClick={() => void action('approve')}><ShieldCheck size={16} />执行</button>}
          {['awaiting_approval', 'queued', 'running', 'reconciling'].includes(selected.status) && <button type="button" className="command-button" disabled={busy || Boolean(stale)} onClick={() => void action('cancel')}><Square size={15} />{['running', 'reconciling'].includes(selected.status) ? '请求取消' : '取消请求'}</button>}
        </div>
        {selected.backend_operation_id && <p>远端任务：<code>{selected.backend_operation_id}</code></p>}
        {selected.error_code && <p className="ops-error">{selected.error_code}</p>}
        {selected.result?.phase && <p><strong>{hostPhases[selected.result.phase] || selected.result.phase}</strong></p>}
        {selected.result?.summary && <p>{selected.result.summary}</p>}
        {selected.result?.verification?.level === 'service_state' && <dl className="ops-evidence">
          <dt>操作前实例</dt><dd><code>{selected.result.verification.before?.invocation_id || '未运行'}</code></dd>
          <dt>操作后实例</dt><dd><code>{selected.result.verification.after?.invocation_id || '未运行'}</code></dd>
          <dt>当前服务状态</dt><dd>{selected.result.verification.current ? `${selected.result.verification.current.active_state}/${selected.result.verification.current.sub_state}` : '等待复查'}</dd>
          <dt>当前进程 PID</dt><dd>{selected.result.verification.current?.main_pid == null ? '尚未获取' : selected.result.verification.current.main_pid || '无运行进程'}</dd>
          <dt>最新复查时间</dt><dd>{fmtTime(selected.result.verification.current?.observed_at)}</dd>
          <dt>验收结论</dt><dd>{selected.result.verification.reason || '等待目标证据'}</dd>
          <dt>业务接口健康</dt><dd>未单独检测</dd>
        </dl>}
        {selected.result?.preflight?.evidence && <dl className="ops-evidence">
          <dt>执行身份</dt><dd>UID {selected.result.preflight.evidence.uid} · GID {selected.result.preflight.evidence.gid}</dd>
          <dt>工作目录</dt><dd><code>{selected.result.preflight.evidence.cwd?.resolved}</code></dd>
          <dt>实际程序</dt><dd>{(selected.result.preflight.evidence.programs ?? []).map((program: any) => <code key={program.requested}>{program.resolved}</code>)}</dd>
        </dl>}
        {selected.result?.verification?.before_boot_id && <dl className="ops-evidence">
          <dt>重启前开机编号</dt><dd><code>{selected.result.verification.before_boot_id}</code></dd>
          <dt>当前开机编号</dt><dd><code>{selected.result.verification.after_boot_id}</code></dd>
          <dt>验收时间</dt><dd>{fmtTime(selected.result.verification.observed_at)}</dd>
          <dt>重启验收</dt><dd>{selected.result.verification.verified ? '开机编号已改变' : '尚未观察到新的开机编号'}</dd>
        </dl>}
        {selected.result?.preflight_error && <p className="ops-error">{selected.result.preflight_error.error || selected.result.preflight_error.code}{selected.result.preflight_error.suggested_program && <> · 候选路径：<code>{selected.result.preflight_error.suggested_program}</code></>}</p>}
        <details open={selected.status !== 'awaiting_approval'}><summary>执行结果</summary><pre>{JSON.stringify(selected.result ?? {}, null, 2)}</pre></details>
        <DataTable><thead><tr><th>时间</th><th>事件</th><th>状态</th></tr></thead><tbody>{(selected.events ?? []).slice().reverse().map((event: any) => <tr key={event.sequence}><td>{fmtTime(event.created_at)}</td><td>{event.event_type}</td><td><StatusBadge value={event.status} /></td></tr>)}</tbody></DataTable>
      </div>
    </div></div>}
  </Section>
}
