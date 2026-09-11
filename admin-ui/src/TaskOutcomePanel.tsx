import { useEffect, useRef, useState } from 'react'
import { ArrowRight, FileSearch, Maximize2, X } from 'lucide-react'
import { fmtTime, StatusBadge } from './components'
import type { useControlPlane } from './useControlPlane'
import './task-outcome.css'

type Plane = ReturnType<typeof useControlPlane>
const labels: Record<string, string> = {
  passed: '已核实', completed: '已完成', committed: '已送达', succeeded: '执行成功',
  running: '进行中', waiting: '等待授权', waiting_external: '等待外部结果',
  pending: '待处理', unverified: '尚未验证', failed: '失败', partial: '部分完成',
  not_required: '无需授权', incomplete: '未完成', ambiguous: '回执待核对', resolved: '已返回',
}

export function TaskOutcomePanel({ detail, plane }: { detail: any; plane: Plane }) {
  const [expanded, setExpanded] = useState(false)
  const [selectedRef, setSelectedRef] = useState('')
  const [receipt, setReceipt] = useState<any>(null)
  const [error, setError] = useState('')
  const [tab, setTab] = useState('acceptance')
  const dialog = useRef<HTMLDialogElement>(null)
  const progress = detail.progress
  const taskId = detail.task.task_id
  const revision = detail.control?.revision

  useEffect(() => {
    if (expanded && !dialog.current?.open) dialog.current?.showModal()
    if (!expanded && dialog.current?.open) dialog.current?.close()
  }, [expanded])
  useEffect(() => {
    setReceipt(null); setError('')
    if (!selectedRef) return
    const controller = new AbortController()
    void plane.query(`/subagents/${taskId}/evidence/${encodeURIComponent(selectedRef)}`, controller.signal)
      .then(value => { if (!controller.signal.aborted) setReceipt(value) })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '读取证据失败') })
    return () => controller.abort()
  }, [taskId, revision, selectedRef, plane.query])
  if (!progress) return null

  const badge = (status: string) => <StatusBadge value={status} label={labels[status] ?? status} />
  const showEvidence = (ref: string) => { setSelectedRef(ref); setExpanded(true); setTab('evidence') }
  const rows = progress.acceptance?.criteria ?? []
  const stageList = <ol className="outcome-stages" aria-label="任务完整过程">{progress.stages.map((stage: any, index: number) =>
    <li key={stage.key}><div><strong>{stage.label}</strong>{badge(stage.status)}<small>{stage.detail}</small></div>
      {index < progress.stages.length - 1 && <ArrowRight size={15} aria-hidden="true" />}</li>)}</ol>
  const criteria = (all: boolean) => <div className="outcome-criteria">{(all ? rows : rows.slice(0, 5)).map((row: any) =>
    <article key={row.criterion_index}><div className="outcome-row-heading"><strong>{typeof row.criterion_index === 'number' ? `${row.criterion_index + 1}. ` : ''}{row.description}</strong>{badge(row.status)}</div>
      <p>{row.reason}</p>{row.detail?.before && row.detail?.after && <p className="outcome-comparison">
        {row.detail.host_id} {row.detail.mountpoint} · {(row.detail.before.available_bytes / 1024 ** 3).toFixed(2)} → {(row.detail.after.available_bytes / 1024 ** 3).toFixed(2)} GiB
        <small>{fmtTime(row.detail.before_at)} → {fmtTime(row.detail.after_at)}</small></p>}
      <div className="outcome-ref-list">{(row.evidence_refs ?? []).map((ref: string) => <button className="icon-button" key={ref}
        title={`查看 ${ref}`} aria-label={`查看 ${ref}`} onClick={() => showEvidence(ref)}><FileSearch size={16} /></button>)}</div></article>)}</div>
  const operations = <div className="outcome-operations">{(progress.operations ?? []).map((operation: any) =>
    <article key={`${operation.run_id}:${operation.call_id}`}><div className="outcome-row-heading"><strong>{operation.host_id || operation.remote_path || operation.tool_name || '工具调用'}</strong>{badge(operation.status)}</div>
      {operation.historical && <small>{operation.source_revision === revision ? '本轮授权与派发凭据' : `历史授权与派发凭据 · 修订 ${operation.source_revision ?? '-'}`}</small>}
      <time>{fmtTime(operation.updated_at)}</time><p>{operation.summary || operation.error || '等待最终操作回执'}</p>
      <details><summary>操作对象与命令</summary><pre>{JSON.stringify(operation.arguments, null, 2)}</pre></details>
      {operation.verification && <details><summary>复查证据</summary><pre>{JSON.stringify(operation.verification, null, 2)}</pre></details>}</article>)}</div>
  const findings = <div className="outcome-findings">{['findings', 'completed', 'authorization', 'next_verification'].map(key =>
    <section key={key}><h4>{{ findings: '发现的问题', completed: '完成的工作', authorization: '授权需求', next_verification: '仍需验证' }[key]}</h4>
      {(progress[key] ?? []).map((item: any, index: number) => <article key={`${item.run_id}:${index}`}><small>{item.step} · agent#{item.run_id}</small>
        <p>{typeof item.value === 'string' ? item.value : item.value.description || item.value.reason}</p>
        {key === 'authorization' && <pre>{JSON.stringify(item.value, null, 2)}</pre>}
        {(item.value?.evidence_refs ?? []).map((ref: string) => <button key={ref} className="icon-button" title={`查看 ${ref}`} aria-label={`查看 ${ref}`} onClick={() => showEvidence(ref)}><FileSearch size={16} /></button>)}
      </article>)}{!progress[key]?.length && <p className="muted">暂无记录</p>}</section>)}</div>

  return <section className="task-outcome">
    <header className="outcome-heading"><div><h3>任务完整过程</h3><small>修订 {revision} · {fmtTime(progress.updated_at)}</small></div>
      <button className="icon-button" title="展开任务过程与全部证据" aria-label="展开任务完整过程" onClick={() => setExpanded(true)}><Maximize2 size={17} /></button></header>
    {stageList}{criteria(false)}{!rows.length && <p className="muted">{detail.task.status === 'verifying' ? '正在逐项核对验收条件' : '验收结果尚未生成'}</p>}
    <dialog ref={dialog} className="outcome-dialog" aria-label="任务过程与证据" onCancel={() => setExpanded(false)} onClose={() => setExpanded(false)}>
      <header className="outcome-heading"><h3>{detail.task.handle} · 任务过程与证据</h3><button className="icon-button" title="关闭详情" aria-label="关闭任务过程详情" onClick={() => setExpanded(false)}><X size={18} /></button></header>
      {stageList}<div className="outcome-tabs" role="tablist" aria-label="任务详情分类">{[['acceptance', '逐项验收'], ['findings', '发现与交接'], ['operations', '操作与复查'], ['evidence', '原始证据']].map(([key, label]) =>
        <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{label}</button>)}</div>
      <div className="outcome-tab-content" role="tabpanel">
        {tab === 'acceptance' && criteria(true)}{tab === 'findings' && findings}{tab === 'operations' && operations}
        {tab === 'evidence' && <><label className="outcome-evidence-select">工具证据<select aria-label="选择工具证据" value={selectedRef} onChange={event => setSelectedRef(event.target.value)}>
          <option value="">选择一条证据</option>{(progress.evidence ?? []).map((item: any) => <option key={item.ref} value={item.ref}>{fmtTime(item.recorded_at)} · agent#{item.run_id} · {item.tool}</option>)}</select></label>
          {error && <p className="inline-error">{error}</p>}{selectedRef && !receipt && !error && <p>正在读取证据...</p>}
          {receipt && <><p>{receipt.tool_name} · {fmtTime(receipt.recorded_at)} · {receipt.complete ? '完整回执' : '内容过大，不可用于验收'}</p><code className="outcome-hash">SHA-256 {receipt.payload_hash}</code>
            <h4>实际工具参数</h4><pre>{JSON.stringify(receipt.arguments, null, 2)}</pre><h4>原始返回内容</h4><pre>{JSON.stringify(receipt.payload, null, 2)}</pre></>}
        </>}
      </div>
    </dialog>
  </section>
}
