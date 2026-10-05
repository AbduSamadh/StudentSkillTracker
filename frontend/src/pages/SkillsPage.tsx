import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Badge, Card, FigureTile, Loading, Meter, PageHeader, SelectInput, TableWrap, TextInput } from '@/components/ui'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { withheldReason } from '@/lib/figures'
import { useSkills } from '@/lib/queries'
import type { Figure } from '@/lib/types'

interface Coverage {
  taxonomy_coverage: Figure
  active_students: number
  domains: { domain: string; skills_total: number; skills_evidenced: number; coverage: Figure; skills: { code: string; name: string; holders: Figure }[] }[]
  frameworks: string[]
  framework?: { name: string; codes: { code: string; skills: string[]; skills_evidenced: number; students: Figure }[] }
}

// Framework identifiers as stored in the taxonomy's framework_refs, and how they are written.
const FRAMEWORK_NAMES: Record<string, string> = { IBATL: 'IB ATL', MOEAI: 'MoE AI' }
const frameworkName = (f: string) => FRAMEWORK_NAMES[f] ?? f

export default function SkillsPage() {
  const { t, i18n } = useTranslation()
  const { can } = useAuth()
  const skills = useSkills()
  const [domain, setDomain] = useState('')
  const [q, setQ] = useState('')
  const [framework, setFramework] = useState('')
  const coverage = useQuery({ queryKey: ['coverage'], queryFn: () => api<Coverage>('/skills/coverage'), enabled: can('view_school_analytics') })
  const byFramework = useQuery({
    queryKey: ['coverage', framework],
    queryFn: () => api<Coverage>(`/skills/coverage?framework=${encodeURIComponent(framework)}`),
    enabled: can('view_school_analytics') && !!framework,
  })
  const domains = useMemo(() => [...new Set((skills.data ?? []).map((s) => s.domain))], [skills.data])
  const holders = useMemo(() => {
    const m = new Map<string, Figure>()
    coverage.data?.domains.forEach((d) => d.skills.forEach((s) => m.set(s.code, s.holders)))
    return m
  }, [coverage.data])
  if (skills.isLoading) return <Loading />
  const list = (skills.data ?? []).filter(
    (s) => (!domain || s.domain === domain) && (!q || `${s.code} ${s.name} ${s.parent_label_en} ${s.parent_label_ar}`.toLowerCase().includes(q.toLowerCase())),
  )
  return (
    <>
      <PageHeader title={t('skills.title')} subtitle={`${skills.data?.length ?? 0}`} />
      {coverage.data && (
        <div className="mb-4 grid gap-4 lg:grid-cols-3">
          <FigureTile title={t('skills.coverage')} figure={coverage.data.taxonomy_coverage} />
          <Card className="lg:col-span-2" title={t('skills.evidenced')}>
            <ul className="space-y-2">
              {coverage.data.domains.map((d) => (
                <li key={d.domain} className="grid grid-cols-[120px_1fr] items-center gap-2 text-sm">
                  <span>{d.domain}</span>
                  <Meter value={d.skills_evidenced} max={d.skills_total} label={d.domain} valueText={`${d.skills_evidenced}/${d.skills_total}`} />
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}
      {coverage.data && (
        <Card className="mb-4" title={t('skills.byFramework')}>
          <div className="max-w-xs">
            <SelectInput label={t('skills.reportAgainst')} value={framework} onChange={(e) => setFramework(e.target.value)}>
              <option value="">{t('skills.chooseFramework')}</option>
              {coverage.data.frameworks.map((f) => (
                <option key={f} value={f}>
                  {frameworkName(f)}
                </option>
              ))}
            </SelectInput>
          </div>
          {framework && byFramework.isLoading && <Loading />}
          {framework && byFramework.data?.framework && (
            <>
              <p className="mt-3 text-sm text-slate-600">{t('skills.frameworkNote', { name: frameworkName(framework) })}</p>
              <TableWrap>
                <table className="mt-2">
                  <thead>
                    <tr>
                      <th>{t('skills.frameworkCode')}</th>
                      <th>{t('skills.mappedSkills')}</th>
                      <th>{t('skills.evidenced')}</th>
                      <th>{t('skills.holdersAny')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {byFramework.data.framework.codes.map((r) => (
                      <tr key={r.code}>
                        <td className="whitespace-nowrap font-mono text-xs" dir="ltr">
                          {r.code}
                        </td>
                        <td className="font-mono text-xs" dir="ltr">
                          {r.skills.join(', ')}
                        </td>
                        <td>
                          {r.skills_evidenced}/{r.skills.length}
                        </td>
                        <td title={r.students.withheld ? withheldReason(t, r.students) : ''}>{r.students.withheld ? t('common.withheld') : r.students.value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </>
          )}
        </Card>
      )}
      <Card>
        <div className="mb-4 grid gap-3 sm:grid-cols-2">
          <TextInput label={t('common.search')} value={q} onChange={(e) => setQ(e.target.value)} />
          <SelectInput label={t('skills.domain')} value={domain} onChange={(e) => setDomain(e.target.value)}>
            <option value="">{t('common.all')}</option>
            {domains.map((d) => (
              <option key={d}>{d}</option>
            ))}
          </SelectInput>
        </div>
        <TableWrap>
          <table>
            <thead>
              <tr>
                <th>{t('skills.code')}</th>
                <th>{t('capture.skill')}</th>
                <th>{t('skills.parentLabel')}</th>
                <th>{t('skills.typicalYear')}</th>
                <th>{t('skills.frameworks')}</th>
                {coverage.data && <th>{t('skills.holders')}</th>}
              </tr>
            </thead>
            <tbody>
              {list.map((s) => {
                const h = holders.get(s.code)
                return (
                  <tr key={s.id}>
                    <td className="whitespace-nowrap font-mono text-xs">{s.code}</td>
                    <td>
                      {s.name}
                      <div className="text-xs text-slate-500">
                        {s.domain} · {s.strand}
                      </div>
                    </td>
                    <td className="text-sm">
                      <div lang="en">{s.parent_label_en}</div>
                      <div lang="ar" dir="rtl" className="text-slate-600">
                        {i18n.language === 'ar' ? null : s.parent_label_ar}
                      </div>
                    </td>
                    <td>{s.typical_year_group ?? '—'}</td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {s.framework_refs.map((r) => (
                          <Badge key={r}>{r}</Badge>
                        ))}
                      </div>
                    </td>
                    {coverage.data && <td title={h?.withheld ? withheldReason(t, h) : ''}>{h ? (h.withheld ? t('common.withheld') : h.value) : '—'}</td>}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </TableWrap>
      </Card>
    </>
  )
}
