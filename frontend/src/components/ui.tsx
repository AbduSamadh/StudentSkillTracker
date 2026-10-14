import {
  BadgeCheck,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Info,
  Inbox,
  type LucideIcon,
  Sparkles,
  TriangleAlert,
} from 'lucide-react'
import { type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { figureCaption, withheldReason } from '@/lib/figures'
import type { ClaimType, Figure } from '@/lib/types'

export type { LucideIcon }

export function cx(...c: (string | false | null | undefined)[]): string {
  return c.filter(Boolean).join(' ')
}

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost'
type Size = 'sm' | 'md' | 'lg'

const BUTTON_BASE =
  'inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50'
const BUTTON_VARIANTS: Record<Variant, string> = {
  primary: 'bg-brand text-brand-ink shadow-sm hover:bg-brand/90',
  secondary: 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50',
  danger: 'border border-red-200 bg-white text-red-700 hover:bg-red-50',
  ghost: 'text-slate-600 hover:bg-slate-100 hover:text-slate-900',
}
const BUTTON_SIZES: Record<Size, string> = {
  sm: 'min-h-8 px-3 py-1 text-sm',
  md: 'min-h-10 px-4 py-2 text-sm',
  lg: 'min-h-12 px-5 py-3 text-base',
}
const ICON_SIZES: Record<Size, string> = { sm: 'h-4 w-4', md: 'h-4 w-4', lg: 'h-5 w-5' }

export function buttonClass(variant: Variant = 'primary', size: Size = 'md', className?: string): string {
  return cx(BUTTON_BASE, BUTTON_VARIANTS[variant], BUTTON_SIZES[size], className)
}

export function Button({
  variant = 'primary',
  size = 'md',
  busy,
  icon: Icon,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size; busy?: boolean; icon?: LucideIcon }) {
  return (
    <button className={buttonClass(variant, size, className)} disabled={busy || rest.disabled} aria-busy={busy || undefined} {...rest}>
      {busy ? <Spinner small /> : Icon && <Icon aria-hidden className={ICON_SIZES[size]} />}
      {children}
    </button>
  )
}

/** A link that looks like a button (never a button inside a link). */
export function ButtonLink({
  to,
  variant = 'primary',
  size = 'md',
  icon: Icon,
  className,
  children,
}: {
  to: string
  variant?: Variant
  size?: Size
  icon?: LucideIcon
  className?: string
  children: ReactNode
}) {
  return (
    <Link to={to} className={buttonClass(variant, size, className)}>
      {Icon && <Icon aria-hidden className={ICON_SIZES[size]} />}
      {children}
    </Link>
  )
}

export function Spinner({ small }: { small?: boolean }) {
  return (
    <span
      role="status"
      aria-label="loading"
      className={cx('inline-block animate-spin rounded-full border-2 border-current border-e-transparent', small ? 'h-3.5 w-3.5' : 'h-5 w-5')}
    />
  )
}

export function Loading() {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-3 p-6 text-sm text-slate-500">
      <Spinner /> {t('common.loading')}
    </div>
  )
}

