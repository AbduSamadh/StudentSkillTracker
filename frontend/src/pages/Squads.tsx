import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarDays, CalendarPlus, ClipboardCheck, Medal, Plus, Sparkles, Trophy, UsersRound } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'

import { Alert, Badge, Button, ButtonLink, Card, ClaimBadge, EmptyState, Loading, Meter, PageHeader, SelectInput, TableWrap, Tabs, TextInput, useToast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { fmtDate, pick } from '@/lib/format'
import { useSkills, useSquad, useSquads } from '@/lib/queries'
import { SquadCards } from '@/pages/Home'
import type { Page, Student } from '@/lib/types'

export default function SquadsPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const squads = useSquads()
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [discipline, setDiscipline] = useState('Robotics')
  const create = useMutation({
    mutationFn: () => api('/squads', { method: 'POST', body: { name, discipline } }),
    onSuccess: () => {
      setName('')
      void qc.invalidateQueries({ queryKey: ['squads'] })
    },
  })
  if (squads.isLoading) return <Loading />
  if (squads.error) return <Alert tone="error">{errorMessage(squads.error)}</Alert>
  return (
    <>
      <PageHeader icon={UsersRound} title={t('squads.title')} subtitle={t('squads.intro')} />
      {squads.data!.length === 0 ? (
        <EmptyState icon={UsersRound} title={t('home.noSquads')}>
          {t('home.noSquadsHint')}
        </EmptyState>
      ) : (
        <SquadCards />
      )}
      {can('manage_squads') && (
        <Card title={t('squads.newSquad')} description={t('squads.newSquadHint')} icon={Plus} className="mt-8">
          <form
            className="grid gap-3 sm:grid-cols-3"
            onSubmit={(e) => {
              e.preventDefault()
              create.mutate()
            }}
          >
            <TextInput label={t('common.name')} value={name} onChange={(e) => setName(e.target.value)} required />
            <SelectInput label={t('competitions.discipline')} value={discipline} onChange={(e) => setDiscipline(e.target.value)}>
              {['Robotics', 'Coding', 'AI', 'Engineering', 'Science', 'Design'].map((d) => (
                <option key={d}>{d}</option>
              ))}
            </SelectInput>
            <div className="flex items-end">
              <Button type="submit" busy={create.isPending}>
                {t('common.create')}
              </Button>
            </div>
          </form>
        </Card>
      )}
    </>
  )
}

interface SquadReadiness {
  threshold_percent: number
  editions: {
    edition_id: string
    edition_name: string
    event_starts: string
    ready_count: number
    member_count: number
    students: { student_id: string; name: string; percent: number | null; gap_count: number; gap_codes: string[] }[]
    shared_gaps: { skill_id: string; code: string; name: string; required_level: number; student_count: number; unblocks: number; students: { student_id: string; name: string; earned_level: number }[] }[]
    clusters: { missing_codes: string[]; skills_away: number; students: { student_id: string; name: string }[] }[]
  }[]
  suggested_session_focus: { skill_id: string; code: string; name: string; reason_en: string; reason_ar: string }[]
}

