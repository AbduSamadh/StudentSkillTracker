import { type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { ClaimType, Figure } from '@/lib/types'

export function cx(...c: (string | false | null | undefined)[]): string {
  return c.filter(Boolean).join(' ')
}

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost'

export function Button({
  variant = 'primary',
  size = 'md',
  busy,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: 'sm' | 'md' | 'lg'; busy?: boolean }) {
  const styles: Record<Variant, string> = {
    primary: 'bg-brand text-brand-ink hover:bg-brand/90',
    secondary: 'bg-white text-slate-800 border border-slate-300 hover:bg-slate-50',
    danger: 'bg-red-700 text-white hover:bg-red-800',
    ghost: 'text-slate-700 hover:bg-slate-100',
  }
  const sizes = { sm: 'px-2.5 py-1 text-xs', md: 'px-3.5 py-2 text-sm', lg: 'px-5 py-3 text-base' }
  return (
    <button
      className={cx(
        'inline-flex items-center justify-center gap-2 rounded-lg font-medium transition disabled:opacity-50 disabled:cursor-not-allowed',
        styles[variant],
        sizes[size],
        className,
      )}
      disabled={busy || rest.disabled}
      aria-busy={busy || undefined}
      {...rest}
    >
      {busy && <Spinner small />}
      {children}
    </button>
  )
}

export function Spinner({ small }: { small?: boolean }) {
  return (
    <span
      role="status"
      aria-label="loading"
      className={cx('inline-block animate-spin rounded-full border-2 border-current border-e-transparent', small ? 'h-3.5 w-3.5' : 'h-6 w-6')}
    />
  )
}

export function Loading() {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-3 p-6 text-slate-600">
      <Spinner /> {t('common.loading')}
    </div>
  )
}

export function Card({ title, actions, children, className, inferred }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; inferred?: boolean }) {
  return (
    <section className={cx('card', inferred && 'claim-inferred', className)}>
      {(title || actions) && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          {title && <h2>{title}</h2>}
          {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-600">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  )
}

export function Badge({ children, tone = 'slate', className }: { children: ReactNode; tone?: 'slate' | 'green' | 'amber' | 'red' | 'blue' | 'brand'; className?: string }) {
  const tones = {
    slate: 'bg-slate-100 text-slate-700',
    green: 'bg-emerald-100 text-emerald-800',
    amber: 'bg-amber-100 text-amber-900',
    red: 'bg-red-100 text-red-800',
    blue: 'bg-sky-100 text-sky-800',
    brand: 'bg-brand-soft text-brand',
  }
  return <span className={cx('inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium', tones[tone], className)}>{children}</span>
}

/** Measured and inferred claims never render identically (spec §6.1). */
export function ClaimBadge({ type }: { type: ClaimType }) {
  const { t } = useTranslation()
  return type === 'measured' ? (
    <span title={t('common.measuredHint')} className="inline-flex items-center gap-1 rounded-sm border border-emerald-600 px-1.5 text-[11px] font-semibold uppercase tracking-wide text-emerald-800">
      <span aria-hidden>●</span> {t('common.measured')}
    </span>
  ) : (
    <span title={t('common.inferredHint')} className="inline-flex items-center gap-1 rounded-sm border border-dashed border-violet-600 px-1.5 text-[11px] font-semibold uppercase tracking-wide text-violet-800">
      <span aria-hidden>◌</span> {t('common.inferred')}
    </span>
  )
}

const LEVEL_TONES = ['bg-slate-100 text-slate-600', 'bg-sky-100 text-sky-800', 'bg-indigo-100 text-indigo-800', 'bg-emerald-100 text-emerald-800', 'bg-amber-100 text-amber-900']

export function LevelPill({ level }: { level: number }) {
  const { t } = useTranslation()
  return (
    <span className={cx('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold', LEVEL_TONES[level] ?? LEVEL_TONES[0])}>
      <span aria-hidden className="tracking-tighter">{'●'.repeat(level)}{'○'.repeat(Math.max(0, 4 - level))}</span>
      {t(`levels.${level}` as 'levels.0')}
    </span>
  )
}

/** A figure with its denominator, or "Withheld" with the reason — never silently blank. */
export function FigureTile({ figure, title, footer }: { figure: Figure; title?: string; footer?: ReactNode }) {
  const { t } = useTranslation()
  return (
    <div className={cx('card flex flex-col gap-2', figure.claim_type === 'inferred' && 'claim-inferred')}>
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm font-medium text-slate-600">{title ?? figure.label}</h3>
        <ClaimBadge type={figure.claim_type} />
      </div>
      {figure.withheld ? (
        <div>
          <div className="text-2xl font-bold text-amber-800">{t('common.withheld')}</div>
          <p className="text-xs text-amber-900">{t('common.withheldBecause', { reason: figure.withheld_reason })}</p>
        </div>
      ) : (
        <div>
          <div className="text-3xl font-bold text-slate-900">
            {figure.kind === 'percent' ? `${figure.value}%` : figure.kind === 'money' ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(Number(figure.value)) : figure.value}
            {figure.kind === 'money' && <span className="ms-1 text-base font-medium text-slate-500">{figure.unit}</span>}
          </div>
          <p className="text-xs text-slate-600">{figure.display}</p>
        </div>
      )}
      {footer}
    </div>
  )
}

export function Field({ label, hint, error, children, htmlFor }: { label: ReactNode; hint?: ReactNode; error?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className="text-sm font-medium text-slate-700">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
      {error && <p className="text-xs text-red-700" role="alert">{error}</p>}
    </div>
  )
}

export function TextInput({ label, hint, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode; hint?: ReactNode }) {
  const id = useId()
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <input id={id} className="input" {...rest} />
    </Field>
  )
}

export function SelectInput({ label, hint, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement> & { label: ReactNode; hint?: ReactNode }) {
  const id = useId()
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <select id={id} className="input" {...rest}>
        {children}
      </select>
    </Field>
  )
}

export function TextArea({ label, hint, ...rest }: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { label: ReactNode; hint?: ReactNode }) {
  const id = useId()
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <textarea id={id} className="input min-h-[96px]" {...rest} />
    </Field>
  )
}

export function Alert({ tone = 'info', children, title }: { tone?: 'info' | 'warn' | 'error' | 'success'; children: ReactNode; title?: ReactNode }) {
  const tones = {
    info: 'border-sky-300 bg-sky-50 text-sky-900',
    warn: 'border-amber-300 bg-amber-50 text-amber-900',
    error: 'border-red-300 bg-red-50 text-red-900',
    success: 'border-emerald-300 bg-emerald-50 text-emerald-900',
  }
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={cx('rounded-lg border p-3 text-sm', tones[tone])}>
      {title && <p className="font-semibold">{title}</p>}
      {children}
    </div>
  )
}