export function Card({
  title,
  description,
  icon: Icon,
  actions,
  children,
  className,
  inferred,
}: {
  title?: ReactNode
  description?: ReactNode
  icon?: LucideIcon
  actions?: ReactNode
  children: ReactNode
  className?: string
  inferred?: boolean
}) {
  return (
    <section className={cx('card', inferred && 'claim-inferred', className)}>
      {(title || actions) && (
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-2.5">
            {Icon && <Icon aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" />}
            <div className="min-w-0">
              {title && <h2>{title}</h2>}
              {description && <p className="mt-0.5 text-sm text-slate-500">{description}</p>}
            </div>
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  )
}

/** Every page opens with what it is and what it is for, in one plain sentence. */
export function PageHeader({
  icon: Icon,
  title,
  subtitle,
  actions,
  back,
}: {
  icon?: LucideIcon
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  back?: { to: string; label: string }
}) {
  return (
    <header className="mb-6">
      {back && (
        <Link to={back.to} className="mb-3 inline-flex items-center gap-1 text-sm font-medium text-slate-500 hover:text-slate-900">
          <ChevronLeft aria-hidden className="h-4 w-4 rtl:rotate-180" />
          {back.label}
        </Link>
      )}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3.5">
          {Icon && (
            <span aria-hidden className="mt-0.5 grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand">
              <Icon className="h-5 w-5" />
            </span>
          )}
          <div className="min-w-0">
            <h1>{title}</h1>
            {subtitle && <div className="mt-1 max-w-3xl text-[15px] leading-relaxed text-slate-600">{subtitle}</div>}
          </div>
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  )
}

/** A titled group of content on a page, outside any card. */
export function Section({ title, description, actions, children, className }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx('space-y-3', className)}>
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-lg">{title}</h2>
          {description && <p className="mt-0.5 text-sm text-slate-500">{description}</p>}
        </div>
        {actions}
      </div>
      {children}
    </section>
  )
}

type Tone = 'slate' | 'green' | 'amber' | 'red' | 'blue' | 'brand'

export function Badge({ children, tone = 'slate', className }: { children: ReactNode; tone?: Tone; className?: string }) {
  const tones: Record<Tone, string> = {
    slate: 'bg-slate-100 text-slate-700',
    green: 'bg-emerald-50 text-emerald-800 ring-1 ring-inset ring-emerald-600/15',
    amber: 'bg-amber-50 text-amber-900 ring-1 ring-inset ring-amber-600/20',
    red: 'bg-red-50 text-red-800 ring-1 ring-inset ring-red-600/15',
    blue: 'bg-sky-50 text-sky-800 ring-1 ring-inset ring-sky-600/15',
    brand: 'bg-brand-soft text-brand ring-1 ring-inset ring-brand/15',
  }
  return <span className={cx('inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium', tones[tone], className)}>{children}</span>
}

/** Measured and inferred claims never render identically (spec §6.1): a confirmed record is
 *  marked with a tick; anything the rules worked out has a dashed outline. Kept small so it
 *  informs without competing with the content. */
export function ClaimBadge({ type }: { type: ClaimType }) {
  const { t } = useTranslation()
  return type === 'measured' ? (
    <span title={t('common.measuredHint')} className="inline-flex shrink-0 items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
      <BadgeCheck aria-hidden className="h-3.5 w-3.5 text-emerald-600" /> {t('common.measured')}
    </span>
  ) : (
    <span title={t('common.inferredHint')} className="inline-flex shrink-0 items-center gap-1 rounded-full border border-dashed border-violet-300 px-2 py-0.5 text-xs font-medium text-violet-700">
      <Sparkles aria-hidden className="h-3.5 w-3.5" /> {t('common.inferred')}
    </span>
  )
}

const LEVEL_TONES = ['bg-slate-100 text-slate-600', 'bg-sky-50 text-sky-800', 'bg-indigo-50 text-indigo-800', 'bg-emerald-50 text-emerald-800', 'bg-amber-50 text-amber-900']

export function LevelPill({ level }: { level: number }) {
  const { t } = useTranslation()
  return (
    <span className={cx('inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold', LEVEL_TONES[level] ?? LEVEL_TONES[0])}>
      <span aria-hidden className="tracking-tighter">
        {'●'.repeat(level)}
        <span className="opacity-30">{'●'.repeat(Math.max(0, 4 - level))}</span>
      </span>
      {t(`levels.${level}` as 'levels.0')}
    </span>
  )
}

/** A figure with its denominator, or "Withheld" with the reason — never silently blank. */
export function FigureTile({ figure, title, footer, help }: { figure: Figure; title?: string; footer?: ReactNode; help?: ReactNode }) {
  const { t, i18n } = useTranslation()
  return (
    <div className={cx('card flex flex-col gap-3', figure.claim_type === 'inferred' && 'claim-inferred')}>
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-medium text-slate-600">{title ?? figure.label}</h3>
        <ClaimBadge type={figure.claim_type} />
      </div>
      {figure.withheld ? (
        <div>
          <div className="text-2xl font-semibold text-slate-700">{t('common.withheld')}</div>
          <p className="mt-1 text-sm text-slate-500">{t('common.withheldBecause', { reason: withheldReason(t, figure) })}</p>
        </div>
      ) : (
        <div>
          <div className="text-3xl font-semibold tracking-tight text-slate-900">
            {figure.kind === 'percent' ? `${figure.value}%` : figure.kind === 'money' ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(Number(figure.value)) : figure.value}
            {figure.kind === 'money' && <span className="ms-1 text-base font-medium text-slate-500">{figure.unit}</span>}
          </div>
          <p className="mt-1 text-sm text-slate-500">{figureCaption(t, figure, i18n.language)}</p>
        </div>
      )}
      {help && <p className="text-sm text-slate-600">{help}</p>}
      {footer && <div className="mt-auto">{footer}</div>}
    </div>
  )
}

export function Field({ label, hint, error, children, htmlFor }: { label: ReactNode; hint?: ReactNode; error?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-sm font-medium text-slate-800">
        {label}
      </label>
      {children}
      {hint && <p className="text-sm text-slate-500">{hint}</p>}
      {error && (
        <p className="text-sm text-red-700" role="alert">
          {error}
        </p>
      )}
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
    info: ['border-sky-200 bg-sky-50 text-sky-950', Info, 'text-sky-600'],
    warn: ['border-amber-200 bg-amber-50 text-amber-950', TriangleAlert, 'text-amber-600'],
    error: ['border-red-200 bg-red-50 text-red-950', CircleAlert, 'text-red-600'],
    success: ['border-emerald-200 bg-emerald-50 text-emerald-950', CircleCheck, 'text-emerald-600'],
  } as const
  const [box, Icon, iconTone] = tones[tone]
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={cx('flex gap-3 rounded-xl border p-3.5 text-sm leading-relaxed', box)}>
      <Icon aria-hidden className={cx('mt-0.5 h-4 w-4 shrink-0', iconTone)} />
      <div className="min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        {children}
      </div>
    </div>
  )
}

