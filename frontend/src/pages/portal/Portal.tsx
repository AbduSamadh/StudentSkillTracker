import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'

import { RecommendationList } from '@/components/Readiness'
import { Alert, Badge, Button, Card, ClaimBadge, Empty, Loading, PageHeader, SelectInput, TextArea, useToast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { fmtDate, fmtDateTime, fmtMoney, pick } from '@/lib/format'
import { useSkills } from '@/lib/queries'
import type { RecommendationItem } from '@/lib/types'

interface PlainSkill {
  skill_id: string
  domain: string
  label_en: string
  label_ar: string
  level: number
  level_en: string
  level_ar: string
  since: string
}

interface ChildOverview {
  student: { id: string; name: string; name_ar: string | null; year_group: number }
  upcoming_events: { edition_id: string; name: string; competition: string; starts: string; ends: string; venue: string | null; entry_fee: string | null; currency: string }[]
  skills: PlainSkill[]
  results: { edition: string; competition: string; date: string; placement: number | null; field_size: number | null; award: string | null }[]
  consents: { purpose: string; active: boolean; decision: string | null; decided_at: string | null; withdrawn_at: string | null; version: number | null; can_withdraw: boolean }[]
  pending_consent_requests: { id: string; purpose: string; edition_id: string | null; created_at: string }[]
}

export default function ParentHome() {
  const { t } = useTranslation()
  const { me } = useAuth()
  if (!me) return <Loading />
  return (
    <>
      <PageHeader title={t('portal.welcome', { name: me.display_name })} />
      {me.children.length === 0 ? (
        <Empty />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {me.children.map((c) => (
            <Link key={c.id} to={`/portal/children/${c.id}`} className="card block hover:border-brand">
              <h2>{c.name}</h2>
              <p className="text-sm text-slate-600">{t('common.year', { n: c.year_group })}</p>
            </Link>
          ))}
        </div>
      )}
    </>
  )
}

function SkillsByDomain({ skills, name }: { skills: PlainSkill[]; name: string }) {
  const { t } = useTranslation()
  const domains = [...new Set(skills.map((s) => s.domain))]
  return (
    <Card title={t('portal.skills', { name })} actions={<ClaimBadge type="measured" />}>
      <p className="mb-3 text-sm text-slate-600">{t('portal.skillsHint')}</p>
      {skills.length === 0 ? (
        <Empty>{t('portal.noSkills')}</Empty>
      ) : (
        <div className="space-y-4">
          {domains.map((d) => (
            <div key={d}>
              <h3 className="mb-1 text-sm text-slate-500">{t(`domains.${d}` as 'domains.Coding', { defaultValue: d })}</h3>
              <ul className="space-y-1">
                {skills
                  .filter((s) => s.domain === d)
                  .map((s) => (
                    <li key={s.skill_id} className="flex flex-wrap items-baseline justify-between gap-2 border-b border-slate-100 py-1">
                      <span>{pick(s.label_en, s.label_ar)}</span>
                      <span className="text-sm font-semibold text-brand">
                        <span aria-hidden>{'●'.repeat(s.level)}{'○'.repeat(4 - s.level)} </span>
                        {t(`portal.levelWords.${s.level}` as 'portal.levelWords.1')}
                      </span>
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

export function ChildPage() {
  const { id = '' } = useParams()
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [toast, show] = useToast()
  const q = useQuery({ queryKey: ['portal-child', id], queryFn: () => api<ChildOverview>(`/portal/children/${id}`) })
  const [formPurpose, setFormPurpose] = useState<string | null>(null)
  const form = useQuery({
    queryKey: ['consent-form', formPurpose],
    queryFn: () => api<{ title_en: string; body_en: string; title_ar: string; body_ar: string; version: number }>(`/portal/consent-forms/${formPurpose}`),
    enabled: !!formPurpose,
  })
  const refresh = () => void qc.invalidateQueries({ queryKey: ['portal-child', id] })
  const withdraw = useMutation({
    mutationFn: (purpose: string) => api('/portal/consents/withdraw', { method: 'POST', body: { student_id: id, purpose } }),
    onSuccess: () => {
      show(t('common.saved'))
      refresh()
    },
    onError: (e) => show(errorMessage(e), 'error'),
  })
  const decide = useMutation({
    mutationFn: ({ purpose, decision, request }: { purpose: string; decision: 'granted' | 'declined'; request?: { id: string; edition_id: string | null } }) =>
      api('/portal/consents', { method: 'POST', body: { student_id: id, purpose, decision, consent_request_id: request?.id, edition_id: request?.edition_id } }),
    onSuccess: () => {
      setFormPurpose(null)
      show(t('common.saved'))
      refresh()
    },
    onError: (e) => show(errorMessage(e), 'error'),
  })
  if (q.isLoading) return <Loading />
  if (q.error) return <Alert tone="error">{errorMessage(q.error)}</Alert>
  const d = q.data!
  return (
    <>
      {toast}
      <PageHeader title={pick(d.student.name, d.student.name_ar)} subtitle={t('common.year', { n: d.student.year_group })} />
      {d.pending_consent_requests.length > 0 && (
        <Card title={t('portal.pending')} className="mb-4 border-amber-300">
          <ul className="space-y-2">
            {d.pending_consent_requests.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-2">
                <span>{t(`portal.purposes.${r.purpose}` as 'portal.purposes.travel')}</span>
                <span className="flex gap-2">
                  <Button size="sm" onClick={() => decide.mutate({ purpose: r.purpose, decision: 'granted', request: r })}>
                    {t('portal.give')}
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => decide.mutate({ purpose: r.purpose, decision: 'declined', request: r })}>
                    {t('portal.decline')}
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t('portal.upcoming')}>
          {d.upcoming_events.length === 0 ? (
            <p className="text-sm text-slate-600">{t('portal.noUpcoming')}</p>
          ) : (
            <ul className="space-y-3">
              {d.upcoming_events.map((e) => (
                <li key={e.edition_id}>
                  <div className="font-semibold">{e.name}</div>
                  <div className="text-sm text-slate-600">
                    {fmtDate(e.starts)}
                    {e.ends !== e.starts && ` – ${fmtDate(e.ends)}`} · {e.venue}
                  </div>
                  {e.entry_fee && Number(e.entry_fee) > 0 && (
                    <div className="text-xs text-slate-500">
                      {t('portal.fee')}: {fmtMoney(e.entry_fee, e.currency)}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
        <SkillsByDomain skills={d.skills} name={pick(d.student.name, d.student.name_ar).split(' ')[0]} />
        <Card title={t('portal.results')} actions={<ClaimBadge type="measured" />}>
          {d.results.length === 0 ? (
            <Empty />
          ) : (
            <ul className="space-y-2 text-sm">
              {d.results.map((r, i) => (
                <li key={i}>
                  <div className="font-medium">{r.edition}</div>
                  <div className="text-slate-600">
                    {fmtDate(r.date)} · {r.placement && r.field_size ? t('profile.placement', { p: r.placement, f: r.field_size }) : ''} {r.award && <Badge tone="green">{r.award}</Badge>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title={t('portal.consents')}>
          <ul className="space-y-2">
            {d.consents.map((c) => (
              <li key={c.purpose} className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-2">
                <div>
                  <div className="font-medium">{t(`portal.purposes.${c.purpose}` as 'portal.purposes.media')}</div>
                  <div className="text-xs text-slate-500">{c.decided_at && `${fmtDate(c.decided_at)} · v${c.version}`}</div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone={c.active ? 'green' : 'slate'}>
                    <span aria-hidden>{c.active ? '✓ ' : '— '}</span>
                    {c.active ? t('portal.consentGiven') : t('portal.consentNotGiven')}
                  </Badge>
                  {c.active ? (
                    <Button size="sm" variant="secondary" busy={withdraw.isPending} onClick={() => window.confirm(`${t('portal.withdraw')}?`) && withdraw.mutate(c.purpose)}>
                      {t('portal.withdraw')}
                    </Button>
                  ) : (
                    c.purpose !== 'travel' &&
                    c.purpose !== 'fee_authorisation' && (
                      <Button size="sm" variant="ghost" onClick={() => setFormPurpose(c.purpose)}>
                        {t('portal.give')}
                      </Button>
                    )
                  )}
                </div>
              </li>
            ))}
          </ul>
          {formPurpose && form.data && (
            <div className="mt-4 rounded-lg border border-slate-200 p-3">
              <h3>{pick(form.data.title_en, form.data.title_ar)}</h3>
              <p className="mt-1 text-sm">{pick(form.data.body_en, form.data.body_ar)}</p>
              <div className="mt-3 flex gap-2">
                <Button size="sm" onClick={() => decide.mutate({ purpose: formPurpose, decision: 'granted' })}>
                  {t('portal.give')}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setFormPurpose(null)}>
                  {t('common.cancel')}
                </Button>
              </div>
            </div>
          )}
        </Card>
      </div>
    </>
  )
}

export function PortalMessages() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['portal-messages'], queryFn: () => api<{ delivery_id: string; type: string; subject: string | null; body: string | null; sent_at: string; opened_at: string | null; is_emergency: boolean }[]>('/portal/messages') })
  const read = useMutation({ mutationFn: (id: string) => api(`/portal/messages/${id}/read`, { method: 'POST' }), onSuccess: () => void qc.invalidateQueries({ queryKey: ['portal-messages'] }) })
  return (
    <>
      <PageHeader title={t('portal.messages')} />
      {q.isLoading ? (
        <Loading />
      ) : !q.data?.length ? (
        <Empty>{t('portal.noMessages')}</Empty>
      ) : (
        <ul className="space-y-3">
          {q.data.map((m) => (
            <li key={m.delivery_id}>
              <details className={`card ${m.is_emergency ? 'border-red-400' : ''}`} onToggle={(e) => (e.target as HTMLDetailsElement).open && !m.opened_at && read.mutate(m.delivery_id)}>
                <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2">
                  <span className={m.opened_at ? '' : 'font-semibold'}>{m.subject}</span>
                  <span className="text-xs text-slate-500">
                    {t(`messageTypes.${m.type}` as 'messageTypes.logistics')} · {fmtDateTime(m.sent_at)}
                  </span>
                </summary>
                <pre className="mt-3 whitespace-pre-wrap font-sans text-sm [overflow-wrap:anywhere]">{m.body}</pre>
              </details>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

export function PortalPreferences() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [toast, show] = useToast()
  type Prefs = { guardians: { guardian_id: string; preferred_channel: string; language: string; has_whatsapp: boolean; has_phone: boolean; categories: { category: string; opted_out: boolean }[] }[] }
  const q = useQuery({ queryKey: ['portal-prefs'], queryFn: () => api<Prefs>('/portal/preferences') })
  const save = useMutation({
    mutationFn: (body: object) => api('/portal/preferences', { method: 'PATCH', body }),
    onSuccess: () => {
      show(t('common.saved'))
      void qc.invalidateQueries({ queryKey: ['portal-prefs'] })
    },
  })
  const opt = useMutation({
    mutationFn: (body: { category: string; opted_out: boolean }) => api('/portal/opt-outs', { method: 'POST', body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['portal-prefs'] }),
    onError: (e) => show(errorMessage(e), 'error'),
  })
  if (q.isLoading) return <Loading />
  const g = q.data?.guardians[0]
  if (!g) return <Empty />
  return (
    <>
      {toast}
      <PageHeader title={t('portal.preferences')} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <div className="grid gap-3">
            <SelectInput label={t('portal.channel')} value={g.preferred_channel} onChange={(e) => save.mutate({ preferred_channel: e.target.value })}>
              {(['whatsapp', 'email', 'sms', 'in_app'] as const)
                .filter((c) => (c === 'whatsapp' ? g.has_whatsapp : c === 'sms' ? g.has_phone : true))
                .map((c) => (
                  <option key={c} value={c}>
                    {t(`channels.${c}`)}
                  </option>
                ))}
            </SelectInput>
            <SelectInput label={t('common.language')} value={g.language} onChange={(e) => save.mutate({ language: e.target.value })}>
              <option value="en">English</option>
              <option value="ar">العربية</option>
            </SelectInput>
          </div>
        </Card>
        <Card title={t('portal.categories')}>
          <ul className="space-y-2">
            {g.categories.map((c) => (
              <li key={c.category} className="flex items-center justify-between gap-2">
                <span>{t(`messageTypes.${c.category}` as 'messageTypes.logistics')}</span>
                <Button size="sm" variant={c.opted_out ? 'primary' : 'secondary'} onClick={() => opt.mutate({ category: c.category, opted_out: !c.opted_out })}>
                  {c.opted_out ? t('portal.receive') : t('portal.stop')}
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  )
}

export function StudentHome() {
  const { t } = useTranslation()
  const { me } = useAuth()
  const [toast, show] = useToast()
  const skills = useSkills()
  const q = useQuery({
    queryKey: ['student-home'],
    queryFn: () => api<{ student: { id: string; name: string }; skills: PlainSkill[]; recommended: RecommendationItem[]; almost_ready: RecommendationItem[] }>('/portal/student'),
    retry: false,
  })
  const [skill, setSkill] = useState('')
  const [level, setLevel] = useState(2)
  const [note, setNote] = useState('')
  const self = useMutation({
    mutationFn: () => api('/skills/awards/self-assess', { method: 'POST', body: { student_id: me?.student_portal?.student_id, skill_id: skill, level, evidence_note: note } }),
    onSuccess: () => {
      setNote('')
      show(t('common.saved'))
    },
    onError: (e) => show(errorMessage(e), 'error'),
  })
  if (q.isLoading) return <Loading />
  if (q.error) return <Alert tone="info">{errorMessage(q.error)}</Alert>
  const d = q.data!
  return (
    <>
      {toast}
      <PageHeader title={t('student.title')} subtitle={d.student.name} />
      <div className="grid gap-4 lg:grid-cols-2">
        <SkillsByDomain skills={d.skills} name={d.student.name.split(' ')[0]} />
        <div className="space-y-4">
          <RecommendationList items={d.recommended} kind="recommended" />
          <RecommendationList items={d.almost_ready} kind="almost" />
          <Card title={t('student.selfAssess')}>
            <p className="mb-2 text-sm text-slate-600">{t('student.selfAssessHint')}</p>
            <div className="grid gap-3">
              <SelectInput label={t('capture.skill')} value={skill} onChange={(e) => setSkill(e.target.value)}>
                <option value="" />
                {(skills.data ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {pick(s.parent_label_en, s.parent_label_ar)}
                  </option>
                ))}
              </SelectInput>
              <SelectInput label={t('capture.level')} value={level} onChange={(e) => setLevel(Number(e.target.value))}>
                {[1, 2, 3, 4].map((l) => (
                  <option key={l} value={l}>
                    {t(`portal.levelWords.${l}` as 'portal.levelWords.1')}
                  </option>
                ))}
              </SelectInput>
              <TextArea label={t('capture.evidenceNote')} value={note} onChange={(e) => setNote(e.target.value)} />
              <Button disabled={!skill} busy={self.isPending} onClick={() => self.mutate()}>
                {t('common.submit')}
              </Button>
            </div>
          </Card>
        </div>
      </div>
    </>
  )
}
