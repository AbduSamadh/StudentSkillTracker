import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { Alert, Badge, Button, Card, Empty, Loading, PageHeader, SelectInput, TableWrap, TextArea, TextInput, cx, useToast } from '@/components/ui'
import { api, downloadBlob, errorMessage } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { fmtDateTime } from '@/lib/format'
import { useEditions, useSquads } from '@/lib/queries'
import type { Message, Page, Student, Template } from '@/lib/types'

const TYPES = ['selection_notice', 'logistics', 'consent_request', 'result_notification', 'progress_report', 'celebration', 'attendance_concern', 'non_selection', 'behaviour_note'] as const
const NEGATIVE = new Set(['attendance_concern', 'non_selection', 'behaviour_note'])
// Filled in automatically from the student, guardian, edition and squad.
const AUTO_VARS = new Set(['guardian_name', 'child_name', 'child_names', 'school_name', 'portal_link', 'opt_out_link', 'edition_name', 'competition_name', 'event_date', 'venue', 'squad_name', 'result_summary', 'team_members', 'progress_summary'])

export default function MessagesPage() {
  const { t } = useTranslation()
  const [status, setStatus] = useState('')
  const q = useQuery({ queryKey: ['messages', status], queryFn: () => api<Message[]>(`/messages${status ? `?status=${status}` : ''}`) })
  const optOuts = useQuery({ queryKey: ['opt-outs'], queryFn: () => api<{ by_category: Record<string, number> }>('/messages-opt-outs') })
  return (
    <>
      <PageHeader
        title={t('messages.title')}
        actions={
          <Link to="/messages/new">
            <Button>{t('messages.compose')}</Button>
          </Link>
        }
      />
      <div className="grid gap-4 lg:grid-cols-4">
        <Card className="lg:col-span-3">
          <div className="mb-3 max-w-xs">
            <SelectInput label={t('common.status')} value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">{t('common.all')}</option>
              {['draft', 'released', 'completed', 'manual_handoff', 'cancelled'].map((s) => (
                <option key={s} value={s}>
                  {t(`messageStatus.${s}` as 'messageStatus.draft')}
                </option>
              ))}
            </SelectInput>
          </div>
          {q.isLoading ? (
            <Loading />
          ) : !q.data?.length ? (
            <Empty />
          ) : (
            <TableWrap>
              <table>
                <thead>
                  <tr>
                    <th>{t('common.name')}</th>
                    <th>{t('messages.type')}</th>
                    <th>{t('common.status')}</th>
                    <th>{t('common.date')}</th>
                  </tr>
                </thead>
                <tbody>
                  {q.data.map((m) => (
                    <tr key={m.id}>
                      <td>
                        <Link to={`/messages/${m.id}`} className={cx('font-medium hover:underline', m.is_emergency ? 'text-red-700' : 'text-brand')}>
                          {m.title}
                        </Link>
                        <div className="text-xs text-slate-500">{m.student_ids.length}</div>
                      </td>
                      <td>
                        {t(`messageTypes.${m.message_type}` as 'messageTypes.logistics')}
                        {m.is_negative && <Badge tone="amber" className="ms-1">✋</Badge>}
                      </td>
                      <td>
                        <Badge tone={m.status === 'completed' ? 'green' : m.status === 'released' ? 'blue' : 'slate'}>{t(`messageStatus.${m.status}` as 'messageStatus.draft')}</Badge>
                      </td>
                      <td className="text-xs">{fmtDateTime(m.released_at ?? m.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </Card>
        <Card title={t('messages.optOutsHeading')}>
          <ul className="space-y-1 text-sm">
            {Object.entries(optOuts.data?.by_category ?? {}).map(([k, n]) => (
              <li key={k} className="flex justify-between">
                <span>{t(`messageTypes.${k}` as 'messageTypes.logistics')}</span>
                <span>{n}</span>
              </li>
            ))}
            {!Object.keys(optOuts.data?.by_category ?? {}).length && <li className="text-slate-500">{t('common.none')}</li>}
          </ul>
        </Card>
      </div>
    </>
  )
}

export function ComposePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [type, setType] = useState<string>('logistics')
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => api<Template[]>('/message-templates') })
  const squads = useSquads()
  const editions = useEditions()
  const [squadId, setSquadId] = useState('')
  const [editionId, setEditionId] = useState('')
  const [title, setTitle] = useState('')
  const [students, setStudents] = useState<Student[]>([])
  const [search, setSearch] = useState('')
  const [vars, setVars] = useState<Record<string, string>>({})
  const found = useQuery({
    queryKey: ['student-search', search],
    queryFn: () => api<Page<Student>>(`/students?q=${encodeURIComponent(search)}&limit=8`),
    enabled: search.length >= 2,
  })
  const template = useMemo(() => (templates.data ?? []).find((x) => x.message_type === type && x.status === 'approved'), [templates.data, type])
  const manualVars = (template?.variables ?? []).filter((v) => !AUTO_VARS.has(v))
  const draft = useMutation({
    mutationFn: () =>
      api<{ message: Message; families_opted_out_of_category: number }>('/messages/draft', {
        method: 'POST',
        body: {
          message_type: type,
          title: title || t(`messageTypes.${type}` as 'messageTypes.logistics'),
          student_ids: students.map((s) => s.id),
          squad_id: squadId || null,
          edition_id: editionId || null,
          variables: vars,
        },
      }),
    onSuccess: (r) => navigate(`/messages/${r.message.id}`),
  })
  return (
    <>
      <PageHeader title={t('messages.compose')} />
      <Card>
        <form
          className="grid gap-4 lg:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault()
            draft.mutate()
          }}
        >
          <SelectInput label={t('messages.type')} value={type} onChange={(e) => setType(e.target.value)}>
            {TYPES.map((x) => (
              <option key={x} value={x}>
                {t(`messageTypes.${x}`)}
              </option>
            ))}
          </SelectInput>
          <TextInput label={t('common.name')} value={title} onChange={(e) => setTitle(e.target.value)} />
          {NEGATIVE.has(type) && (
            <div className="lg:col-span-2">
              <Alert tone="warn">{t('messages.negativeNotice')}</Alert>
            </div>
          )}
          {!template && <Alert tone="error">{t('messages.templateNotApproved')}</Alert>}
          <SelectInput label={t('messages.squad')} value={squadId} onChange={(e) => setSquadId(e.target.value)}>
            <option value="" />
            {(squads.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </SelectInput>
          <SelectInput label={t('messages.edition')} value={editionId} onChange={(e) => setEditionId(e.target.value)}>
            <option value="" />
            {(editions.data ?? []).map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </SelectInput>
          <div className="lg:col-span-2">
            <TextInput label={t('messages.recipients')} value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('students.searchPlaceholder')} />
            <div className="mt-2 flex flex-wrap gap-1">
              {students.map((s) => (
                <Badge key={s.id} tone="brand">
                  {s.display_name}
                  <button type="button" className="ms-1" aria-label={`${t('common.remove')} ${s.display_name}`} onClick={() => setStudents((x) => x.filter((y) => y.id !== s.id))}>
                    ✕
                  </button>
                </Badge>
              ))}
            </div>
            {found.data && search.length >= 2 && (
              <ul className="mt-1 rounded-lg border border-slate-200">
                {found.data.items.map((s) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      className="w-full px-3 py-1.5 text-start text-sm hover:bg-slate-50"
                      onClick={() => {
                        setStudents((x) => (x.some((y) => y.id === s.id) ? x : [...x, s]))
                        setSearch('')
                      }}
                    >
                      {s.display_name} · {t('common.year', { n: s.year_group })}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {manualVars.length > 0 && (
            <fieldset className="grid gap-3 lg:col-span-2 lg:grid-cols-2">
              <legend className="mb-1 text-sm font-semibold">{t('messages.variables')}</legend>
              {manualVars.map((v) => (
                <TextInput key={v} label={v.replace(/_/g, ' ')} value={vars[v] ?? ''} onChange={(e) => setVars((x) => ({ ...x, [v]: e.target.value }))} dir={v.endsWith('_ar') ? 'rtl' : undefined} />
              ))}
            </fieldset>
          )}
          {draft.error && <Alert tone="error">{errorMessage(draft.error)}</Alert>}
          <div className="lg:col-span-2">
            <Button type="submit" busy={draft.isPending} disabled={!template || (!students.length && !squadId)}>
              {t('messages.saveDraft')}
            </Button>
          </div>
        </form>
      </Card>
    </>
  )
}

interface Preview {
  is_negative: boolean
  template_approved: boolean
  template_version: number
  family_count: number
  samples: { guardian_name: string; children: string[]; language: string; channels: string[]; subject: string | null; body: string | null; would_be: string; reason: string | null; sent_this_week: number; weekly_cap: number }[]
  render_errors: string[]
  audience_status: Record<string, number>
  families_opted_out: number
  families_one_below_cap: number
  quiet_hours: { start: string; end: string }
}

interface Delivery {
  released_by_id: string | null
  released_at: string | null
  counts: Record<string, number>
  deliveries: { delivery_id: string; guardian_name: string; language: string; channel_planned: string; channel_used: string | null; status: string; error: string | null; sent_at: string | null; opened_at: string | null; subject: string | null; body: string | null; hold_until: string | null }[]
}

export function MessageDetailPage() {
  const { id = '' } = useParams()
  const { t } = useTranslation()
  const { can } = useAuth()
  const qc = useQueryClient()
  const [toast, show] = useToast()
  const msg = useQuery({ queryKey: ['message', id], queryFn: () => api<Message>(`/messages/${id}`) })
  const delivery = useQuery({ queryKey: ['delivery', id], queryFn: () => api<Delivery>(`/messages/${id}/delivery`), enabled: !!msg.data && msg.data.status !== 'draft' })
  const [preview, setPreview] = useState<Preview | null>(null)
  const [schedule, setSchedule] = useState('')
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['message', id] })
    void qc.invalidateQueries({ queryKey: ['delivery', id] })
  }
  const doPreview = useMutation({ mutationFn: () => api<Preview>(`/messages/${id}/preview`, { method: 'POST' }), onSuccess: (p) => { setPreview(p); refresh() }, onError: (e) => show(errorMessage(e), 'error') })
  const release = useMutation({
    mutationFn: () => api(`/messages/${id}/release`, { method: 'POST', body: { scheduled_for: schedule ? new Date(schedule).toISOString() : null } }),
    onSuccess: refresh,
    onError: (e) => show(errorMessage(e), 'error'),
  })
  const handoff = useMutation({ mutationFn: () => api(`/messages/${id}/handoff`, { method: 'POST' }), onSuccess: refresh, onError: (e) => show(errorMessage(e), 'error') })
  const cancel = useMutation({
    mutationFn: (reason: string) => api(`/messages/${id}/cancel`, { method: 'POST', body: { reason } }),
    onSuccess: refresh,
    onError: (e) => show(errorMessage(e), 'error'),
  })
  const markSent = useMutation({
    mutationFn: ({ did, note }: { did: string; note: string }) => api(`/messages/${id}/deliveries/${did}/sent-personally`, { method: 'POST', body: { channel_note: note } }),
    onSuccess: refresh,
  })
  if (msg.isLoading) return <Loading />
  if (msg.error) return <Alert tone="error">{errorMessage(msg.error)}</Alert>
  const m = msg.data!
  const isDraft = m.status === 'draft'
  return (
    <>
      {toast}
      <PageHeader
        title={m.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            {t(`messageTypes.${m.message_type}` as 'messageTypes.logistics')}
            <Badge>{t(`messageStatus.${m.status}` as 'messageStatus.draft')}</Badge>
            {m.released_at && <span>{t('messages.releasedBy', { date: fmtDateTime(m.released_at) })}</span>}
            {m.is_emergency && <Badge tone="red">{t('nav.emergency')}</Badge>}
          </span>
        }
        actions={
          (isDraft || m.status === 'released') && (
            <Button
              variant="ghost"
              onClick={() => {
                const r = window.prompt(t('messages.cancelReason'))
                if (r && r.length >= 3) cancel.mutate(r)
              }}
            >
              {t('messages.cancelMessage')}
            </Button>
          )
        }
      />
      {m.is_negative && <Alert tone="warn">{t('messages.negativeNotice')}</Alert>}
      {isDraft && (
        <Card title={t('common.preview')} className="mt-4">
          <Button variant="secondary" onClick={() => doPreview.mutate()} busy={doPreview.isPending}>
            {t('common.preview')}
          </Button>
          {preview && <PreviewPanel p={preview} />}
          {preview && !preview.render_errors.length && (
            <div className="mt-4 rounded-lg border border-slate-200 p-3">
              {m.is_negative ? (
                <Button onClick={() => handoff.mutate()} busy={handoff.isPending}>
                  {t('messages.handoff')}
                </Button>
              ) : can('release_messages') ? (
                <div className="flex flex-wrap items-end gap-3">
                  <div className="w-64">
                    <TextInput label={t('messages.schedule')} type="datetime-local" value={schedule} onChange={(e) => setSchedule(e.target.value)} />
                  </div>
                  <Button
                    busy={release.isPending}
                    onClick={() => {
                      if (window.confirm(t('messages.releaseConfirm', { n: preview.family_count }))) release.mutate()
                    }}
                  >
                    {t('messages.release')}
                  </Button>
                </div>
              ) : (
                <p className="text-sm text-slate-600">{t('messages.previewFirst')}</p>
              )}
            </div>
          )}
        </Card>
      )}
      {delivery.data && (
        <Card
          title={t('messages.delivery')}
          className="mt-4"
          actions={
            <Button variant="secondary" size="sm" onClick={() => void downloadBlob(`/messages/${id}/delivery.csv`, 'delivery.csv')}>
              {t('messages.exportCsv')}
            </Button>
          }
        >
          <div className="mb-3 flex flex-wrap gap-2">
            {Object.entries(delivery.data.counts).map(([k, n]) => (
              <Badge key={k} tone={k === 'sent' || k === 'delivered' || k === 'read' ? 'green' : k.startsWith('blocked') || k === 'failed' ? 'red' : 'amber'}>
                {t(`deliveryStatus.${k}` as 'deliveryStatus.sent')}: {n}
              </Badge>
            ))}
          </div>
          <TableWrap>
            <table>
              <thead>
                <tr>
                  <th>{t('students.guardians')}</th>
                  <th>{t('messages.channel')}</th>
                  <th>{t('common.status')}</th>
                  <th>{t('messages.opened')}</th>
                </tr>
              </thead>
              <tbody>
                {delivery.data.deliveries.map((d) => (
                  <tr key={d.delivery_id}>
                    <td>
                      {d.guardian_name} <span className="text-xs text-slate-500">({d.language})</span>
                      <details className="text-xs">
                        <summary className="cursor-pointer text-slate-500">{d.subject}</summary>
                        <pre className="whitespace-pre-wrap font-sans wrap-anywhere" dir={d.language === 'ar' ? 'rtl' : 'ltr'}>
                          {d.body}
                        </pre>
                      </details>
                    </td>
                    <td>{d.channel_used ? t(`channels.${d.channel_used}` as 'channels.email') : '—'}</td>
                    <td>
                      {t(`deliveryStatus.${d.status}` as 'deliveryStatus.sent')}
                      {d.error && <div className="text-xs text-slate-500">{d.error}</div>}
                      {m.status === 'manual_handoff' && d.status === 'pending' && (
                        <Button
                          size="sm"
                          variant="secondary"
                          className="mt-1"
                          onClick={() => {
                            const note = window.prompt(t('messages.sentNote'))
                            if (note) markSent.mutate({ did: d.delivery_id, note })
                          }}
                        >
                          {t('messages.markSent')}
                        </Button>
                      )}
                    </td>
                    <td className="text-xs">{fmtDateTime(d.opened_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Card>
      )}
    </>
  )
}

export function PreviewPanel({ p }: { p: Preview }) {
  const { t } = useTranslation()
  return (
    <div className="mt-4 space-y-4">
      {p.render_errors.length > 0 && (
        <Alert tone="error" title={t('messages.renderErrors')}>
          {p.render_errors.join('; ')}
        </Alert>
      )}
      {!p.template_approved && <Alert tone="error">{t('messages.templateNotApproved')}</Alert>}
      <div className="flex flex-wrap gap-2 text-sm">
        <Badge tone="brand">{t('messages.families', { n: p.family_count })}</Badge>
        <Badge tone={p.families_opted_out ? 'amber' : 'slate'}>{t('messages.optedOut', { n: p.families_opted_out })}</Badge>
        <Badge tone={p.families_one_below_cap ? 'amber' : 'slate'}>{t('messages.nearCap', { n: p.families_one_below_cap })}</Badge>
        <Badge>{t('messages.quietHours', { start: p.quiet_hours.start.slice(0, 5), end: p.quiet_hours.end.slice(0, 5) })}</Badge>
        {Object.entries(p.audience_status).map(([k, n]) => (
          <Badge key={k}>
            {t(`deliveryStatus.${k}` as 'deliveryStatus.sent')}: {n}
          </Badge>
        ))}
      </div>
      <h3>{t('messages.previewTitle')}</h3>
      <div className="grid gap-3 lg:grid-cols-3">
        {p.samples.map((s, i) => (
          <article key={i} className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm" dir={s.language === 'ar' ? 'rtl' : 'ltr'} lang={s.language}>
            <div className="mb-1 text-xs text-slate-500">
              {s.guardian_name} · {s.children.join(', ')} · {s.channels.map((c) => t(`channels.${c}` as 'channels.email')).join(' → ')}
            </div>
            <div className="font-semibold">{s.subject}</div>
            <pre className="mt-1 whitespace-pre-wrap font-sans wrap-anywhere">{s.body}</pre>
            <div className="mt-2 flex flex-wrap gap-1 text-xs">
              <Badge tone={s.would_be === 'pending' ? 'green' : 'amber'}>{t('messages.wouldBe', { status: t(`deliveryStatus.${s.would_be}` as 'deliveryStatus.sent') })}</Badge>
              <Badge>{t('messages.weekCount', { n: s.sent_this_week, cap: s.weekly_cap })}</Badge>
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}

export function EmergencyPage() {
  const { t } = useTranslation()
  const squads = useSquads()
  const navigate = useNavigate()
  const [form, setForm] = useState({ reason: '', title: '', notice_en: '', notice_ar: '', whole_school: false })
  const [selected, setSelected] = useState<string[]>([])
  const [preview, setPreview] = useState<Preview | null>(null)
  const body = (confirm: boolean) => ({ ...form, squad_ids: selected, confirm })
  const run = useMutation({
    mutationFn: (confirm: boolean) => api<{ preview: Preview; released: boolean; message?: Message }>('/messages/emergency', { method: 'POST', body: body(confirm) }),
    onSuccess: (r) => {
      setPreview(r.preview)
      if (r.released && r.message) navigate(`/messages/${r.message.id}`)
    },
  })
  return (
    <>
      <PageHeader title={t('emergency.title')} />
      <div className="rounded-xl border-2 border-red-600 bg-red-50 p-4">
        <p className="mb-4 font-medium text-red-900">⚠ {t('emergency.warning')}</p>
        <div className="grid gap-4 lg:grid-cols-2">
          <TextInput label={t('emergency.reason')} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} required minLength={10} />
          <TextInput label={t('common.name')} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} required />
          <TextArea label={t('emergency.noticeEn')} value={form.notice_en} onChange={(e) => setForm({ ...form, notice_en: e.target.value })} lang="en" dir="ltr" />
          <TextArea label={t('emergency.noticeAr')} value={form.notice_ar} onChange={(e) => setForm({ ...form, notice_ar: e.target.value })} lang="ar" dir="rtl" />
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.whole_school} onChange={(e) => setForm({ ...form, whole_school: e.target.checked })} /> {t('emergency.wholeSchool')}
          </label>
          {!form.whole_school && (
            <fieldset>
              <legend className="text-sm font-medium">{t('emergency.squads')}</legend>
              <div className="mt-1 flex flex-wrap gap-2">
                {(squads.data ?? []).map((s) => (
                  <label key={s.id} className="flex items-center gap-1 text-sm">
                    <input type="checkbox" checked={selected.includes(s.id)} onChange={(e) => setSelected((x) => (e.target.checked ? [...x, s.id] : x.filter((y) => y !== s.id)))} /> {s.name}
                  </label>
                ))}
              </div>
            </fieldset>
          )}
        </div>
        {run.error && <div className="mt-3"><Alert tone="error">{errorMessage(run.error)}</Alert></div>}
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => run.mutate(false)} busy={run.isPending}>
            {t('emergency.previewFirst')}
          </Button>
          <Button variant="danger" disabled={!preview} onClick={() => window.confirm(t('emergency.warning')) && run.mutate(true)}>
            {t('emergency.send')}
          </Button>
        </div>
      </div>
      {preview && <PreviewPanel p={preview} />}
    </>
  )
}

export function TemplatesPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['templates-all'], queryFn: () => api<Template[]>('/message-templates?include_retired=true') })
  const [editing, setEditing] = useState<Template | null>(null)
  const [toast, show] = useToast()
  const save = useMutation({
    mutationFn: (tpl: Template) =>
      api('/message-templates', { method: 'POST', body: { key: tpl.key, message_type: tpl.message_type, subject_en: tpl.subject_en, body_en: tpl.body_en, subject_ar: tpl.subject_ar, body_ar: tpl.body_ar, whatsapp_template_name: tpl.whatsapp_template_name } }),
    onSuccess: () => {
      setEditing(null)
      show(t('common.saved'))
      void qc.invalidateQueries({ queryKey: ['templates-all'] })
    },
    onError: (e) => show(errorMessage(e), 'error'),
  })
  const approve = useMutation({
    mutationFn: (id: string) => api(`/message-templates/${id}/approve`, { method: 'POST' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['templates-all'] }),
    onError: (e) => show(errorMessage(e), 'error'),
  })
  if (q.isLoading) return <Loading />
  return (
    <>
      {toast}
      <PageHeader title={t('templates.title')} subtitle={t('templates.variablesHint')} />
      {editing && (
        <Card title={`${editing.key} — ${t('templates.newVersion')}`} className="mb-4">
          <div className="grid gap-3 lg:grid-cols-2">
            <TextInput label={`${t('templates.subject')} (EN)`} value={editing.subject_en} onChange={(e) => setEditing({ ...editing, subject_en: e.target.value })} dir="ltr" />
            <TextInput label={`${t('templates.subject')} (AR)`} value={editing.subject_ar} onChange={(e) => setEditing({ ...editing, subject_ar: e.target.value })} dir="rtl" />
            <TextArea label={`${t('templates.body')} (EN)`} rows={8} value={editing.body_en} onChange={(e) => setEditing({ ...editing, body_en: e.target.value })} dir="ltr" />
            <TextArea label={`${t('templates.body')} (AR)`} rows={8} value={editing.body_ar} onChange={(e) => setEditing({ ...editing, body_ar: e.target.value })} dir="rtl" />
          </div>
          <div className="mt-3 flex gap-2">
            <Button onClick={() => save.mutate(editing)} busy={save.isPending}>
              {t('common.save')}
            </Button>
            <Button variant="ghost" onClick={() => setEditing(null)}>
              {t('common.cancel')}
            </Button>
          </div>
        </Card>
      )}
      <Card>
        <TableWrap>
          <table>
            <thead>
              <tr>
                <th>{t('templates.key')}</th>
                <th>{t('messages.type')}</th>
                <th>{t('templates.version')}</th>
                <th>{t('common.status')}</th>
                <th>{t('templates.whatsapp')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(q.data ?? []).map((tpl) => (
                <tr key={tpl.id} className={tpl.status === 'retired' ? 'opacity-60' : ''}>
                  <td className="font-mono text-xs">{tpl.key}</td>
                  <td>{t(`messageTypes.${tpl.message_type}` as 'messageTypes.logistics')}</td>
                  <td>v{tpl.version}</td>
                  <td>
                    <Badge tone={tpl.status === 'approved' ? 'green' : tpl.status === 'draft' ? 'amber' : 'slate'}>{t(`templates.${tpl.status}`)}</Badge>
                  </td>
                  <td className="text-xs">
                    {tpl.whatsapp_template_name ?? '—'} <span className="text-slate-500">{tpl.whatsapp_status}</span>
                  </td>
                  <td className="space-x-2 rtl:space-x-reverse">
                    {tpl.status === 'draft' && (
                      <Button size="sm" onClick={() => approve.mutate(tpl.id)}>
                        {t('common.approve')}
                      </Button>
                    )}
                    {tpl.status === 'approved' && (
                      <Button size="sm" variant="secondary" onClick={() => setEditing(tpl)}>
                        {t('templates.newVersion')}
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </Card>
    </>
  )
}
