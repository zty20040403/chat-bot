import { useEffect, useState, type ReactNode } from 'react'
import { ArrowRight, LoaderCircle, RefreshCw } from 'lucide-react'

export function PageHeader({
  title,
  description,
  action,
}: {
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </header>
  )
}

export function Section({
  title,
  description,
  children,
  action,
}: {
  title: string
  description?: string
  children: ReactNode
  action?: ReactNode
}) {
  return (
    <section className="section">
      <div className="section-heading">
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

export function StatusBadge({ value, label }: { value: unknown; label?: string }) {
  const text = String(label ?? value ?? 'unknown')
  const normalized = String(value ?? '').toLowerCase()
  const tone = ['active', 'healthy', 'online', 'ready', 'succeeded', 'safe', 'approved', 'configured', 'correct'].includes(normalized)
    ? 'success'
    : ['failed', 'offline', 'blocked', 'rejected', 'critical', 'error', 'off_topic'].includes(normalized)
      ? 'danger'
      : ['running', 'pending', 'degraded', 'warning', 'review', 'medium'].includes(normalized)
        ? 'warning'
        : 'neutral'
  return <span className={`badge ${tone}`}>{text}</span>
}

export function Metric({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {hint && <small>{hint}</small>}
    </div>
  )
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>
}

export function RefreshButton({ loading, onClick }: { loading?: boolean; onClick: () => void }) {
  return (
    <button className="icon-button" type="button" onClick={onClick} title="刷新当前数据" disabled={loading}>
      {loading ? <LoaderCircle className="spin" size={17} /> : <RefreshCw size={17} />}
    </button>
  )
}

export function ViewAllButton({ count, onClick }: { count: number; onClick: () => void }) {
  return (
    <button className="view-all-button" type="button" onClick={onClick}>
      <span>查看全部{count ? ` (${count})` : ''}</span>
      <ArrowRight size={14} />
    </button>
  )
}

export function Toggle({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean
  disabled?: boolean
  label: string
  onChange: (checked: boolean) => void
}) {
  return (
    <label className="toggle" title={label}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
      <span aria-hidden="true" />
      <b>{label}</b>
    </label>
  )
}

export function DraftSelect({
  value,
  options,
  onCommit,
  disabled,
  ariaLabel,
}: {
  value: string
  options: Array<{ value: string; label: string }>
  onCommit: (value: string) => Promise<unknown>
  disabled?: boolean
  ariaLabel: string
}) {
  const [draft, setDraft] = useState(value)
  const [focused, setFocused] = useState(false)
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (!focused && !saving) setDraft(value)
  }, [focused, saving, value])
  return (
    <select
      aria-label={ariaLabel}
      value={draft}
      disabled={disabled || saving}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      onChange={async (event) => {
        const next = event.target.value
        setDraft(next)
        setSaving(true)
        try {
          await onCommit(next)
        } catch {
          setDraft(value)
        } finally {
          setSaving(false)
        }
      }}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
}

export function DataTable({ children }: { children: ReactNode }) {
  return <div className="table-scroll"><table>{children}</table></div>
}

export function fmtNumber(value: unknown): string {
  const number = Number(value ?? 0)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN') : '0'
}

export function fmtBytes(value: unknown): string {
  let number = Number(value ?? 0)
  if (!Number.isFinite(number) || number <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let index = 0
  while (number >= 1024 && index < units.length - 1) {
    number /= 1024
    index += 1
  }
  return `${number.toFixed(index ? 1 : 0)} ${units[index]}`
}

export function fmtTime(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-'
  const numeric = Number(value)
  const date = Number.isFinite(numeric)
    ? new Date(numeric > 10_000_000_000 ? numeric : numeric * 1000)
    : new Date(String(value))
  if (!Number.isFinite(date.getTime())) return '-'
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date)
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? ''
  return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}:${part('second')}`
}

export function fmtDuration(value: unknown): string {
  const seconds = Math.max(Number(value ?? 0), 0)
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${Math.floor(seconds % 60)} 秒`
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`
}
