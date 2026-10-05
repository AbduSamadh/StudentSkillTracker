import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'

import { ReadinessTrace, RecommendationList } from '@/components/Readiness'
import { Alert, Badge, Button, Card, ClaimBadge, Empty, LevelPill, Loading, PageHeader, SelectInput, TableWrap, Tabs, useToast } from '@/components/ui'
import { api, downloadBlob, errorMessage } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { fmtDate } from '@/lib/format'
import { useEditions, useSkills, useStaffDirectory } from '@/lib/queries'
import type { Award, Readiness, Recommendations, Student } from '@/lib/types'

interface Detail extends Student {
  guardians: { id: string; full_name: string; relationship: string; preferred_channel: string; language: string; email: string | null; phone: string | null }[]
  squads: { squad_id: string; squad_name: string; role: string | null; status: string }[]
  open_flags: { id: string; kind: string; rule: string; explanation: string; raised_at: string }[]
}

interface Profile {
  skills: { skill_id: string; code: string; name: string; domain: string; strand: string; level: number; latest_award: Award }[]
  award_timeline: Award[]
  results: { result_id: string; edition_name: string; competition_name: string; tier: string; event_starts: string; placement: number | null; field_size: number | null; performance_index: string | null; award_title: string | null; data_quality_flags: string[] }[]
  attendance: { attended: number; denominator: number; rate_percent: number | null }
  goals: { id: string; skill_code: string; skill_name: string; target_level: number; target_date: string | null; status: string; origin: string }[]
}

type Tab = 'skills' | 'readiness' | 'recommendations' | 'history' | 'goals' | 'consents'

export default function StudentProfilePage() {
  const { id = '' } = useParams()
  const { t } = useTranslation()
  const { can } = useAuth()
  const [tab, setTab] = useState<Tab>('skills')
  const detail = useQuery({ queryKey: ['student', id], queryFn: () => api<Detail>(`/students/${id}`) })
  const profile = useQuery({ queryKey: ['profile', id], queryFn: () => api<Profile>(`/students/${id}/profile`) })
  const [toast, show] = useToast()

  if (detail.isLoading) return <Loading />
  if (detail.error) return <Alert tone="error">{errorMessage(detail.error)}</Alert>
  const s = detail.data!

  const sar = async () => {
    const reason = window.prompt(t('profile.exportReason'))
    if (!reason || reason.length < 3) return
    try {
      await downloadBlob(`/students/${id}/export?reason=${encodeURIComponent(reason)}`, 'subject-access.zip')
    } catch (e) {
      show(errorMessage(e), 'error')
    }
  }
  const profileReport = async () => {
    try {
      await api('/reports/student_profile/generate', { method: 'POST', body: { format: 'pdf', params: { student_id: id } } })
      show(t('common.saved'))
    } catch (e) {
      show(errorMessage(e), 'error')
    }
  }

  return (
    <>
      {toast}
      <PageHeader
        title={s.display_name}
        subtitle={
          <span className="flex flex-wrap gap-2">
            <span>{t('common.year', { n: s.year_group })}</span>
            {s.house && <span>· {s.house}</span>}
            <span>· {s.external_mis_id}</span>
            {s.squads.filter((q) => q.status === 'active').map((q) => (
              <Badge key={q.squad_id} tone="brand">
                {q.squad_name}
                {q.role ? ` · ${q.role}` : ''}
              </Badge>
            ))}
          </span>
        }
        actions={
          <>
            {can('generate_reports') && (
              <Button variant="secondary" onClick={() => void profileReport()}>
                {t('profile.profileReport')}
              </Button>
            )}
            {can('subject_access_export') && (
              <Button variant="secondary" onClick={() => void sar()}>
                {t('profile.exportSar')}
              </Button>
            )}
          </>
        }
      />
      {s.open_flags.length > 0 && (
        <div className="mb-4 space-y-2">
          {s.open_flags.map((f) => (
            <div key={f.id} className="card claim-inferred flex flex-wrap items-start gap-3 border-violet-300">
              <Badge tone="amber">{t(`flagKinds.${f.kind}` as 'flagKinds.plateau')}</Badge>
              <div className="flex-1 text-sm">
                <p>{f.explanation}</p>
                <p className="text-xs text-slate-500">
                  {t('students.rule')}: {f.rule}
                </p>
              </div>
              <ClaimBadge type="inferred" />
            </div>
          ))}
        </div>
      )}
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={(['skills', 'readiness', 'recommendations', 'history', 'goals', 'consents'] as Tab[]).map((x) => ({ id: x, label: t(`profile.tabs.${x}`) }))}
      />
      {tab === 'skills' && <SkillsTab profile={profile.data} loading={profile.isLoading} />}
      {tab === 'readiness' && <ReadinessTab studentId={id} />}
      {tab === 'recommendations' && <RecommendationsTab studentId={id} />}
      {tab === 'history' && <HistoryTab profile={profile.data} />}
      {tab === 'goals' && <GoalsTab studentId={id} profile={profile.data} />}
      {tab === 'consents' && <ConsentsTab studentId={id} guardians={s.guardians} />}
    </>
  )
}

