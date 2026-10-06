import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { Alert, Badge, Button, Card, ClaimBadge, Empty, Loading, PageHeader, SelectInput, TableWrap, TextInput, useToast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { fmtDateTime } from '@/lib/format'
import { useSquads } from '@/lib/queries'
import type { Page, Student } from '@/lib/types'

const REPORTS: { type: string; cap: string; audience: string; needs?: 'student' | 'squad' }[] = [
  { type: 'student_profile', cap: 'generate_reports', audience: 'parent, student, coach', needs: 'student' },
  { type: 'squad_readiness', cap: 'run_readiness', audience: 'coach', needs: 'squad' },
  { type: 'season_review', cap: 'view_school_analytics', audience: 'leader' },
  { type: 'cohort_coverage', cap: 'view_school_analytics', audience: 'leader, admin' },
  { type: 'inspection_evidence', cap: 'export_inspection', audience: 'leader' },
  { type: 'plateau_stretch', cap: 'run_readiness', audience: 'coach, admin' },
  { type: 'kit_utilisation', cap: 'manage_inventory', audience: 'admin' },
]

interface Job {
  id: string
  report_type: string
  format: string
  status: string
  filename: string | null
  error: string | null
  created_at: string
  download_url: string | null
}

export default function ReportsPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const qc = useQueryClient()
  const squads = useSquads()
  const [toast, show] = useToast()
  const [format, setFormat] = useState('pdf')
  const [squad, setSquad] = useState('')
  const [studentQ, setStudentQ] = useState('')
  const [student, setStudent] = useState<Student | null>(null)
  const found = useQuery({
    queryKey: ['student-search', studentQ],
    queryFn: () => api<Page<Student>>(`/students?q=${encodeURIComponent(studentQ)}&limit=6`),
    enabled: studentQ.length >= 2,
  })
  const jobs = useQuery({
    queryKey: ['report-jobs'],
    queryFn: () => api<Job[]>('/reports/jobs'),
    refetchInterval: (q) => ((q.state.data ?? []).some((j) => j.status === 'queued' || j.status === 'running') ? 2000 : false),
  })
  const generate = useMutation({
    mutationFn: ({ type, params }: { type: string; params: Record<string, string> }) => api(`/reports/${type}/generate`, { method: 'POST', body: { format, params } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['report-jobs'] }),
    onError: (e) => show(errorMessage(e), 'error'),
  })
  const params = (needs?: 'student' | 'squad'): Record<string, string> | null => {
    if (needs === 'student') return student ? { student_id: student.id } : null
    if (needs === 'squad') return squad ? { squad_id: squad } : null
    return {}
  }
  const openPrint = async (type: string, p: Record<string, string>) => {
    const qs = new URLSearchParams({ ...p, as_html: 'true' })
    const res = await api<Response>(`/reports/${type}/preview?${qs}`, { raw: true })
    const url = URL.createObjectURL(new Blob([await res.text()], { type: 'text/html' }))
    window.open(url, '_blank', 'noopener')
  }
  return (
    <>
      {toast}
      <PageHeader title={t('reports.title')} />
      <Card className="mb-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <SelectInput label={t('reports.format')} value={format} onChange={(e) => setFormat(e.target.value)}>
            <option value="pdf">PDF</option>
            <option value="xlsx">Excel</option>
            <option value="csv">CSV</option>
            <option value="html">HTML</option>
          </SelectInput>
          <SelectInput label={t('reports.squad')} value={squad} onChange={(e) => setSquad(e.target.value)}>
            <option value="" />
            {(squads.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </SelectInput>
          <div>
            <TextInput label={t('reports.student')} value={student ? student.display_name : studentQ} onChange={(e) => { setStudent(null); setStudentQ(e.target.value) }} />
            {!student && found.data && studentQ.length >= 2 && (
              <ul className="mt-1 rounded-lg border border-slate-200">
                {found.data.items.map((s) => (
                  <li key={s.id}>
                    <button className="w-full px-3 py-1 text-start text-sm hover:bg-slate-50" onClick={() => setStudent(s)}>
                      {s.display_name}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </Card>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {REPORTS.filter((r) => can(r.cap)).map((r) => {
          const p = params(r.needs)
          return (
            <Card key={r.type} title={t(`reports.types.${r.type}` as 'reports.types.season_review')}>
              <p className="text-sm text-slate-600">{t(`reports.descriptions.${r.type}` as 'reports.descriptions.season_review')}</p>
              <p className="mt-1 text-xs text-slate-500">{t('reports.audience', { who: r.audience })}</p>
              {r.needs && !p && <p className="mt-2 text-xs text-amber-800">{r.needs === 'student' ? t('reports.student') : t('reports.squad')} ↑</p>}
              <div className="mt-3 flex gap-2">
                <Button size="sm" disabled={!p} busy={generate.isPending} onClick={() => p && generate.mutate({ type: r.type, params: p })}>
                  {t('reports.generate')}
                </Button>
                <Button size="sm" variant="secondary" disabled={!p} onClick={() => p && void openPrint(r.type, p)}>
                  {t('reports.previewHtml')}
                </Button>
              </div>
            </Card>
          )
        })}
      </div>
      <Card title={t('reports.jobs')} className="mt-4">
        {jobs.isLoading ? (
          <Loading />
        ) : !jobs.data?.length ? (
          <Empty />
        ) : (
          <TableWrap>
            <table>
              <thead>
                <tr>
                  <th>{t('common.name')}</th>
                  <th>{t('reports.format')}</th>
                  <th>{t('common.status')}</th>
                  <th>{t('common.date')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {jobs.data.map((j) => (
                  <tr key={j.id}>
                    <td>{t(`reports.types.${j.report_type}` as 'reports.types.season_review')}</td>
                    <td className="uppercase">{j.format}</td>
                    <td>
                      <Badge tone={j.status === 'succeeded' ? 'green' : j.status === 'failed' ? 'red' : 'amber'}>{j.status}</Badge>
                      {j.error && <div className="text-xs text-red-700">{j.error}</div>}
                    </td>
                    <td className="text-xs">{fmtDateTime(j.created_at)}</td>
                    <td>
                      {j.download_url && (
                        <a href={j.download_url} className="text-sm font-medium text-brand underline">
                          {t('common.download')}
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
    </>
  )
}

interface FlagRow {
  id: string
  student_id: string
  student_name: string
  year_group: number
  kind: string
  rule: string
  explanation: string
  raised_at: string
}

export function FlagsPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [kind, setKind] = useState('')
  const q = useQuery({ queryKey: ['flags', kind], queryFn: () => api<FlagRow[]>(`/flags${kind ? `?kind=${kind}` : ''}`) })
  const refresh = useMutation({ mutationFn: () => api('/flags/refresh', { method: 'POST' }), onSuccess: () => void qc.invalidateQueries({ queryKey: ['flags'] }) })
  const resolve = useMutation({ mutationFn: (id: string) => api(`/flags/${id}/resolve`, { method: 'POST', body: {} }), onSuccess: () => void qc.invalidateQueries({ queryKey: ['flags'] }) })
  return (
    <>
      <PageHeader
        title={t('flags.title')}
        actions={
          <Button variant="secondary" busy={refresh.isPending} onClick={() => refresh.mutate()}>
            {t('flags.refresh')}
          </Button>
        }
      />
      <div className="mb-3 max-w-xs">
        <SelectInput label={t('students.flag')} value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">{t('common.all')}</option>
          {(['plateau', 'stretch', 'attendance'] as const).map((k) => (
            <option key={k} value={k}>
              {t(`flagKinds.${k}`)}
            </option>
          ))}
        </SelectInput>
      </div>
      {q.isLoading ? (
        <Loading />
      ) : !q.data?.length ? (
        <Empty>{t('flags.none')}</Empty>
      ) : (
        <div className="space-y-3">
          {q.data.map((f) => (
            <div key={f.id} className="card claim-inferred flex flex-wrap items-start gap-3">
              <Badge tone="amber">{t(`flagKinds.${f.kind}` as 'flagKinds.plateau')}</Badge>
              <div className="min-w-[200px] flex-1">
                <Link to={`/students/${f.student_id}`} className="font-medium text-brand hover:underline">
                  {f.student_name}
                </Link>{' '}
                <span className="text-sm text-slate-500">· {t('common.year', { n: f.year_group })}</span>
                <p className="text-sm">{f.explanation}</p>
                <p className="text-xs text-slate-500">
                  {t('students.rule')}: {f.rule} · {t('flags.raised')} {fmtDateTime(f.raised_at)}
                </p>
              </div>
              <ClaimBadge type="inferred" />
              <Button size="sm" variant="ghost" onClick={() => resolve.mutate(f.id)}>
                {t('flags.resolve')}
              </Button>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

interface InsightRow {
  id: string
  student_id: string
  student_name: string
  period: string
  rule: string
  claim_type: 'measured' | 'inferred'
  text_en: string
  text_ar: string
  generated_en: string
  facts: Record<string, unknown>
  status: string
}

export function InsightsPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const qc = useQueryClient()
  const [toast, show] = useToast()
  const q = useQuery({ queryKey: ['insights'], queryFn: () => api<InsightRow[]>('/insights?status=draft') })
  const [edits, setEdits] = useState<Record<string, { en?: string; ar?: string }>>({})
  const gen = useMutation({ mutationFn: () => api('/insights/generate', { method: 'POST', body: {} }), onSuccess: () => void qc.invalidateQueries({ queryKey: ['insights'] }) })
  const review = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: 'approved' | 'rejected' }) =>
      api(`/insights/${id}/review`, { method: 'POST', body: { decision, edited_text_en: edits[id]?.en, edited_text_ar: edits[id]?.ar } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['insights'] }),
  })
  const progress = useMutation({
    mutationFn: () => api<{ message_id: string }>('/messages/progress-reports', { method: 'POST' }),
    onSuccess: () => show(t('common.saved')),
    onError: (e) => show(errorMessage(e), 'error'),
  })
  return (
    <>
      {toast}
      <PageHeader
        title={t('insights.title')}
        subtitle={t('insights.hint')}
        actions={
          <>
            <Button variant="secondary" busy={gen.isPending} onClick={() => gen.mutate()}>
              {t('insights.generate')}
            </Button>
            {can('release_messages') && (
              <Button variant="secondary" busy={progress.isPending} onClick={() => progress.mutate()}>
                {t('insights.draftProgress')}
              </Button>
            )}
          </>
        }
      />
      {q.isLoading ? (
        <Loading />
      ) : !q.data?.length ? (
        <Empty>{t('insights.none')}</Empty>
      ) : (
        <div className="space-y-3">
          {q.data.map((i) => (
            <Card key={i.id} inferred={i.claim_type === 'inferred'} title={<Link to={`/students/${i.student_id}`} className="hover:underline">{i.student_name}</Link>} actions={<><Badge>{i.period}</Badge><ClaimBadge type={i.claim_type} /></>}>
              <div className="grid gap-3 lg:grid-cols-2">
                <textarea
                  aria-label="English"
                  className="input min-h-[80px]"
                  dir="ltr"
                  lang="en"
                  defaultValue={i.text_en}
                  onChange={(e) => setEdits((x) => ({ ...x, [i.id]: { ...x[i.id], en: e.target.value } }))}
                />
                <textarea
                  aria-label="العربية"
                  className="input min-h-[80px]"
                  dir="rtl"
                  lang="ar"
                  defaultValue={i.text_ar}
                  onChange={(e) => setEdits((x) => ({ ...x, [i.id]: { ...x[i.id], ar: e.target.value } }))}
                />
              </div>
              <details className="mt-2 text-xs">
                <summary className="cursor-pointer text-slate-600">
                  {t('insights.facts')} · {i.rule}
                </summary>
                <pre className="mt-1 overflow-auto rounded-sm bg-slate-50 p-2" dir="ltr">
                  {JSON.stringify(i.facts, null, 2)}
                </pre>
              </details>
              <div className="mt-3 flex gap-2">
                <Button size="sm" onClick={() => review.mutate({ id: i.id, decision: 'approved' })}>
                  {t('insights.approve')}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => review.mutate({ id: i.id, decision: 'rejected' })}>
                  {t('insights.reject')}
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      {review.error && <Alert tone="error">{errorMessage(review.error)}</Alert>}
    </>
  )
}