export function SquadDetailPage() {
  const { id = '' } = useParams()
  const { t } = useTranslation()
  const { can } = useAuth()
  const squad = useSquad(id)
  const [tab, setTab] = useState<'roster' | 'readiness'>('roster')
  const [toast, show] = useToast()
  if (squad.isLoading) return <Loading />
  if (squad.error) return <Alert tone="error">{errorMessage(squad.error)}</Alert>
  const s = squad.data!
  const copyCalendar = async () => {
    const r = await api<{ url: string }>(`/squads/${id}/calendar-link`, { method: 'POST' })
    await navigator.clipboard?.writeText(r.url).catch(() => undefined)
    show(t('squads.calendarCopied'))
  }
  return (
    <>
      {toast}
      <PageHeader
        back={{ to: '/squads', label: t('squads.title') }}
        icon={UsersRound}
        title={s.name}
        subtitle={`${s.discipline ?? ''} · ${t('squads.coaches')}: ${s.coaches.map((c) => c.name).join(', ') || '—'}`}
        actions={
          <Button variant="ghost" size="sm" icon={CalendarPlus} onClick={() => void copyCalendar()}>
            {t('squads.calendar')}
          </Button>
        }
      />
      <div className="mb-6 flex flex-wrap gap-2">
        {can('record_attendance') && (
          <ButtonLink to={`/capture/attendance?squad=${id}`} variant="secondary" icon={ClipboardCheck}>
            {t('nav.attendance')}
          </ButtonLink>
        )}
        {can('verify_skills') && (
          <ButtonLink to={`/capture/tag?squad=${id}`} variant="secondary" icon={Sparkles}>
            {t('nav.quickTag')}
          </ButtonLink>
        )}
        {can('record_results') && (
          <ButtonLink to={`/capture/result?squad=${id}`} variant="secondary" icon={Medal}>
            {t('capture.resultTitle')}
          </ButtonLink>
        )}
      </div>
      <Card title={t('squads.targets')} icon={Trophy} className="mb-6">
        {s.target_editions.length === 0 ? (
          <p className="text-sm text-slate-500">{t('squads.noTargets')}</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {s.target_editions.map((e) => (
              <li key={e.edition_id}>
                <Link to={`/editions/${e.edition_id}`} className="inline-flex items-center gap-2 rounded-xl border border-slate-200 px-3 py-2 text-sm hover:border-brand/50 hover:bg-slate-50">
                  <CalendarDays aria-hidden className="h-4 w-4 text-slate-400" />
                  <span className="font-medium text-slate-800">{e.name}</span> <span className="text-slate-500">{fmtDate(e.event_starts)}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'roster', label: t('squads.members') },
          { id: 'readiness', label: t('squads.readiness') },
        ]}
      />
      {tab === 'roster' ? <Roster squadId={id} /> : <SquadReadinessView squadId={id} />}
    </>
  )
}

function Roster({ squadId }: { squadId: string }) {
  const { t } = useTranslation()
  const { can } = useAuth()
  const qc = useQueryClient()
  const squad = useSquad(squadId)
  const [search, setSearch] = useState('')
  const [role, setRole] = useState('')
  const canEdit = can('manage_squad_members')
  const found = useQuery({
    queryKey: ['student-search', search],
    queryFn: () => api<Page<Student>>(`/students?q=${encodeURIComponent(search)}&limit=10`),
    enabled: canEdit && search.length >= 2,
  })
  const refresh = () => qc.invalidateQueries({ queryKey: ['squad', squadId] })
  const add = useMutation({
    mutationFn: (studentId: string) => api(`/squads/${squadId}/members`, { method: 'POST', body: { student_id: studentId, role: role || null } }),
    onSuccess: () => {
      setSearch('')
      void refresh()
    },
  })
  const patch = useMutation({
    mutationFn: ({ mid, body }: { mid: string; body: object }) => api(`/squads/${squadId}/members/${mid}`, { method: 'PATCH', body }),
    onSuccess: () => void refresh(),
  })
  const members = squad.data?.members ?? []
  return (
    <Card>
      <TableWrap>
        <table>
          <thead>
            <tr>
              <th>{t('common.name')}</th>
              <th>{t('common.yearGroup')}</th>
              <th>{t('squads.role')}</th>
              <th>{t('common.status')}</th>
              {canEdit && <th>{t('common.actions')}</th>}
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id} className={m.status === 'withdrawn' ? 'opacity-60' : ''}>
                <td>
                  <Link to={`/students/${m.student_id}`} className="font-medium text-brand hover:underline">
                    {m.name}
                  </Link>
                </td>
                <td>{m.year_group}</td>
                <td>
                  {m.role ?? '—'} {m.is_reserve && <Badge>{t('squads.reserve')}</Badge>}
                </td>
                <td>
                  {m.status === 'withdrawn' ? (
                    <span title={m.withdrawal_reason ?? ''}>
                      <Badge tone="slate">{t('squads.withdrawn')}</Badge>
                    </span>
                  ) : (
                    <Badge tone="green">{t('squads.active')}</Badge>
                  )}
                </td>
                {canEdit && (
                  <td className="space-x-2 rtl:space-x-reverse">
                    {m.status === 'active' && (
                      <>
                        <Button size="sm" variant="ghost" onClick={() => patch.mutate({ mid: m.id, body: { is_reserve: !m.is_reserve } })}>
                          {t('squads.reserve')}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            const reason = window.prompt(t('squads.withdrawReason'))
                            if (reason) patch.mutate({ mid: m.id, body: { withdraw: true, withdrawal_reason: reason } })
                          }}
                        >
                          {t('squads.withdraw')}
                        </Button>
                      </>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
      {canEdit && (
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <TextInput label={t('squads.addMember')} value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('students.searchPlaceholder')} />
          <TextInput label={t('squads.role')} value={role} onChange={(e) => setRole(e.target.value)} placeholder="driver, programmer…" />
          {found.data && (
            <ul className="sm:col-span-3">
              {found.data.items
                .filter((st) => !members.some((m) => m.student_id === st.id && m.status === 'active'))
                .map((st) => (
                  <li key={st.id} className="flex items-center justify-between border-b border-slate-100 py-1 text-sm">
                    <span>
                      {st.display_name} · {t('common.year', { n: st.year_group })}
                    </span>
                    <Button size="sm" onClick={() => add.mutate(st.id)} busy={add.isPending}>
                      {t('common.add')}
                    </Button>
                  </li>
                ))}
            </ul>
          )}
          {(add.error || patch.error) && <Alert tone="error">{errorMessage(add.error ?? patch.error)}</Alert>}
        </div>
      )}
    </Card>
  )
}

function SquadReadinessView({ squadId }: { squadId: string }) {
  const { t } = useTranslation()
  const skills = useSkills()
  const skillName = new Map((skills.data ?? []).map((s) => [s.code, s.name]))
  const q = useQuery({ queryKey: ['squad-readiness', squadId], queryFn: () => api<SquadReadiness>(`/squads/${squadId}/readiness`) })
  if (q.isLoading) return <Loading />
  if (q.error) return <Alert tone="error">{errorMessage(q.error)}</Alert>
  const d = q.data!
  if (d.editions.length === 0) return <EmptyState icon={Trophy} title={t('squads.noTargets')}>{t('squads.noTargetsHint')}</EmptyState>
  return (
    <div className="space-y-4">
      {d.suggested_session_focus.length > 0 && (
        <Card title={t('squads.focus')} inferred actions={<ClaimBadge type="inferred" />}>
          <ol className="list-decimal space-y-1 ps-5 text-sm">
            {d.suggested_session_focus.map((f) => (
              <li key={f.skill_id}>{pick(f.reason_en, f.reason_ar)}</li>
            ))}
          </ol>
        </Card>
      )}
      {d.editions.map((e) => (
        <Card key={e.edition_id} title={`${e.edition_name} · ${fmtDate(e.event_starts)}`} inferred actions={<Badge tone="brand">{t('squads.readyCount', { n: e.ready_count, m: e.member_count })}</Badge>}>
          <div className="grid gap-6 lg:grid-cols-2">
            <div>
              <h3 className="mb-2">{t('squads.readiness')}</h3>
              <ul className="space-y-2">
                {e.students.map((s) => (
                  <li key={s.student_id}>
                    <div className="flex items-center justify-between text-sm">
                      <Link to={`/students/${s.student_id}`} className="hover:underline">
                        {s.name}
                      </Link>
                      <span className="text-xs text-slate-500">{s.gap_count > 0 ? t('squads.skillsToGo', { n: s.gap_count }) : t('competitions.ready')}</span>
                    </div>
                    <Meter value={s.percent} threshold={d.threshold_percent} label={s.name} />
                  </li>
                ))}
              </ul>
            </div>
            <div className="space-y-4">
              <div>
                <h3>{t('squads.sharedGaps')}</h3>
                <p className="mb-2 text-xs text-slate-500">{t('squads.sharedGapsHint')}</p>
                {e.shared_gaps.length === 0 ? (
                  <p className="text-sm text-emerald-800">{t('profile.noGaps')}</p>
                ) : (
                  <ul className="space-y-2">
                    {e.shared_gaps.map((g) => (
                      <li key={g.skill_id} className="rounded-lg border border-slate-200 p-2 text-sm">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="font-medium text-slate-800">{g.name}</span>
                          {g.unblocks > 0 && <Badge tone="green">{t('squads.unblocks', { n: g.unblocks })}</Badge>}
                        </div>
                        <div className="text-xs text-slate-600">
                          {t('squads.studentsWithGap', { n: g.student_count, m: e.member_count })}: {g.students.map((s) => s.name).join(', ')}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              {e.clusters.length > 0 && (
                <div>
                  <h3 className="mb-2">{t('squads.clusters')}</h3>
                  <ul className="space-y-2 text-sm">
                    {e.clusters.map((c) => (
                      <li key={c.missing_codes.join()} className="rounded-lg bg-slate-50 p-2">
                        <Badge tone="amber">{t('squads.skillsAway', { n: c.skills_away })}</Badge>{' '}
                        <span className="text-slate-700">{c.missing_codes.map((code) => skillName.get(code) ?? code).join(' + ')}</span>
                        <div className="mt-1 text-slate-700">{c.students.map((s) => s.name).join(', ')}</div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        </Card>
      ))}
    </div>
  )
}
