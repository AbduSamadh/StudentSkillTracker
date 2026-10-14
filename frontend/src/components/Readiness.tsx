import { useTranslation } from 'react-i18next'

import { pick } from '@/lib/format'
import { fmtDate } from '@/lib/format'
import type { Readiness, RecommendationItem } from '@/lib/types'

import { Badge, ClaimBadge, LevelPill, Meter, TableWrap, cx } from './ui'

/** A readiness score is traceable to the exact awards and requirements that produced it
 *  (acceptance criterion 13.1.3). */
export function ReadinessTrace({ r, verifierName }: { r: Readiness; verifierName?: (id: string | null) => string }) {
  const { t } = useTranslation()
  if (r.percent === null) return <p className="text-sm text-slate-600">{t('profile.notCatalogued')}</p>
  const ready = r.percent >= r.threshold_percent
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="w-64">
          <Meter value={r.percent} threshold={r.threshold_percent} label={`${t('profile.readinessFor')} ${r.edition_name}`} />
        </div>
        <Badge tone={ready ? 'green' : 'amber'}>
          <span aria-hidden>{ready ? '✓ ' : '… '}</span>
          {ready ? t('competitions.ready') : t('competitions.notReady')}
        </Badge>
        <span className="text-sm text-slate-600">{t('profile.threshold', { n: r.threshold_percent })}</span>
        <ClaimBadge type="inferred" />
      </div>
      <details className="rounded-xl border border-slate-200 p-4" open>
        <summary className="text-sm font-semibold text-slate-900">{t('profile.whyScore')}</summary>
        <p className="mt-1 text-sm text-slate-500">{t('profile.whyScoreHint')}</p>
        <p className="mt-2 text-xs text-slate-500" dir="ltr">
          {t('profile.formula')}: {r.formula} = {r.lines.reduce((a, l) => a + l.contribution, 0).toFixed(2)} / {r.total_weight.toFixed(2)}
        </p>
        <TableWrap>
          <table className="mt-2">
            <thead>
              <tr>
                <th>{t('profile.requirement')}</th>
                <th>{t('profile.required')}</th>
                <th>{t('profile.earned')}</th>
                <th>{t('profile.weight')}</th>
                <th>{t('profile.contribution')}</th>
                <th>{t('profile.evidence')}</th>
              </tr>
            </thead>
            <tbody>
              {r.lines.map((l) => (
                <tr key={l.skill_id} className={cx(l.is_gap && 'bg-amber-50')}>
                  <td>
                    <div>{l.name}</div>
                    <div className="font-mono text-[11px] text-slate-400">{l.code}</div>
                    <div className="mt-1 flex gap-1">
                      {l.is_core && <Badge tone="brand">{t('profile.core')}</Badge>}
                      <Badge>{l.inherited ? t('profile.inherited') : t('profile.override')}</Badge>
                    </div>
                  </td>
                  <td>
                    <LevelPill level={l.required_level} />
                  </td>
                  <td>
                    <LevelPill level={l.earned_level} />
                    {l.is_gap && <div className="mt-1 text-xs text-amber-900">{t('profile.levelsShort', { n: l.levels_short })}</div>}
                  </td>
                  <td className="tabular-nums">{l.weight}</td>
                  <td className="tabular-nums">{l.contribution.toFixed(2)}</td>
                  <td className="text-xs">
                    {l.evidence ? (
                      <>
                        <div>
                          {t(`profile.sources.${l.evidence.source}` as 'profile.sources.teacher')}
                          {l.evidence.confidence && ` · ${t(`confidence.${l.evidence.confidence}` as 'confidence.high', { defaultValue: l.evidence.confidence })}`}
                        </div>
                        <div className="text-slate-600">{l.evidence.evidence_note}</div>
                        <div className="text-slate-500">
                          {fmtDate(l.evidence.awarded_on)}
                          {verifierName && ` · ${verifierName(l.evidence.verified_by_id)}`}
                        </div>
                        <div className="font-mono text-[10px] text-slate-400" dir="ltr">#{l.evidence.award_id.slice(0, 8)}</div>
                      </>
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </details>
      <div>
        <h3 className="mb-2">{t('profile.gaps')}</h3>
        {r.gaps.length === 0 ? (
          <p className="text-sm text-emerald-800">{t('profile.noGaps')}</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {r.gaps.map((g) => (
              <li key={g.skill_id} className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-slate-800">{pick(g.name, null)}</span>
                <span className="text-slate-500">
                  {t('levels.' + g.earned_level as 'levels.0')} → {t('levels.' + g.required_level as 'levels.0')}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export function RecommendationList({ items, kind }: { items: RecommendationItem[]; kind: 'recommended' | 'almost' | 'not' }) {
  const { t } = useTranslation()
  if (items.length === 0) return null
  const title = kind === 'recommended' ? t('profile.recommended') : kind === 'almost' ? t('profile.almostReady') : t('profile.notRecommended')
  return (
    <section className="card claim-inferred space-y-3 border-dashed">
      <div className="flex items-center justify-between">
        <h3>{title}</h3>
        <ClaimBadge type="inferred" />
      </div>
      <ul className="space-y-3">
        {items.map((r) => (
          <li key={r.edition_id} className="rounded-xl border border-slate-200 p-3.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="font-semibold">{r.edition_name}</div>
                <div className="text-xs text-slate-500">
                  {t(`tiers.${r.tier}` as 'tiers.school')} · {fmtDate(r.event_starts)}
                </div>
              </div>
              {r.readiness_percent !== null && (
                <div className="w-40">
                  <Meter value={r.readiness_percent} label={r.edition_name} />
                </div>
              )}
            </div>
            <p className="mt-2 text-sm">{pick(r.reason_en, r.reason_ar)}</p>
            <ul className="mt-2 flex flex-wrap gap-1">
              {r.checks.map((c) => (
                <li key={c.key}>
                  <Badge tone={c.passed ? 'green' : 'red'}>
                    <span aria-hidden>{c.passed ? '✓' : '✕'}</span>&nbsp;{pick(c.detail_en, c.detail_ar)}
                  </Badge>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </section>
  )
}
