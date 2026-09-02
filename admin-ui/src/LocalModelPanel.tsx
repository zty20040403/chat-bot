import { useState } from 'react'
import { Check, LoaderCircle, Play, Square, X } from 'lucide-react'
import { EmptyState, Section, StatusBadge, fmtNumber, fmtTime } from './components'
import type { JsonObject } from './api'
import type { useControlPlane } from './useControlPlane'

const STATES: Record<string, string> = {
  ready: '已开启', stopped: '已关闭', starting: '启动中', loading: '模型加载中',
  stopping: '停止中', failed: '启动失败', unreachable: '接口不可达',
  unavailable: '接口异常', unknown: '状态待确认',
}

export function LocalModelPanel({ plane }: { plane: ReturnType<typeof useControlPlane> }) {
  const data = plane.data.localModel
  const [busy, setBusy] = useState(false)
  const [confirmStop, setConfirmStop] = useState(false)
  const [notice, setNotice] = useState('')
  const [acceptedAction, setAcceptedAction] = useState<'start' | 'stop' | null>(null)
  if (!data?.configured) return <Section title="本地千问"><EmptyState>{data ? '未配置本地模型' : '正在读取状态'}</EmptyState></Section>
  const disabled = busy || !data.can_control || !data.control_reachable
  const gpu = Array.isArray(data.gpu) ? data.gpu : []
  const act = async (action: 'start' | 'stop') => {
    if (busy) return
    setBusy(true)
    setNotice('')
    setAcceptedAction(null)
    try {
      await plane.mutate('local-model', '/local-model/control', 'POST', { action, request_id: crypto.randomUUID() }, ['localModel'])
      setAcceptedAction(action)
      setConfirmStop(false)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '控制请求未确认')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Section title="本地千问" action={<div className="local-model-actions">
      <button type="button" className="feedback-button" disabled={disabled || ['ready', 'starting', 'loading', 'stopping'].includes(data.state)} onClick={() => void act('start')} title="启动 WSL 千问服务">
        {busy ? <LoaderCircle size={15} className="spin" /> : <Play size={15} />}启动千问
      </button>
      <button type="button" className="feedback-button" disabled={disabled || ['stopped', 'stopping'].includes(data.state)} onClick={() => setConfirmStop(true)} title="停止 WSL 千问服务"><Square size={15} />停止千问</button>
    </div>}>
      <div className="local-model-head"><div><strong>{data.profile}</strong><code>{data.model}</code></div><StatusBadge value={data.ready ? 'ready' : ['starting', 'loading', 'stopping'].includes(data.state) ? 'pending' : data.state} label={STATES[data.state] ?? '状态未知'} /></div>
      <dl className="local-model-metrics">
        <div><dt>简单聊天</dt><dd title="仅接管未指定个人、群或引用模型的简单聊天">{data.serving_simple_chat ? '千问优先' : data.simple_chat_selected ? '千问暂不接管' : '未配置为默认'}</dd></div>
        <div><dt>GPU 显存</dt><dd>{gpu.length ? gpu.map((item: JsonObject) => <span key={item.index}>GPU {item.index} · {(Number(item.used_mib) / 1024).toFixed(1)} / {(Number(item.total_mib) / 1024).toFixed(1)} GiB</span>) : '暂无数据'}</dd></div>
        <div title={`Bot 本次启动以来累计：${fmtTime(data.metrics_since)}；不含被健康检查跳过的请求`}><dt>千问请求数</dt><dd>{fmtNumber(data.request_count)}</dd></div>
        <div title="从发送请求到首个响应的平均耗时，流式回答不包含后续生成时间；本次 Bot 进程累计"><dt>平均首响应</dt><dd>{data.average_latency_ms == null ? '暂无数据' : `${fmtNumber(data.average_latency_ms)} ms`}</dd></div>
      </dl>
      <div className="local-model-meta"><span>{data.reason}{data.circuit_state === 'open' ? ' · 模型请求熔断中' : ''}</span><time>探测于 {fmtTime(data.checked_at)}</time></div>
      {!data.can_control && <p className="source-note">{data.control_reason}</p>}
      {data.can_control && !data.control_reachable && <p className="error-text">WSL 管理接口不可达，启停和显存数据暂不可用</p>}
      {confirmStop && <div className="local-model-confirm" role="alert"><span>停止千问可能中断正在生成的回答。确认停止？</span><button type="button" className="icon-button danger" title="确认停止千问" disabled={disabled} onClick={() => void act('stop')}><Check size={16} /></button><button type="button" className="icon-button" title="取消停止" disabled={busy} onClick={() => setConfirmStop(false)}><X size={16} /></button></div>}
      {notice && <p role="status" className="local-model-notice">{notice}</p>}
      {acceptedAction && <p role="status" className="local-model-notice">{data.state === 'failed' ? '服务操作失败，请检查 WSL 服务日志' : acceptedAction === 'start' ? data.ready ? '千问已就绪' : '启动请求已接受，等待模型就绪' : data.state === 'stopped' ? '千问已停止' : '停止请求已接受，等待服务退出'}</p>}
    </Section>
  )
}

export function ModelRouteSummary({ trace, detailed = false }: { trace: JsonObject; detailed?: boolean }) {
  const routing = Array.isArray(trace.model_routing) ? trace.model_routing : []
  return <div className="model-route-summary">
    <span>期望：<b>{trace.requested_profile ?? trace.profile ?? '-'}</b></span>
    <span title={trace.actual_model ?? trace.model}>实际：<b>{trace.actual_profile === '' ? '未成功响应' : trace.actual_profile ?? trace.profile ?? '-'}</b></span>
    {trace.routing_reason && <small className="model-route-reason">{trace.routing_reason}</small>}
    {detailed && routing.length > 0 && <details><summary>{routing.length} 次路由决策</summary><ol>{routing.map((item: JsonObject, index: number) => <li key={index}><span>{item.requested_profile} → {item.actual_profile || '未成功响应'}</span><small>{item.reason}</small>{(item.outcomes ?? []).map((attempt: JsonObject, attemptIndex: number) => <small key={attemptIndex}>{attempt.profile} · {attempt.status === 'succeeded' ? '成功' : attempt.status === 'skipped' ? '跳过' : '失败'} · {attempt.reason}</small>)}</li>)}</ol></details>}
  </div>
}