export function Empty({ children }: { children?: ReactNode }) {
  const { t } = useTranslation()
  return <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">{children ?? t('common.noData')}</p>
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: ReactNode }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div role="tablist" className="mb-4 flex flex-wrap gap-1 border-b border-slate-200">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={value === tab.id}
          onClick={() => onChange(tab.id)}
          className={cx(
            '-mb-px border-b-2 px-3 py-2 text-sm font-medium',
            value === tab.id ? 'border-brand text-brand' : 'border-transparent text-slate-600 hover:text-slate-900',
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}

export function TableWrap({ children }: { children: ReactNode }) {
  return <div className="-mx-4 overflow-x-auto px-4">{children}</div>
}

export function useToast(): [ReactNode, (msg: string, tone?: 'success' | 'error' | 'info') => void] {
  const [toast, setToast] = useState<{ msg: string; tone: 'success' | 'error' | 'info' } | null>(null)
  const node = toast ? (
    <div aria-live="polite" className="fixed bottom-4 inset-s-1/2 z-50 -translate-x-1/2 rtl:translate-x-1/2">
      <div className={cx('rounded-lg px-4 py-2 text-sm text-white shadow-lg', toast.tone === 'error' ? 'bg-red-700' : toast.tone === 'info' ? 'bg-slate-800' : 'bg-emerald-700')}>
        {toast.msg}
      </div>
    </div>
  ) : null
  return [
    node,
    (msg, tone = 'success') => {
      setToast({ msg, tone })
      window.setTimeout(() => setToast(null), 4000)
    },
  ]
}

/**
 * Meter for a single ratio (readiness, coverage). One sequential hue: fill is blue step 500,
 * the track a lighter step of the same ramp. An optional threshold tick marks the target.
 * Status is always also given in text by the caller — never by colour alone.
 */
export function Meter({ value, max = 100, threshold, label, valueText }: { value: number | null; max?: number; threshold?: number; label: string; valueText?: string }) {
  const pct = value === null ? 0 : Math.max(0, Math.min(100, (value / max) * 100))
  const tip = valueText ?? (value === null ? '—' : `${Math.round(pct)}%`)
  return (
    <div className="flex items-center gap-2" title={`${label}: ${tip}`}>
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value ?? undefined}
        aria-valuetext={tip}
        className="relative h-2.5 min-w-[96px] flex-1 overflow-hidden rounded-sm bg-[#cde2fb]"
      >
        <div className="absolute inset-y-0 inset-s-0 rounded-sm bg-[#256abf]" style={{ width: `${pct}%` }} />
        {threshold !== undefined && (
          <div aria-hidden className="absolute inset-y-[-2px] w-0.5 bg-slate-900" style={{ insetInlineStart: `calc(${(threshold / max) * 100}% - 1px)` }} />
        )}
      </div>
      <span className="w-12 text-end text-sm font-semibold tabular-nums text-slate-800">{tip}</span>
    </div>
  )
}