function SkillsTab({ profile, loading }: { profile?: Profile; loading: boolean }) {
  const { t } = useTranslation()
  const name = useStaffDirectory()
  if (loading || !profile) return <Loading />
  if (profile.skills.length === 0) return <Empty>{t('profile.noSkills')}</Empty>
  const domains = [...new Set(profile.skills.map((s) => s.domain))]
  return (
    <div className="space-y-4">
      {domains.map((d) => (
        <Card key={d} title={t(`domains.${d}` as 'domains.Coding', { defaultValue: d })} actions={<ClaimBadge type="measured" />}>
          <TableWrap>
            <table>
              <thead>
                <tr>
                  <th>{t('skills.code')}</th>
                  <th>{t('capture.skill')}</th>
                  <th>{t('capture.level')}</th>
                  <th>{t('profile.evidence')}</th>
                  <th>{t('profile.verifiedBy')}</th>
                </tr>
              </thead>
              <tbody>
                {profile.skills
                  .filter((s) => s.domain === d)
                  .map((s) => (
                    <tr key={s.skill_id}>
                      <td className="font-mono text-xs">{s.code}</td>
                      <td>{s.name}</td>
                      <td>
                        <LevelPill level={s.level} />
                      </td>
                      <td className="text-xs">
                        <div>{t(`profile.sources.${s.latest_award.source}` as 'profile.sources.teacher')} · {s.latest_award.confidence}</div>
                        <div className="text-slate-600">{s.latest_award.evidence_note}</div>
                      </td>
                      <td className="text-xs">
                        {name(s.latest_award.verified_by_id)}
                        <div className="text-slate-500">{fmtDate(s.latest_award.awarded_on)}</div>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </TableWrap>
        </Card>
      ))}
    </div>
  )
}

function ReadinessTab({ studentId }: { studentId: string }) {
  const { t } = useTranslation()
  const editions = useEditions(true)
  const [edition, setEdition] = useState('')
  const name = useStaffDirectory()
  const r = useQuery({
    queryKey: ['readiness', studentId, edition],
    queryFn: () => api<Readiness>(`/students/${studentId}/readiness?edition_id=${edition}`),
    enabled: !!edition,
  })
  return (
    <Card>
      <div className="mb-4 max-w-md">
        <SelectInput label={t('profile.readinessFor')} value={edition} onChange={(e) => setEdition(e.target.value)}>
          <option value="">{t('profile.chooseEdition')}</option>
          {(editions.data ?? []).map((e) => (
            <option key={e.id} value={e.id}>
              {e.name}
            </option>
          ))}
        </SelectInput>
      </div>
      {r.isLoading && edition ? <Loading /> : r.data ? <ReadinessTrace r={r.data} verifierName={name} /> : null}
      {r.error && <Alert tone="error">{errorMessage(r.error)}</Alert>}
    </Card>
  )
}

function RecommendationsTab({ studentId }: { studentId: string }) {
  const r = useQuery({ queryKey: ['recs', studentId], queryFn: () => api<Recommendations>(`/students/${studentId}/recommendations`) })
  if (r.isLoading) return <Loading />
  if (r.error) return <Alert tone="error">{errorMessage(r.error)}</Alert>
  const d = r.data!
  if (!d.recommended.length && !d.almost_ready.length && !d.not_recommended.length) return <Empty />
  return (
    <div className="space-y-4">
      <RecommendationList items={d.recommended} kind="recommended" />
      <RecommendationList items={d.almost_ready} kind="almost" />
      <RecommendationList items={d.not_recommended} kind="not" />
    </div>
  )
}

function HistoryTab({ profile }: { profile?: Profile }) {
  const { t } = useTranslation()
  if (!profile) return <Loading />
  const att = profile.attendance
  return (
    <div className="space-y-4">
      <Card title={t('profile.attendance')} actions={<ClaimBadge type="measured" />}>
        <p className="text-sm">{att.rate_percent === null ? '—' : t('profile.attendanceRate', { rate: att.rate_percent, n: att.denominator })}</p>
      </Card>
      <Card title={t('profile.results')} actions={<ClaimBadge type="measured" />}>
        {profile.results.length === 0 ? (
          <Empty />
        ) : (
          <TableWrap>
            <table>
              <thead>
                <tr>
                  <th>{t('common.date')}</th>
                  <th>{t('capture.edition')}</th>
                  <th>{t('capture.placement')}</th>
                  <th>{t('profile.index')}</th>
                  <th>{t('capture.award')}</th>
                </tr>
              </thead>
              <tbody>
                {profile.results.map((r) => (
                  <tr key={r.result_id}>
                    <td>{fmtDate(r.event_starts)}</td>
                    <td>
                      {r.edition_name}
                      <div className="text-xs text-slate-500">{t(`tiers.${r.tier}` as 'tiers.school')}</div>
                    </td>
                    <td>
                      {r.placement && r.field_size ? (
                        t('profile.placement', { p: r.placement, f: r.field_size })
                      ) : (
                        <Badge tone="amber">{t('profile.fieldMissing')}</Badge>
                      )}
                    </td>
                    <td className="tabular-nums">{r.performance_index ?? '—'}</td>
                    <td>{r.award_title ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <Card title={t('profile.timeline')}>
        <ul className="space-y-1 text-sm">
          {profile.award_timeline
            .slice()
            .reverse()
            .map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-2">
                <span className="w-24 text-slate-500">{fmtDate(a.awarded_on)}</span>
                <span className="font-mono text-xs">{a.skill_code}</span>
                <LevelPill level={a.level} />
                <Badge tone={a.status === 'verified' ? 'green' : a.status === 'proposed' ? 'amber' : 'slate'}>{a.status}</Badge>
                <ClaimBadge type={a.claim_type} />
              </li>
            ))}
        </ul>
      </Card>
    </div>
  )
}

function GoalsTab({ studentId, profile }: { studentId: string; profile?: Profile }) {
  const { t } = useTranslation()
  const { can } = useAuth()
  const qc = useQueryClient()
  const skills = useSkills()
  const [skill, setSkill] = useState('')
  const [level, setLevel] = useState(3)
  const [date, setDate] = useState('')
  const add = useMutation({
    mutationFn: () => api(`/students/${studentId}/goals`, { method: 'POST', body: { skill_id: skill, target_level: level, target_date: date || null } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profile', studentId] }),
  })
  return (
    <Card title={t('profile.goals')}>
      {profile?.goals.length ? (
        <ul className="mb-4 space-y-2 text-sm">
          {profile.goals.map((g) => (
            <li key={g.id} className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs">{g.skill_code}</span>
              <span>{g.skill_name}</span>
              <LevelPill level={g.target_level} />
              {g.target_date && <span className="text-slate-500">{fmtDate(g.target_date)}</span>}
              <Badge>{g.status}</Badge>
            </li>
          ))}
        </ul>
      ) : (
        <Empty />
      )}
      {can('verify_skills') && (
        <form
          className="grid gap-3 sm:grid-cols-4"
          onSubmit={(e) => {
            e.preventDefault()
            add.mutate()
          }}
        >
          <SelectInput label={t('capture.skill')} value={skill} onChange={(e) => setSkill(e.target.value)} required>
            <option value="" />
            {(skills.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.code} — {s.name}
              </option>
            ))}
          </SelectInput>
          <SelectInput label={t('profile.targetLevel')} value={level} onChange={(e) => setLevel(Number(e.target.value))}>
            {[1, 2, 3, 4].map((l) => (
              <option key={l} value={l}>
                {t(`levels.${l}` as 'levels.1')}
              </option>
            ))}
          </SelectInput>
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-slate-700" htmlFor="goal-date">
              {t('profile.targetDate')}
            </label>
            <input id="goal-date" type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
          <div className="flex items-end">
            <Button type="submit" busy={add.isPending}>
              {t('profile.addGoal')}
            </Button>
          </div>
          {add.error && <Alert tone="error">{errorMessage(add.error)}</Alert>}
        </form>
      )}
    </Card>
  )
}

function ConsentsTab({ studentId, guardians }: { studentId: string; guardians: Detail['guardians'] }) {
  const { t } = useTranslation()
  const q = useQuery({
    queryKey: ['consents', studentId],
    queryFn: () => api<{ current: Record<string, boolean>; history: { id: string; purpose: string; decision: string; decided_at: string; withdrawn_at: string | null; method: string; version: number }[] }>(`/students/${studentId}/consents`),
  })
  if (q.isLoading) return <Loading />
  if (q.error) return <Alert tone="error">{errorMessage(q.error)}</Alert>
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title={t('profile.consentCurrent')}>
        <ul className="space-y-2 text-sm">
          {Object.entries(q.data!.current).map(([purpose, ok]) => (
            <li key={purpose} className="flex items-center justify-between">
              <span>{t(`portal.purposes.${purpose}` as 'portal.purposes.media')}</span>
              <Badge tone={ok ? 'green' : 'slate'}>
                <span aria-hidden>{ok ? '✓ ' : '— '}</span>
                {ok ? t('portal.consentGiven') : t('portal.consentNotGiven')}
              </Badge>
            </li>
          ))}
        </ul>
        <h3 className="mt-4">{t('students.guardians')}</h3>
        <ul className="mt-2 space-y-1 text-sm">
          {guardians.map((g) => (
            <li key={g.id}>
              {g.full_name} · {g.relationship} · {t(`channels.${g.preferred_channel}` as 'channels.email')} · {g.language.toUpperCase()}
              {g.email && <div className="text-xs text-slate-500" dir="ltr">{g.email}</div>}
            </li>
          ))}
        </ul>
      </Card>
      <Card title={t('profile.consentHistory')}>
        <ul className="space-y-1 text-sm">
          {q.data!.history.map((c) => (
            <li key={c.id}>
              {fmtDate(c.decided_at)} · {t(`portal.purposes.${c.purpose}` as 'portal.purposes.media')} · {c.decision} (v{c.version}, {c.method})
              {c.withdrawn_at && <span className="text-red-700"> · {t('portal.withdraw')} {fmtDate(c.withdrawn_at)}</span>}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
