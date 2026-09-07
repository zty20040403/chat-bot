import { useEffect, useRef, useState } from 'react'
import { ShieldCheck, X } from 'lucide-react'
import { fmtTime } from './components'
import type { useControlPlane } from './useControlPlane'
import './ops-management.css'

type Plane = ReturnType<typeof useControlPlane>
type Target = { target_id: string; host_id: string; service_ref: string; target_hash: string }
type Policy = {
  target_id: string; mode: string; expires_at: number; interval_seconds: number;
  failure_threshold: number; max_actions: number; expected_target_hash: string;
  confirm_remediation: boolean; authorized_action: Record<string, string>;
}

export function GuardianControls({ plane }: { plane: Plane }) {
  const capability = plane.data.fleet?.execution_capabilities?.guardians
  const targets: Target[] = capability?.target_details ?? []
  const [targetId, setTargetId] = useState('')
  const [mode, setMode] = useState('observe')
  const [hours, setHours] = useState(3)
  const [count, setCount] = useState(1)
  const [action, setAction] = useState('service.restart')
  const [review, setReview] = useState<Policy | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const dialog = useRef<HTMLDialogElement>(null)
  const stale = review && targets.find((target) => target.target_id === review.target_id)?.target_hash !== review.expected_target_hash
  useEffect(() => { if (review && !dialog.current?.open) dialog.current?.showModal() }, [review])
  const save = async (policy: Policy) => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await plane.mutate('fleet', '/fleet/guardians', 'POST', policy, ['fleet'])
      setReview(null)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '守护创建失败') }
    finally { setBusy(false) }
  }
  return <>
    <form className="diagnostic-controls" onSubmit={(event) => {
      event.preventDefault()
      const target = targets.find((item) => item.target_id === targetId)
      if (!target || busy) return
      const policy: Policy = {
        target_id: target.target_id, mode, expires_at: Math.floor(Date.now() / 1000) + hours * 3600,
        interval_seconds: 60, failure_threshold: 3, max_actions: mode === 'remediate' ? count : 0,
        expected_target_hash: target.target_hash, confirm_remediation: mode === 'remediate',
        authorized_action: mode === 'remediate' ? {
          host_id: target.host_id, resource_ref: target.service_ref, operation: action,
        } : {},
      }
      setError('')
      if (mode === 'remediate') { setConfirmed(false); setReview(policy) }
      else void save(policy)
    }}>
      <label><span>探测目标</span><select required aria-label="守护目标" value={targetId} onChange={(event) => setTargetId(event.target.value)}><option value="">选择已登记目标</option>{targets.map((target) => <option key={target.target_id} value={target.target_id}>{target.target_id}</option>)}</select></label>
      <label><span>模式</span><select aria-label="守护模式" value={mode} onChange={(event) => setMode(event.target.value)}><option value="observe">只观察</option><option value="remediate" disabled={!capability?.remediation_available}>有限修复</option></select></label>
      <label><span>时长（小时）</span><input aria-label="守护时长" required type="number" min={1} max={744} step={1} value={hours} onChange={(event) => setHours(Number(event.target.value))} /></label>
      {mode === 'remediate' && <>
        <label><span>修复动作</span><select aria-label="修复动作" value={action} onChange={(event) => setAction(event.target.value)}><option value="service.restart">重启服务</option><option value="service.start">启动服务</option></select></label>
        <label><span>最多尝试次数</span><input aria-label="修复次数" required type="number" min={1} max={20} step={1} value={count} onChange={(event) => setCount(Number(event.target.value))} /></label>
      </>}
      <button className="command-button" type="submit" disabled={busy || !targets.length}><ShieldCheck size={15} />{mode === 'remediate' ? '审阅修复授权' : '创建只观察守护'}</button>
    </form>
    {error && !review && <p role="alert" className="ops-error">{error}</p>}
    {review && <dialog ref={dialog} className="ops-modal guardian-review" aria-labelledby="guardian-review-title" onCancel={(event) => { event.preventDefault(); if (!busy) setReview(null) }}>
      <header><h3 id="guardian-review-title">有限修复授权</h3><button className="icon-button" type="button" aria-label="关闭修复审阅" disabled={busy} onClick={() => setReview(null)}><X size={18} /></button></header>
      <div className="ops-modal-body">
        <p><strong>{review.authorized_action.host_id}</strong> · <code>{review.authorized_action.resource_ref}</code></p>
        <p>每 60 秒检查一次，连续 3 次失败后，{review.authorized_action.operation === 'service.restart' ? '重启' : '启动'}该服务，最多 {review.max_actions} 次。</p>
        <p>授权截止：<strong>{fmtTime(review.expires_at)}</strong></p>
        <label className="ops-confirm"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />我同意在以上范围内自动执行宿主机服务操作。暂停、取消或到期后不再派发新操作。</label>
        {stale && <p role="alert" className="ops-error">目标配置已变化，请关闭并重新审阅。</p>}
        {error && <p role="alert" className="ops-error">{error}</p>}
        <button className="command-button" type="button" disabled={busy || !confirmed || Boolean(stale)} onClick={() => void save(review)}><ShieldCheck size={15} />确认授权</button>
      </div>
    </dialog>}
  </>
}
