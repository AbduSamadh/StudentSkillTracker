import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarDays, ExternalLink, Plus, Trophy } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'

import { Alert, Badge, Button, Card, ClaimBadge, Empty, EmptyState, LevelPill, Loading, Meter, PageHeader, Section, SelectInput, TableWrap, TextInput, useToast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { fmtDate, fmtMoney, pick } from '@/lib/format'
import { useCompetitions, useEditions, useSkills } from '@/lib/queries'
import type { Competition, Edition } from '@/lib/types'

const TIERS = ['school', 'inter_school', 'emirate', 'national', 'international'] as const
const DISCIPLINES = ['Robotics', 'Coding', 'AI', 'Engineering', 'Science', 'Design', 'Collaboration']

interface Requirement {
  skill_id: string
  code: string
  name: string
  domain: string
  required_level: number
  weight: number
  is_core: boolean
  inherited?: boolean
}

export default function CompetitionsPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const comps = useCompetitions()
  const editions = useEditions(true)
  const qc = useQueryClient()
  const [form, setForm] = useState({ name: '', discipline: 'Robotics', tier: 'emirate', organiser: '' })
  const create = useMutation({
    mutationFn: () => api('/competitions', { method: 'POST', body: form }),
    onSuccess: () => {
      setForm({ ...form, name: '', organiser: '' })
      void qc.invalidateQueries({ queryKey: ['competitions'] })
    },
  })
  if (comps.isLoading) return <Loading />
  if (comps.error) return <Alert tone="error">{errorMessage(comps.error)}</Alert>
  const upcoming = editions.data ?? []
  return (
    <>
      <PageHeader icon={Trophy} title={t('competitions.title')} subtitle={t('competitions.intro')} />
      <div className="space-y-8">
        <Section title={t('competitions.upcoming')} description={t('competitions.upcomingHint')}>
          {upcoming.length === 0 ? (
            <EmptyState icon={CalendarDays} title={t('competitions.noUpcoming')} />
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {upcoming.map((e) => (
                <li key={e.id} className="min-w-0">
                  <Link to={`/editions/${e.id}`} className="group card flex items-start gap-3.5 transition hover:border-brand/40 hover:shadow-raised">
                    <span aria-hidden className="flex w-12 shrink-0 flex-col items-center rounded-xl bg-brand-soft py-1.5 text-brand">
                      <span className="text-lg font-semibold leading-tight">{fmtDate(e.event_starts, { day: 'numeric' })}</span>
                      <span className="text-[11px] font-medium uppercase">{fmtDate(e.event_starts, { month: 'short' })}</span>
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block font-medium text-slate-900">{e.name}</span>
                      <span className="block text-sm text-slate-500">
                        {t(`tiers.${e.tier}` as 'tiers.school')}
                        {e.venue && ` · ${e.venue}`}
                      </span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Section>
        <Section title={t('competitions.all')}>
          <Card className="p-0">
            <ul className="divide-y divide-slate-100">
              {comps.data!.map((c) => (
                <li key={c.id}>
                  <Link to={`/competitions/${c.id}`} className="flex flex-wrap items-center gap-3 px-5 py-3.5 hover:bg-slate-50">
                    <span className="min-w-0 flex-1">
                      <span className="block font-medium text-slate-900">{pick(c.name, c.name_ar)}</span>
                      <span className="block text-sm text-slate-500">{[c.organiser, t(`domains.${c.discipline}` as 'domains.Coding', { defaultValue: c.discipline }), t(`tiers.${c.tier}` as 'tiers.school')].filter(Boolean).join(' · ')}</span>
                    </span>
                    {c.status === 'proposed' && <Badge tone="amber">{t('competitions.proposed')}</Badge>}
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        </Section>
        {can('propose_competition') && (
          <Card
            icon={Plus}
            title={can('manage_competitions') ? t('competitions.new') : t('competitions.propose')}
            description={can('manage_competitions') ? t('competitions.newHint') : t('competitions.proposeHint')}
          >
            <form
              className="grid gap-4 sm:grid-cols-2"
              onSubmit={(e) => {
                e.preventDefault()
                create.mutate()
              }}
            >
              <TextInput label={t('common.name')} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
              <TextInput label={`${t('competitions.organiser')} (${t('common.optional')})`} value={form.organiser} onChange={(e) => setForm({ ...form, organiser: e.target.value })} />
              <SelectInput label={t('competitions.discipline')} value={form.discipline} onChange={(e) => setForm({ ...form, discipline: e.target.value })}>
                {DISCIPLINES.map((d) => (
                  <option key={d} value={d}>
                    {t(`domains.${d}` as 'domains.Coding', { defaultValue: d })}
                  </option>
                ))}
              </SelectInput>
              <SelectInput label={t('competitions.tier')} value={form.tier} onChange={(e) => setForm({ ...form, tier: e.target.value })}>
                {TIERS.map((x) => (
                  <option key={x} value={x}>
                    {t(`tiers.${x}`)}
                  </option>
                ))}
              </SelectInput>
              {create.error && (
                <div className="sm:col-span-2">
                  <Alert tone="error">{errorMessage(create.error)}</Alert>
                </div>
              )}
              <div>
                <Button type="submit" icon={Plus} busy={create.isPending}>
                  {can('manage_competitions') ? t('competitions.new') : t('competitions.propose')}
                </Button>
              </div>
            </form>
          </Card>
        )}
      </div>
    </>
  )
}

function RequirementEditor({ onAdd, busy }: { onAdd: (r: { skill_code: string; required_level: number; weight: number; is_core: boolean; removed?: boolean }) => void; busy: boolean }) {
  const { t } = useTranslation()
  const skills = useSkills()
  const [code, setCode] = useState('')
  const [level, setLevel] = useState(2)
  const [weight, setWeight] = useState(1)
  const [core, setCore] = useState(false)
  return (
    <form
      className="mt-4 grid items-end gap-3 border-t border-slate-100 pt-4 sm:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault()
        onAdd({ skill_code: code.trim().toUpperCase(), required_level: level, weight, is_core: core })
        setCode('')
      }}
    >
      <SelectInput label={t('capture.skill')} value={code} onChange={(e) => setCode(e.target.value)} required>
        <option value="">{t('capture.chooseSkill')}</option>
        {(skills.data ?? []).map((s) => (
          <option key={s.id} value={s.code}>
            {s.name}
          </option>
        ))}
      </SelectInput>
      <SelectInput label={t('profile.required')} value={level} onChange={(e) => setLevel(Number(e.target.value))}>
        {[1, 2, 3, 4].map((l) => (
          <option key={l} value={l}>
            {t(`levels.${l}` as 'levels.1')}
          </option>
        ))}
      </SelectInput>
      <TextInput label={t('profile.weight')} hint={t('competitions.weightHint')} type="number" min={0.25} step={0.25} value={weight} onChange={(e) => setWeight(Number(e.target.value))} />
      <label className="flex items-center gap-2 pb-2 text-sm">
        <input type="checkbox" className="h-4 w-4 accent-brand" checked={core} onChange={(e) => setCore(e.target.checked)} /> {t('competitions.coreLabel')}
      </label>
      <Button type="submit" variant="secondary" icon={Plus} busy={busy}>
        {t('competitions.addRequirement')}
      </Button>
    </form>
  )
}

function RequirementsTable({ rows, onRemove }: { rows: Requirement[]; onRemove?: (r: Requirement) => void }) {
  const { t } = useTranslation()
  if (!rows.length) return <Empty>{t('competitions.noRequirements')}</Empty>
  return (
    <TableWrap>
      <table>
        <thead>
          <tr>
            <th>{t('capture.skill')}</th>
            <th>{t('profile.required')}</th>
            <th>{t('profile.weight')}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.skill_id}>
              <td>
                {r.name}
                <div className="font-mono text-[11px] text-slate-400">{r.code}</div>
                <div className="mt-1 flex gap-1">
                  {r.is_core && <Badge tone="brand">{t('profile.core')}</Badge>}
                  {r.inherited !== undefined && <Badge>{r.inherited ? t('profile.inherited') : t('profile.override')}</Badge>}
                </div>
              </td>
              <td>
                <LevelPill level={r.required_level} />
              </td>
              <td>{r.weight}</td>
              <td>
                {onRemove && (
                  <Button size="sm" variant="ghost" onClick={() => onRemove(r)}>
                    {t('common.remove')}
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  )
}

export function CompetitionDetailPage() {
  const { id = '' } = useParams()
  const { t } = useTranslation()
  const { can } = useAuth()
  const qc = useQueryClient()
  const comp = useQuery({ queryKey: ['competition', id], queryFn: () => api<Competition>(`/competitions/${id}`) })
  const reqs = useQuery({ queryKey: ['comp-reqs', id], queryFn: () => api<Requirement[]>(`/competitions/${id}/requirements`) })
  const eds = useQuery({ queryKey: ['comp-editions', id], queryFn: () => api<Edition[]>(`/competitions/${id}/editions`) })
  const [toast, show] = useToast()
  const addReq = useMutation({
    mutationFn: (r: object) => api(`/competitions/${id}/requirements`, { method: 'POST', body: [r] }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['comp-reqs', id] }),
    onError: (e) => show(errorMessage(e), 'error'),
  })
  const approve = useMutation({
    mutationFn: () => api(`/competitions/${id}/approve`, { method: 'POST' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['competition', id] }),
  })
  const [ed, setEd] = useState({ name: '', stage: '', event_starts: '', event_ends: '', registration_opens: '', registration_closes: '', venue: '', entry_fee: '', expected_field_size: '', eligible_year_min: '', eligible_year_max: '' })
  const createEd = useMutation({
    mutationFn: () =>
      api(`/competitions/${id}/editions`, {
        method: 'POST',
        body: Object.fromEntries(Object.entries(ed).map(([k, v]) => [k, v === '' ? null : v])),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['comp-editions', id] })
      show(t('common.saved'))
    },
    onError: (e) => show(errorMessage(e), 'error'),
  })
  if (comp.isLoading) return <Loading />
  if (comp.error) return <Alert tone="error">{errorMessage(comp.error)}</Alert>
  const c = comp.data!
  const admin = can('manage_competitions')
  return (
    <>
      {toast}
      <PageHeader
        back={{ to: '/competitions', label: t('competitions.title') }}
        icon={Trophy}
        title={pick(c.name, c.name_ar)}
        subtitle={`${c.organiser ?? ''} · ${c.discipline} · ${t(`tiers.${c.tier}` as 'tiers.school')}`}
        actions={
          c.status === 'proposed' && admin ? (
            <Button onClick={() => approve.mutate()} busy={approve.isPending}>
              {t('competitions.approve')}
            </Button>
          ) : undefined
        }
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t('competitions.requirements')} description={t('competitions.requirementsHint')}>
          <RequirementsTable rows={reqs.data ?? []} />
          {admin && <RequirementEditor busy={addReq.isPending} onAdd={(r) => addReq.mutate(r)} />}
        </Card>
        <Card title={t('competitions.editions')}>
          <ul className="space-y-2">
            {(eds.data ?? []).map((e) => (
              <li key={e.id}>
                <Link to={`/editions/${e.id}`} className="font-medium text-brand hover:underline">
                  {e.name}
                </Link>
                <div className="text-xs text-slate-500">
                  {fmtDate(e.event_starts)} · {e.venue}
                </div>
              </li>
            ))}
          </ul>
          {admin && (
            <details className="mt-4 rounded-xl border border-slate-200 p-3">
              <summary className="text-sm font-medium text-slate-700">{t('competitions.newEdition')}</summary>
              <form
                className="mt-3 grid gap-3 sm:grid-cols-2"
                onSubmit={(e) => {
                  e.preventDefault()
                  createEd.mutate()
                }}
              >
                <TextInput label={t('common.name')} value={ed.name} onChange={(e) => setEd({ ...ed, name: e.target.value })} required />
                <TextInput label={t('competitions.stage')} value={ed.stage} onChange={(e) => setEd({ ...ed, stage: e.target.value })} />
                <TextInput label={`${t('competitions.eventDates')} — ${t('common.from')}`} type="date" value={ed.event_starts} onChange={(e) => setEd({ ...ed, event_starts: e.target.value })} required />
                <TextInput label={`${t('competitions.eventDates')} — ${t('common.to')}`} type="date" value={ed.event_ends} onChange={(e) => setEd({ ...ed, event_ends: e.target.value })} required />
                <TextInput label={t('competitions.opens')} type="date" value={ed.registration_opens} onChange={(e) => setEd({ ...ed, registration_opens: e.target.value })} />
                <TextInput label={t('competitions.closes')} type="date" value={ed.registration_closes} onChange={(e) => setEd({ ...ed, registration_closes: e.target.value })} />
                <TextInput label={t('competitions.venue')} value={ed.venue} onChange={(e) => setEd({ ...ed, venue: e.target.value })} />
                <TextInput label={t('competitions.fee')} type="number" min={0} value={ed.entry_fee} onChange={(e) => setEd({ ...ed, entry_fee: e.target.value })} />
                <TextInput label={t('competitions.fieldSize')} type="number" min={1} value={ed.expected_field_size} onChange={(e) => setEd({ ...ed, expected_field_size: e.target.value })} />
                <div className="grid grid-cols-2 gap-2">
                  <TextInput label={`${t('competitions.eligibleYears')} ${t('common.from')}`} type="number" min={1} max={13} value={ed.eligible_year_min} onChange={(e) => setEd({ ...ed, eligible_year_min: e.target.value })} />
                  <TextInput label={t('common.to')} type="number" min={1} max={13} value={ed.eligible_year_max} onChange={(e) => setEd({ ...ed, eligible_year_max: e.target.value })} />
                </div>
                <Button type="submit" busy={createEd.isPending} className="sm:col-span-2">
                  {t('common.create')}
                </Button>
              </form>
            </details>
          )}
        </Card>
      </div>
    </>
  )
}

interface Eligible {
  threshold_percent: number
  requirements_catalogued: boolean
  students: { student_id: string; name: string; year_group: number; percent: number | null; ready: boolean; gap_count: number; gap_codes: string[] }[]
}

export function EditionDetailPage() {
  const { id = '' } = useParams()
  const { t } = useTranslation()
  const { can } = useAuth()
  const qc = useQueryClient()
  const ed = useQuery({ queryKey: ['edition', id], queryFn: () => api<Edition>(`/editions/${id}`) })
  const reqs = useQuery({ queryKey: ['ed-reqs', id], queryFn: () => api<Requirement[]>(`/editions/${id}/requirements`) })
  const clashes = useQuery({ queryKey: ['ed-clashes', id], queryFn: () => api<{ kind: string; reason: string }[]>(`/editions/${id}/clashes`) })
  const eligible = useQuery({ queryKey: ['ed-eligible', id], queryFn: () => api<Eligible>(`/editions/${id}/eligible-students`), enabled: can('run_readiness') })
  const [toast, show] = useToast()
  const override = useMutation({
    mutationFn: (r: object) => api(`/editions/${id}/requirements`, { method: 'POST', body: [r] }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['ed-reqs', id] })
      void qc.invalidateQueries({ queryKey: ['ed-eligible', id] })
    },
    onError: (e) => show(errorMessage(e), 'error'),
  })
  if (ed.isLoading) return <Loading />
  if (ed.error) return <Alert tone="error">{errorMessage(ed.error)}</Alert>
  const e = ed.data!
  const admin = can('manage_competitions')
  return (
    <>
      {toast}
      <PageHeader
        back={{ to: `/competitions/${e.competition_id}`, label: e.competition_name ?? t('competitions.title') }}
        icon={CalendarDays}
        title={e.name}
        subtitle={`${t(`tiers.${e.tier}` as 'tiers.school')}${e.stage ? ` · ${e.stage}` : ''} · ${fmtDate(e.event_starts)}`}
      />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title={t('common.details')}>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
            <dt className="text-slate-500">{t('competitions.eventDates')}</dt>
            <dd>
              {fmtDate(e.event_starts)} – {fmtDate(e.event_ends)}
            </dd>
            <dt className="text-slate-500">{t('competitions.registration')}</dt>
            <dd>
              {fmtDate(e.registration_opens)} – {fmtDate(e.registration_closes)}
            </dd>
            <dt className="text-slate-500">{t('competitions.venue')}</dt>
            <dd>{e.venue ?? '—'}</dd>
            <dt className="text-slate-500">{t('competitions.fee')}</dt>
            <dd>{fmtMoney(e.entry_fee, e.currency)}</dd>
            <dt className="text-slate-500">{t('competitions.fieldSize')}</dt>
            <dd>{e.expected_field_size ?? '—'}</dd>
            <dt className="text-slate-500">{t('competitions.eligibleYears')}</dt>
            <dd>
              {e.eligible_year_min ?? '—'} – {e.eligible_year_max ?? '—'}
            </dd>
          </dl>
          {e.external_registration_url && (
            <a href={e.external_registration_url} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-brand hover:underline">
              {t('competitions.registerOnline')} <ExternalLink aria-hidden className="h-3.5 w-3.5" />
            </a>
          )}
          {e.rubric.length > 0 && (
            <>
              <h3 className="mt-4">{t('competitions.rubric')}</h3>
              <ul className="mt-1 text-sm">
                {e.rubric.map((r) => (
                  <li key={r.key}>
                    {r.label} (/{r.max_score})
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>
        <Card title={t('competitions.requirements')} description={t('competitions.editionRequirementsHint')} className="lg:col-span-2">
          <RequirementsTable rows={reqs.data ?? []} onRemove={admin ? (r) => override.mutate({ skill_code: r.code, required_level: r.required_level, removed: true }) : undefined} />
          {admin && <RequirementEditor busy={override.isPending} onAdd={(r) => override.mutate(r)} />}
        </Card>
        <Card title={t('competitions.clashes')}>
          {clashes.data?.length ? (
            <ul className="space-y-2 text-sm">
              {clashes.data.map((c, i) => (
                <li key={i}>
                  <Alert tone="warn">{c.reason}</Alert>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-slate-600">{t('competitions.noClashes')}</p>
          )}
        </Card>
        {eligible.data && (
          <Card title={t('competitions.eligible')} description={t('competitions.eligibleHint')} className="lg:col-span-2" inferred actions={<ClaimBadge type="inferred" />}>
            {!eligible.data.requirements_catalogued ? (
              <p className="text-sm">{t('profile.notCatalogued')}</p>
            ) : (
              <ul className="space-y-2">
                {eligible.data.students.slice(0, 40).map((s) => (
                  <li key={s.student_id} className="grid grid-cols-[1fr_auto] items-center gap-2 sm:grid-cols-[200px_1fr_auto]">
                    <Link to={`/students/${s.student_id}`} className="truncate text-sm hover:underline">
                      {s.name} · {s.year_group}
                    </Link>
                    <Meter value={s.percent} threshold={eligible.data.threshold_percent} label={s.name} />
                    <Badge tone={s.ready ? 'green' : 'slate'}>{s.ready ? t('competitions.ready') : t('squads.skillsToGo', { n: s.gap_count })}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}
      </div>
    </>
  )
}
