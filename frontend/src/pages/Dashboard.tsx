import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { Alert, Badge, ClaimBadge, FigureTile, Loading, PageHeader } from '@/components/ui'
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

/** Leader landing page: six numbers, not sixty (spec §6.4). */
export default function DashboardPage() {
  const { t } = useTranslation()
  const q = useQuery({ queryKey: ['dashboard'], queryFn: () => api<Dashboard>('/dashboard/leader') })
  if (q.isLoading) return <Loading />
  if (q.error) return <Alert tone="error">{errorMessage(q.error)}</Alert>
  const d = q.data!
  const trend = d.trend
  return (
    <>
      <PageHeader title={t('dashboard.title')} subtitle={`${t('dashboard.subtitle')}${d.season ? ` · ${t('dashboard.season')}: ${isolate(d.season.name)}` : ''}`} />
      {!d.season && <Alert tone="warn">{t('dashboard.noSeason')}</Alert>}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <FigureTile title={t('dashboard.participation')} figure={d.participation} />
        <FigureTile title={t('dashboard.coverage')} figure={d.skills_coverage} />
        <FigureTile
          title={t('dashboard.spend')}
          figure={d.spend}
          footer={
            d.spend.lines_awaiting_approval > 0 && (
              <Link to="/budget" className="text-sm font-medium text-brand underline">
                {t('dashboard.awaiting', { count: d.spend.lines_awaiting_approval })}
              </Link>
            )
          }
        />
        <section className="card flex flex-col gap-2">
          <div className="flex items-start justify-between">
            <h3 className="text-sm font-medium text-slate-600">{t('dashboard.upcoming')}</h3>
            <ClaimBadge type="measured" />
          </div>
          <div className="text-3xl font-bold">{d.upcoming_events.count}</div>
          {d.upcoming_events.next.length === 0 ? (
            <p className="text-sm text-slate-500">{t('dashboard.noEvents')}</p>
          ) : (
            <ul className="space-y-1 text-sm">
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
          footer={
            <div className="flex flex-wrap items-center gap-2">
              {Object.entries(d.flagged_students.by_kind).map(([kind, f]) => (
                <Badge key={kind} tone="slate">
                  {t(`flagKinds.${kind}` as 'flagKinds.plateau')}: {f.withheld ? t('common.withheld') : f.value}
                </Badge>
              ))}
              <Link to="/flags" className="text-sm font-medium text-brand underline">
                {t('dashboard.viewFlags')}
              </Link>
            </div>
          }
        />
        <section className="card flex flex-col gap-2">
          <div className="flex items-start justify-between">
            <h3 className="text-sm font-medium text-slate-600">{t('dashboard.trend')}</h3>
            <ClaimBadge type="measured" />
          </div>
          {trend.available ? (
            <TrendTile previous={trend.previous} current={trend.current} label={t('dashboard.trendLabel', { from: isolate(trend.previous.label), to: isolate(trend.current.label) })} />
          ) : (
            <p className="text-sm text-slate-600">{t('dashboard.trendUnavailable')}</p>
          )}
        </section>
      </div>
    </>
  )
}

/** Two seasons is a delta, not a chart: value + signed change, each with its base. */
function TrendTile({ previous, current, label }: { previous: Figure; current: Figure; label: string }) {
  const { t } = useTranslation()
  if (previous.withheld || current.withheld) {
    return <p className="text-sm text-amber-900">{t('common.withheldBecause', { reason: withheldReason(t, current.withheld ? current : previous) })}</p>
  }
  const delta = Number(current.value) - Number(previous.value)
  return (
    <div>
      <div className="text-3xl font-bold">{current.value}</div>
      <p className="text-sm text-slate-700">
        <span aria-hidden>{delta > 0 ? '▲' : delta < 0 ? '▼' : '■'}</span> {delta > 0 ? `+${delta}` : delta} ({isolate(previous.label)}: {previous.value})
      </p>
      <p className="text-xs text-slate-500">{label}</p>
    </div>
  )
}
