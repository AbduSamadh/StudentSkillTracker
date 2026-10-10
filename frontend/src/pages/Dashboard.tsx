import { useQuery } from '@tanstack/react-query'
import { ArrowDown, ArrowRight, ArrowUp, ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { Alert, Badge, ClaimBadge, FigureTile, Loading, Section } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { withheldReason } from '@/lib/figures'
import { fmtDate, isolate } from '@/lib/format'
import type { Figure } from '@/lib/types'

interface Dashboard {
  season: { id: string; name: string } | null
  participation: Figure
  skills_coverage: Figure
  spend: Figure & { lines_awaiting_approval: number }
  upcoming_events: { count: number; next: { edition_id: string; name: string; starts: string; tier: string }[] }
  flagged_students: Figure & { by_kind: Record<string, Figure> }
  trend:
    | { label: string; available: true; previous: Figure; current: Figure }
    | { label: string; available: false; reason: string }
}

function TileLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link to={to} className="inline-flex items-center gap-1 text-sm font-medium text-brand hover:underline">
      {children}
      <ChevronRight aria-hidden className="h-4 w-4 rtl:rotate-180" />
    </Link>
  )
}

/** Leaders' and admins' six numbers, not sixty (spec §6.4), each with a line saying what it means. */
export function SchoolOverview() {
  const { t } = useTranslation()
  const q = useQuery({ queryKey: ['dashboard'], queryFn: () => api<Dashboard>('/dashboard/leader') })
  const title = t('dashboard.title')
  if (q.isLoading)
    return (
      <Section title={title}>
        <Loading />
      </Section>
    )
  if (q.error)
    return (
      <Section title={title}>
        <Alert tone="error">{errorMessage(q.error)}</Alert>
      </Section>
    )
  const d = q.data!
  const trend = d.trend
  return (
    <Section title={title} description={t('dashboard.subtitle')} actions={d.season && <Badge>{`${t('dashboard.season')}: ${isolate(d.season.name)}`}</Badge>}>
      {!d.season && <Alert tone="warn">{t('dashboard.noSeason')}</Alert>}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <FigureTile title={t('dashboard.participation')} figure={d.participation} help={t('dashboard.participationHelp')} />
        <FigureTile title={t('dashboard.coverage')} figure={d.skills_coverage} help={t('dashboard.coverageHelp')} />
        <FigureTile
          title={t('dashboard.spend')}
          figure={d.spend}
          help={t('dashboard.spendHelp')}
          footer={d.spend.lines_awaiting_approval > 0 && <TileLink to="/budget">{t('dashboard.awaiting', { count: d.spend.lines_awaiting_approval })}</TileLink>}
        />
        <section className="card flex flex-col gap-3">
          <div className="flex items-start justify-between gap-2">
            <h3 className="font-medium text-slate-600">{t('dashboard.upcoming')}</h3>
            <ClaimBadge type="measured" />
          </div>
          <div className="text-3xl font-semibold tracking-tight text-slate-900">{d.upcoming_events.count}</div>
          {d.upcoming_events.next.length === 0 ? (
            <p className="text-sm text-slate-500">{t('dashboard.noEvents')}</p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {d.upcoming_events.next.map((e) => (
                <li key={e.edition_id} className="flex justify-between gap-2">
                  <Link to={`/editions/${e.edition_id}`} className="truncate text-slate-800 hover:underline">
                    {e.name}
                  </Link>
                  <span className="shrink-0 text-slate-500">{fmtDate(e.starts, { day: 'numeric', month: 'short' })}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
        <FigureTile
          title={t('dashboard.flagged')}
          figure={d.flagged_students}
          help={t('dashboard.flaggedHelp')}
          footer={<TileLink to="/flags">{t('dashboard.viewFlags')}</TileLink>}
        />
        <section className="card flex flex-col gap-3">
          <div className="flex items-start justify-between gap-2">
            <h3 className="font-medium text-slate-600">{t('dashboard.trend')}</h3>
            <ClaimBadge type="measured" />
          </div>
          {trend.available ? (
            <TrendTile previous={trend.previous} current={trend.current} label={t('dashboard.trendLabel', { from: isolate(trend.previous.label), to: isolate(trend.current.label) })} />
          ) : (
            <p className="text-sm text-slate-500">{t('dashboard.trendUnavailable')}</p>
          )}
        </section>
      </div>
    </Section>
  )
}

/** Two seasons is a delta, not a chart: value + signed change, each with its base. */
function TrendTile({ previous, current, label }: { previous: Figure; current: Figure; label: string }) {
  const { t } = useTranslation()
  if (previous.withheld || current.withheld) {
    return <p className="text-sm text-slate-500">{t('common.withheldBecause', { reason: withheldReason(t, current.withheld ? current : previous) })}</p>
  }
  const delta = Number(current.value) - Number(previous.value)
  const Arrow = delta > 0 ? ArrowUp : delta < 0 ? ArrowDown : ArrowRight
  return (
    <div>
      <div className="text-3xl font-semibold tracking-tight text-slate-900">{current.value}</div>
      <p className="mt-1 flex items-center gap-1 text-sm text-slate-700">
        <Arrow aria-hidden className="h-4 w-4 text-slate-400" /> {delta > 0 ? `+${delta}` : delta} ({isolate(previous.label)}: {previous.value})
      </p>
      <p className="text-sm text-slate-500">{label}</p>
    </div>
  )
}
