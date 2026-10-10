import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  CalendarClock,
  ClipboardCheck,
  Eye,
  FileText,
  type LucideIcon,
  Medal,
  MessageSquare,
  NotebookPen,
  PartyPopper,
  PenLine,
  Send,
  ShieldCheck,
  Siren,
  Star,
  Users,
  X,
} from 'lucide-react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { Alert, Badge, Button, ButtonLink, Card, ChoiceCard, EmptyState, InfoNote, Loading, PageHeader, SelectInput, Step, TableWrap, Tabs, TextArea, TextInput, cx, useToast } from '@/components/ui'
import { api, downloadBlob, errorMessage } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { fmtDateTime } from '@/lib/format'
import { useEditions, useSquads } from '@/lib/queries'
import type { Message, Page, Student, Template } from '@/lib/types'

const TYPES = ['selection_notice', 'logistics', 'consent_request', 'result_notification', 'progress_report', 'celebration', 'attendance_concern', 'non_selection', 'behaviour_note'] as const
const NEGATIVE = new Set(['attendance_concern', 'non_selection', 'behaviour_note'])
// Filled in automatically from the student, guardian, edition and squad.
const AUTO_VARS = new Set(['guardian_name', 'child_name', 'child_names', 'school_name', 'portal_link', 'opt_out_link', 'edition_name', 'competition_name', 'event_date', 'venue', 'squad_name', 'result_summary', 'team_members', 'progress_summary'])

const STATUS_TABS = ['', 'draft', 'released', 'completed', 'manual_handoff', 'cancelled'] as const