/** A quiet explanation of how something works, for the first time someone sees it. */
export function InfoNote({ children, icon: Icon = Info }: { children: ReactNode; icon?: LucideIcon }) {
  return (
    <p className="flex gap-2.5 rounded-xl bg-slate-100/70 p-3.5 text-sm leading-relaxed text-slate-600">
      <Icon aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
      <span>{children}</span>
    </p>
  )
}

/** Nothing here yet: say so, say why it matters, and offer the next step. */
export function EmptyState({ icon: Icon = Inbox, title, children, action }: { icon?: LucideIcon; title?: ReactNode; children?: ReactNode; action?: ReactNode }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-slate-300 bg-white/60 px-6 py-10 text-center">
      <span aria-hidden className="grid h-11 w-11 place-items-center rounded-full bg-slate-100 text-slate-400">
        <Icon className="h-5 w-5" />
      </span>
      <p className="font-medium text-slate-800">{title ?? t('common.noData')}</p>
      {children && <p className="max-w-md text-sm text-slate-500">{children}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function Empty({ children }: { children?: ReactNode }) {
  return <EmptyState title={children} />
}

/** A large shortcut to one task, used on home pages. */
export function ActionTile({ to, icon: Icon, title, description }: { to: string; icon: LucideIcon; title: string; description: string }) {
  return (
    <Link to={to} className="group card flex min-w-0 items-start gap-3.5 transition hover:border-brand/40 hover:shadow-raised">
      <span aria-hidden className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand">
        <Icon className="h-5 w-5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block font-semibold text-slate-900">{title}</span>
        <span className="mt-0.5 block text-sm text-slate-500">{description}</span>
      </span>
      <ChevronRight aria-hidden className="mt-3 h-4 w-4 shrink-0 text-slate-300 transition group-hover:text-brand rtl:rotate-180" />
    </Link>
  )
}

/** One row of "needs your attention": what is waiting, how many, and where to deal with it. */
export function TodoItem({ to, icon: Icon, count, label, action }: { to?: string; icon: LucideIcon; count: number; label: string; action?: string }) {
  const body = (
    <>
      <span aria-hidden className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-amber-50 text-amber-700">
        <Icon className="h-4 w-4" />
      </span>
      <span className="min-w-0 flex-1 text-sm font-medium text-slate-800">{label}</span>
      <span className="min-w-8 shrink-0 rounded-full bg-slate-100 px-2.5 py-0.5 text-center text-sm font-semibold tabular-nums text-slate-800">{count}</span>
      {to && action && (
        <span className="hidden shrink-0 items-center gap-1 text-sm font-medium text-brand sm:inline-flex">
          {action}
          <ChevronRight aria-hidden className="h-4 w-4 rtl:rotate-180" />
        </span>
      )}
      {to && <ChevronRight aria-hidden className="h-4 w-4 shrink-0 text-slate-300 sm:hidden rtl:rotate-180" />}
    </>
  )
  return to ? (
    <Link to={to} className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-slate-50">
      {body}
    </Link>
  ) : (
    <div className="flex items-center gap-3 px-4 py-3">{body}</div>
  )
}

/** A numbered step in a guided task. Later steps stay visible but quiet until reached. */
export function Step({ n, title, hint, children, active = true, done }: { n: number; title: ReactNode; hint?: ReactNode; children?: ReactNode; active?: boolean; done?: boolean }) {
  return (
    <section className={cx('card', !active && 'bg-slate-50/60 shadow-none')} aria-current={active && !done ? 'step' : undefined}>
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className={cx(
            'grid h-7 w-7 shrink-0 place-items-center rounded-full text-sm font-semibold',
            done ? 'bg-brand text-brand-ink' : active ? 'bg-brand-soft text-brand ring-1 ring-brand/30' : 'bg-slate-100 text-slate-400',
          )}
        >
          {done ? <CircleCheck className="h-4 w-4" /> : n}
        </span>
        <div className="min-w-0 flex-1">
          <h2 className={cx('leading-7', !active && 'text-slate-400')}>{title}</h2>
          {hint && active && <p className="text-sm text-slate-500">{hint}</p>}
          {children && active && <div className="mt-4">{children}</div>}
        </div>
      </div>
    </section>
  )
}

/** A large, tappable choice (one of several), for picking a squad or a type of message. */
export function ChoiceCard({
  selected,
  onSelect,
  title,
  meta,
  icon: Icon,
}: {
  selected: boolean
  onSelect: () => void
  title: ReactNode
  meta?: ReactNode
  icon?: LucideIcon
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className={cx(
        'flex min-h-14 w-full items-start gap-3 rounded-xl border bg-white p-3.5 text-start transition',
        selected ? 'border-brand ring-2 ring-brand/25' : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50',
      )}
    >
      {Icon && <Icon aria-hidden className={cx('mt-0.5 h-5 w-5 shrink-0', selected ? 'text-brand' : 'text-slate-400')} />}
      <span className="min-w-0 flex-1">
        <span className="block font-medium text-slate-900">{title}</span>
        {meta && <span className="mt-0.5 block text-sm text-slate-500">{meta}</span>}
      </span>
      <span
        aria-hidden
        className={cx('mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border', selected ? 'border-brand bg-brand text-brand-ink' : 'border-slate-300')}
      >
        {selected && <span className="h-2 w-2 rounded-full bg-white" />}
      </span>
    </button>
  )
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: ReactNode }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div role="tablist" className="mb-5 flex flex-wrap gap-1 border-b border-slate-200">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={value === tab.id}
          onClick={() => onChange(tab.id)}
          className={cx(
            '-mb-px border-b-2 px-3 py-2.5 text-sm font-medium transition-colors',
            value === tab.id ? 'border-brand text-slate-900' : 'border-transparent text-slate-500 hover:text-slate-800',
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}

export function TableWrap({ children }: { children: ReactNode }) {
  return <div className="-mx-5 overflow-x-auto px-5">{children}</div>
}

export function useToast(): [ReactNode, (msg: string, tone?: 'success' | 'error' | 'info') => void] {
  const [toast, setToast] = useState<{ msg: string; tone: 'success' | 'error' | 'info' } | null>(null)
  const node = toast ? (
    <div aria-live="polite" className="fixed bottom-20 inset-s-1/2 z-50 -translate-x-1/2 lg:bottom-6 rtl:translate-x-1/2">
      <div
        className={cx(
          'flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm text-white shadow-raised',
          toast.tone === 'error' ? 'bg-red-700' : toast.tone === 'info' ? 'bg-slate-800' : 'bg-emerald-700',
        )}
      >
        {toast.tone === 'error' ? <CircleAlert aria-hidden className="h-4 w-4" /> : toast.tone === 'success' ? <CircleCheck aria-hidden className="h-4 w-4" /> : <Info aria-hidden className="h-4 w-4" />}
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
        className="relative h-2 min-w-[96px] flex-1 overflow-hidden rounded-full bg-[#cde2fb]"
      >
        <div className="absolute inset-y-0 inset-s-0 rounded-full bg-[#256abf]" style={{ width: `${pct}%` }} />
        {threshold !== undefined && (
          <div aria-hidden className="absolute inset-y-[-2px] w-0.5 bg-slate-900" style={{ insetInlineStart: `calc(${(threshold / max) * 100}% - 1px)` }} />
        )}
      </div>
      <span className="w-12 text-end text-sm font-semibold tabular-nums text-slate-800">{tip}</span>
    </div>
  )
}