export default function MessagesPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const [params, setParams] = useSearchParams()
  const status = (params.get('status') ?? '') as (typeof STATUS_TABS)[number]
  const q = useQuery({ queryKey: ['messages', status], queryFn: () => api<Message[]>(`/messages${status ? `?status=${status}` : ''}`) })
  const optOuts = useQuery({ queryKey: ['opt-outs'], queryFn: () => api<{ by_category: Record<string, number> }>('/messages-opt-outs') })
  return (
    <>
      <PageHeader
        icon={MessageSquare}
        title={t('messages.title')}
        subtitle={can('release_messages') ? t('messages.introApprover') : t('messages.introTeacher')}
        actions={
          <ButtonLink to="/messages/new" icon={PenLine}>
            {t('messages.compose')}
          </ButtonLink>
        }
      />
      <div className="grid gap-4 lg:grid-cols-4">
        <div className="lg:col-span-3">
          <Tabs
            value={status}
            onChange={(v) => setParams(v ? { status: v } : {})}
            tabs={STATUS_TABS.map((s) => ({ id: s, label: s ? t(`messages.tabs.${s}`) : t('common.all') }))}
          />
          {q.isLoading ? (
            <Loading />
          ) : !q.data?.length ? (
            <EmptyState icon={MessageSquare} title={t('messages.none')} action={<ButtonLink to="/messages/new" variant="secondary" icon={PenLine}>{t('messages.compose')}</ButtonLink>}>
              {t('messages.noneHint')}
            </EmptyState>
          ) : (
            <ul className="card divide-y divide-slate-100 p-0">
              {q.data.map((m) => (
                <li key={m.id}>
                  <Link to={`/messages/${m.id}`} className="flex flex-wrap items-center gap-3 px-5 py-3.5 hover:bg-slate-50">
                    <span className="min-w-0 flex-1">
                      <span className={cx('block font-medium', m.is_emergency ? 'text-red-700' : 'text-slate-900')}>{m.title}</span>
                      <span className="block text-sm text-slate-500">
                        {t(`messageTypes.${m.message_type}` as 'messageTypes.logistics')} · {fmtDateTime(m.released_at ?? m.created_at)}
                        {m.student_ids.length > 0 && ` · ${t('home.members', { n: m.student_ids.length })}`}
                      </span>
                    </span>
                    {m.is_negative && <Badge tone="amber">{t('messageStatus.manual_handoff')}</Badge>}
                    <Badge tone={m.status === 'completed' ? 'green' : m.status === 'released' ? 'blue' : m.status === 'draft' ? 'amber' : 'slate'}>
                      {t(`messageStatus.${m.status}` as 'messageStatus.draft')}
                    </Badge>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
        <Card title={t('messages.optOutsHeading')} description={t('messages.optOutsHint')} className="self-start">
          <ul className="space-y-1.5 text-sm">
            {Object.entries(optOuts.data?.by_category ?? {}).map(([k, n]) => (
              <li key={k} className="flex justify-between gap-2">
                <span>{t(`messageTypes.${k}` as 'messageTypes.logistics')}</span>
                <span className="font-medium tabular-nums">{n}</span>
              </li>
            ))}
            {!Object.keys(optOuts.data?.by_category ?? {}).length && <li className="text-slate-500">{t('common.none')}</li>}
          </ul>
        </Card>
      </div>
    </>
  )
}

const TYPE_ICONS: Record<string, LucideIcon> = {
  selection_notice: Star,
  logistics: CalendarClock,
  consent_request: ShieldCheck,
  result_notification: Medal,
  progress_report: NotebookPen,
  celebration: PartyPopper,
  attendance_concern: ClipboardCheck,
  non_selection: Users,
  behaviour_note: FileText,
}

export function ComposePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [type, setType] = useState<string>('')
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
  const hasAudience = students.length > 0 || !!squadId
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
      <PageHeader icon={PenLine} title={t('messages.compose')} subtitle={t('messages.composeIntro')} back={{ to: '/messages', label: t('messages.title') }} />
      <form
        className="max-w-3xl space-y-3"
        onSubmit={(e) => {
          e.preventDefault()
          draft.mutate()
        }}
      >
        <Step n={1} title={type ? t('messages.typeChosen', { type: t(`messageTypes.${type}` as 'messageTypes.logistics') }) : t('messages.whatKind')} done={!!type}>
          {type ? (
            <Button size="sm" variant="secondary" onClick={() => setType('')}>
              {t('common.change')}
            </Button>
          ) : (
            <div role="radiogroup" aria-label={t('messages.type')} className="grid gap-2 sm:grid-cols-2">
              {TYPES.map((x) => (
                <ChoiceCard key={x} selected={false} onSelect={() => setType(x)} icon={TYPE_ICONS[x]} title={t(`messageTypes.${x}`)} meta={t(`messageTypeHelp.${x}`)} />
              ))}
            </div>
          )}
          {type && NEGATIVE.has(type) && (
            <div className="mt-3">
              <Alert tone="warn">{t('messages.negativeNotice')}</Alert>
            </div>
          )}
          {type && templates.isSuccess && !template && (
            <div className="mt-3">
              <Alert tone="error">{t('messages.templateNotApproved')}</Alert>
            </div>
          )}
        </Step>
        <Step n={2} title={t('messages.whoFor')} hint={t('messages.whoForHint')} active={!!type} done={hasAudience}>
          <div className="grid gap-4 sm:grid-cols-2">
            <SelectInput label={t('messages.squad')} value={squadId} onChange={(e) => setSquadId(e.target.value)}>
              <option value="">{t('messages.noSquad')}</option>
              {(squads.data ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </SelectInput>
            <SelectInput label={t('messages.edition')} hint={t('messages.editionHint')} value={editionId} onChange={(e) => setEditionId(e.target.value)}>
              <option value="">{t('messages.noEdition')}</option>
              {(editions.data ?? []).map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </SelectInput>
          </div>
          <div className="mt-4">
            <TextInput label={t('messages.recipients')} value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('students.searchPlaceholder')} />
            {students.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {students.map((s) => (
                  <Badge key={s.id} tone="brand">
                    {s.display_name}
                    <button type="button" className="ms-0.5 rounded-full p-0.5 hover:bg-brand/10" aria-label={`${t('common.remove')} ${s.display_name}`} onClick={() => setStudents((x) => x.filter((y) => y.id !== s.id))}>
                      <X aria-hidden className="h-3 w-3" />
                    </button>
                  </Badge>
                ))}
              </div>
            )}
            {found.data && search.length >= 2 && (
              <ul className="mt-1 divide-y divide-slate-100 rounded-xl border border-slate-200">
                {found.data.items.map((s) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      className="w-full px-3 py-2 text-start text-sm hover:bg-slate-50"
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
        </Step>
        <Step n={3} title={t('messages.variables')} hint={manualVars.length ? t('messages.variablesHint') : t('messages.noVariables')} active={!!type && hasAudience}>
          <div className="grid gap-4 sm:grid-cols-2">
            {manualVars.map((v) => (
              <TextInput
                key={v}
                label={t(`messageVars.${v}` as 'messageVars.meet_time', { defaultValue: v.replace(/_/g, ' ') })}
                value={vars[v] ?? ''}
                onChange={(e) => setVars((x) => ({ ...x, [v]: e.target.value }))}
                dir={v.endsWith('_ar') ? 'rtl' : undefined}
              />
            ))}
            <TextInput label={t('messages.titleLabel')} hint={t('messages.titleHint')} value={title} onChange={(e) => setTitle(e.target.value)} placeholder={type ? t(`messageTypes.${type}` as 'messageTypes.logistics') : ''} />
          </div>
          {draft.error && (
            <div className="mt-3">
              <Alert tone="error">{errorMessage(draft.error)}</Alert>
            </div>
          )}
          <Button type="submit" size="lg" className="mt-5 w-full" busy={draft.isPending} disabled={!template || !hasAudience}>
            {t('messages.saveDraft')}
          </Button>
          <p className="mt-2 text-center text-sm text-slate-500">{t('messages.saveDraftHint')}</p>
        </Step>
      </form>
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
        back={{ to: '/messages', label: t('messages.title') }}
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
      {m.is_negative && (
        <div className="mb-4">
          <Alert tone="warn">{t('messages.negativeNotice')}</Alert>
        </div>
      )}
      {isDraft && (
        <div className="max-w-5xl space-y-3">
          <Step n={1} title={t('messages.checkTitle')} hint={t('messages.checkHint')} done={!!preview && !preview.render_errors.length}>
            <Button variant={preview ? 'secondary' : 'primary'} icon={Eye} onClick={() => doPreview.mutate()} busy={doPreview.isPending}>
              {preview ? t('messages.previewAgain') : t('messages.showPreview')}
            </Button>
            {preview && <PreviewPanel p={preview} />}
          </Step>
          <Step
            n={2}
            title={m.is_negative ? t('messages.handoffTitle') : t('messages.sendTitle')}
            hint={m.is_negative ? t('messages.handoffHint') : can('release_messages') ? t('messages.sendHint') : t('messages.waitForApprover')}
            active={!!preview && !preview.render_errors.length}
          >
            {preview &&
              (m.is_negative ? (
                <Button onClick={() => handoff.mutate()} busy={handoff.isPending}>
                  {t('messages.handoff')}
                </Button>
              ) : can('release_messages') ? (
                <div className="flex flex-wrap items-end gap-3">
                  <div className="w-64 max-w-full">
                    <TextInput label={t('messages.schedule')} hint={t('messages.scheduleHint')} type="datetime-local" value={schedule} onChange={(e) => setSchedule(e.target.value)} />
                  </div>
                  <Button
                    icon={Send}
                    busy={release.isPending}
                    onClick={() => {
                      if (window.confirm(t('messages.releaseConfirm', { n: preview.family_count }))) release.mutate()
                    }}
                  >
                    {t('messages.release')}
                  </Button>
                </div>
              ) : null)}
          </Step>
        </div>
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
      <PageHeader icon={Siren} title={t('emergency.title')} subtitle={t('emergency.intro')} />
      <div className="card border-red-200">
        <div className="mb-5">
          <Alert tone="error">{t('emergency.warning')}</Alert>
        </div>
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
      <PageHeader icon={FileText} title={t('templates.title')} subtitle={t('templates.intro')} />
      <div className="mb-4">
        <InfoNote>{t('templates.variablesHint')}</InfoNote>
      </div>
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
