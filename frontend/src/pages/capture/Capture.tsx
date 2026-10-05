// Phone-first capture screens. All of them work offline: data they need (rosters, skills,
// editions) is persisted by the query cache, and writes go through the IndexedDB outbox.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { Alert, Badge, Button, Card, Empty, LevelPill, Loading, PageHeader, SelectInput, TextArea, TextInput, cx, useToast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { localDateTimeInput } from '@/lib/format'
import { capture, newId, type FlushResult } from '@/lib/offline/queue'
import { useEditions, useSkills, useSquad, useSquads } from '@/lib/queries'
import type { Award, Membership } from '@/lib/types'

function useSquadParam(): [string, (id: string) => void] {
  const [params, setParams] = useSearchParams()
  const squads = useSquads()
  const id = params.get('squad') ?? ''
  useEffect(() => {
    if (!id && squads.data?.length === 1) setParams({ squad: squads.data[0].id }, { replace: true })
  }, [id, squads.data, setParams])
  return [id, (v: string) => setParams(v ? { squad: v } : {})]
}

function SquadPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useTranslation()
  const squads = useSquads()
  return (
    <SelectInput label={t('capture.chooseSquad')} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="" />
      {(squads.data ?? []).map((s) => (
        <option key={s.id} value={s.id}>
          {s.name}
        </option>
      ))}
    </SelectInput>
  )
}

function useSyncToast() {
  const { t } = useTranslation()
  const [toast, show] = useToast()
  const report = (r: FlushResult) => {
    if (r.offline || r.remaining > 0) show(t('common.queued'), 'info')
    else if (r.failed > 0) show(t('sync.failed'), 'error')
    else show(t('common.saved'))
  }
  return [toast, report] as const
}

const STATUSES = ['present', 'late', 'absent', 'excused'] as const
type AttendanceStatus = (typeof STATUSES)[number]

export function AttendancePage() {
  const { t } = useTranslation()
  const [squadId, setSquadId] = useSquadParam()
  const squad = useSquad(squadId || undefined)
  const skills = useSkills()
  const [startsAt, setStartsAt] = useState(localDateTimeInput())
  const [location, setLocation] = useState('')
  const [covered, setCovered] = useState<string[]>([])
  const [marks, setMarks] = useState<Record<string, AttendanceStatus>>({})
  const [busy, setBusy] = useState(false)
  const [toast, report] = useSyncToast()
  const members: Membership[] = (squad.data?.members ?? []).filter((m) => m.status === 'active')

  const save = async () => {
    setBusy(true)
    const sessionId = newId()
    const now = new Date().toISOString()
    // Two queued writes, flushed in order; the session ID is generated here so the
    // attendance can reference it before the server has ever seen the session.
    await capture({
      method: 'POST',
      path: '/sessions',
      label: `${t('nav.attendance')}: ${squad.data?.name ?? ''} — ${startsAt.replace('T', ' ')}`,
      body: {
        id: sessionId,
        idempotency_key: `session-${sessionId}`,
        squad_id: squadId,
        starts_at: new Date(startsAt).toISOString(),
        location: location || null,
        skill_ids: covered,
        client_modified_at: now,
      },
    })
    const r = await capture({
      method: 'POST',
      path: `/sessions/${sessionId}/attendance`,
      label: `${t('nav.attendance')}: ${members.length}`,
      body: {
        idempotency_key: `attendance-${sessionId}`,
        records: members.map((m) => ({ student_id: m.student_id, status: marks[m.student_id] ?? 'present', client_modified_at: now })),
      },
    })
    report(r)
    setMarks({})
    setBusy(false)
  }

  return (
    <>
      {toast}
      <PageHeader title={t('capture.attendanceTitle')} />
      <Card>
        <div className="grid gap-3 sm:grid-cols-3">
          <SquadPicker value={squadId} onChange={setSquadId} />
          <TextInput label={t('capture.startsAt')} type="datetime-local" value={startsAt} onChange={(e) => setStartsAt(e.target.value)} />
          <TextInput label={t('capture.location')} value={location} onChange={(e) => setLocation(e.target.value)} />
        </div>
        {skills.data && (
          <details className="mt-3">
            <summary className="cursor-pointer text-sm font-medium">
              {t('capture.skillsCovered')} {covered.length > 0 && <Badge tone="brand">{covered.length}</Badge>}
            </summary>
            <SkillChooser value={covered} onChange={setCovered} />
          </details>
        )}
      </Card>
      {squadId && (
        <Card className="mt-4">
          {squad.isLoading ? (
            <Loading />
          ) : members.length === 0 ? (
            <Empty />
          ) : (
            <>
              <div className="mb-3 flex justify-end">
                <Button variant="secondary" size="sm" onClick={() => setMarks(Object.fromEntries(members.map((m) => [m.student_id, 'present'])))}>
                  {t('capture.markAllPresent')}
                </Button>
              </div>
              <ul className="divide-y divide-slate-100">
                {members.map((m) => {
                  const current = marks[m.student_id] ?? 'present'
                  return (
                    <li key={m.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                      <span className="font-medium">{m.name}</span>
                      <div role="radiogroup" aria-label={m.name} className="flex gap-1">
                        {STATUSES.map((st) => (
                          <button
                            key={st}
                            role="radio"
                            aria-checked={current === st}
                            onClick={() => setMarks((x) => ({ ...x, [m.student_id]: st }))}
                            className={cx(
                              'min-h-[44px] min-w-[72px] rounded-lg border px-2 text-sm font-medium',
                              current === st
                                ? st === 'present'
                                  ? 'border-emerald-700 bg-emerald-700 text-white'
                                  : st === 'absent'
                                    ? 'border-red-700 bg-red-700 text-white'
                                    : 'border-slate-800 bg-slate-800 text-white'
                                : 'border-slate-300 bg-white text-slate-700',
                            )}
                          >
                            {t(`capture.${st}`)}
                          </button>
                        ))}
                      </div>
                    </li>
                  )
                })}
              </ul>
              <Button className="mt-4 w-full" size="lg" busy={busy} onClick={() => void save()}>
                {t('capture.saveAttendance')}
              </Button>
            </>
          )}
        </Card>
      )}
    </>
  )
}

function SkillChooser({ value, onChange, single }: { value: string[]; onChange: (v: string[]) => void; single?: boolean }) {
  const { t } = useTranslation()
  const skills = useSkills()
  const [q, setQ] = useState('')
  const list = useMemo(() => {
    const all = skills.data ?? []
    const needle = q.toLowerCase()
    return needle ? all.filter((s) => s.code.toLowerCase().includes(needle) || s.name.toLowerCase().includes(needle)) : all
  }, [skills.data, q])
  return (
    <div className="mt-2 space-y-2">
      <TextInput label={t('common.search')} value={q} onChange={(e) => setQ(e.target.value)} placeholder="PROG.LOOP, nested loops…" />
      <ul className="max-h-64 overflow-auto rounded-lg border border-slate-200">
        {list.slice(0, 80).map((s) => {
          const on = value.includes(s.id)
          return (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => onChange(single ? [s.id] : on ? value.filter((x) => x !== s.id) : [...value, s.id])}
                className={cx('flex w-full items-start gap-2 px-3 py-2 text-start text-sm', on ? 'bg-brand-soft' : 'hover:bg-slate-50')}
                aria-pressed={on}
              >
                <span className="font-mono text-xs text-slate-500">{s.code}</span>
                <span>{s.name}</span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export function ResultEntryPage() {
  const { t } = useTranslation()
  const [squadId, setSquadId] = useSquadParam()
  const squad = useSquad(squadId || undefined)
  const editions = useEditions()
  const [editionId, setEditionId] = useState('')
  const [participants, setParticipants] = useState<string[]>([])
  const [form, setForm] = useState({ entry_name: '', placement: '', field_size: '', score: '', max_score: '', award_title: '', judge_feedback: '' })
  const [rubric, setRubric] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [toast, report] = useSyncToast()
  const edition = editions.data?.find((e) => e.id === editionId)
  const members = (squad.data?.members ?? []).filter((m) => m.status === 'active')
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setForm((f) => ({ ...f, [k]: e.target.value }))
  const num = (v: string) => (v === '' ? null : Number(v))

  const save = async () => {
    setBusy(true)
    const id = newId()
    const r = await capture({
      method: 'POST',
      path: '/results',
      label: `${t('nav.results')}: ${edition?.name ?? ''}`,
      body: {
        id,
        idempotency_key: `result-${id}`,
        edition_id: editionId,
        squad_id: squadId || null,
        entry_name: form.entry_name || squad.data?.name || null,
        participant_ids: participants,
        placement: num(form.placement),
        field_size: num(form.field_size),
        score: num(form.score),
        max_score: num(form.max_score),
        rubric_scores: Object.fromEntries(Object.entries(rubric).filter(([, v]) => v !== '').map(([k, v]) => [k, Number(v)])),
        award_title: form.award_title || null,
        judge_feedback: form.judge_feedback || null,
        client_modified_at: new Date().toISOString(),
      },
    })
    report(r)
    setBusy(false)
    if (!r.failed) {
      setForm({ entry_name: '', placement: '', field_size: '', score: '', max_score: '', award_title: '', judge_feedback: '' })
      setRubric({})
    }
  }

  return (
    <>
      {toast}
      <PageHeader title={t('capture.resultTitle')} />
      <Card>
        <form
          className="grid gap-4 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault()
            void save()
          }}
        >
          <SquadPicker value={squadId} onChange={setSquadId} />
          <SelectInput label={t('capture.edition')} value={editionId} onChange={(e) => setEditionId(e.target.value)} required>
            <option value="" />
            {(editions.data ?? []).map((e) => (
              <option key={e.id} value={e.id}>
                {e.name}
              </option>
            ))}
          </SelectInput>
          <fieldset className="sm:col-span-2">
            <legend className="mb-1 text-sm font-medium text-slate-700">{t('capture.participants')}</legend>
            <div className="flex flex-wrap gap-2">
              {members.map((m) => {
                const on = participants.includes(m.student_id)
                return (
                  <button
                    type="button"
                    key={m.id}
                    aria-pressed={on}
                    onClick={() => setParticipants((p) => (on ? p.filter((x) => x !== m.student_id) : [...p, m.student_id]))}
                    className={cx('min-h-[44px] rounded-lg border px-3 text-sm', on ? 'border-brand bg-brand text-brand-ink' : 'border-slate-300 bg-white')}
                  >
                    {m.name}
                  </button>
                )
              })}
            </div>
          </fieldset>
          <TextInput label={t('capture.entryName')} value={form.entry_name} onChange={set('entry_name')} />
          <div className="grid grid-cols-2 gap-3">
            <TextInput label={t('capture.placement')} type="number" min={1} inputMode="numeric" value={form.placement} onChange={set('placement')} />
            <TextInput label={t('capture.fieldSize')} type="number" min={1} inputMode="numeric" value={form.field_size} onChange={set('field_size')} />
          </div>
          <p className="text-xs text-slate-600 sm:col-span-2">{t('capture.fieldSizeHint')}</p>
          {form.placement && !form.field_size && <Alert tone="warn">{t('profile.fieldMissing')}</Alert>}
          <div className="grid grid-cols-2 gap-3">
            <TextInput label={t('capture.score')} type="number" step="any" value={form.score} onChange={set('score')} />
            <TextInput label={t('capture.maxScore')} type="number" step="any" value={form.max_score} onChange={set('max_score')} />
          </div>
          {edition && edition.rubric.length > 0 && (
            <fieldset className="sm:col-span-2">
              <legend className="mb-1 text-sm font-medium text-slate-700">{t('capture.rubric')}</legend>
              <div className="grid gap-3 sm:grid-cols-2">
                {edition.rubric.map((c) => (
                  <TextInput
                    key={c.key}
                    label={`${c.label} (/${c.max_score})${c.skill_code ? ` · ${c.skill_code}` : ''}`}
                    type="number"
                    min={0}
                    max={c.max_score}
                    step="any"
                    value={rubric[c.key] ?? ''}
                    onChange={(e) => setRubric((r) => ({ ...r, [c.key]: e.target.value }))}
                  />
                ))}
              </div>
            </fieldset>
          )}
          <TextInput label={t('capture.award')} value={form.award_title} onChange={set('award_title')} />
          <TextArea label={t('capture.feedback')} value={form.judge_feedback} onChange={set('judge_feedback')} />
          <Button type="submit" size="lg" className="sm:col-span-2" busy={busy} disabled={!editionId || participants.length === 0}>
            {t('capture.saveResult')}
          </Button>
        </form>
      </Card>
    </>
  )
}

export function QuickTagPage() {
  const { t } = useTranslation()
  const [squadId, setSquadId] = useSquadParam()
  const squad = useSquad(squadId || undefined)
  const [skill, setSkill] = useState<string[]>([])
  const [level, setLevel] = useState(2)
  const [selected, setSelected] = useState<string[]>([])
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [toast, report] = useSyncToast()
  const members = (squad.data?.members ?? []).filter((m) => m.status === 'active')
  const save = async () => {
    setBusy(true)
    const key = newId()
    report(
      await capture({
        method: 'POST',
        path: '/skills/awards/quick-tag',
        label: `${t('nav.quickTag')}: ${selected.length}`,
        body: { idempotency_key: key, skill_id: skill[0], level, student_ids: selected, evidence_note: note || null },
      }),
    )
    setSelected([])
    setNote('')
    setBusy(false)
  }
  return (
    <>
      {toast}
      <PageHeader title={t('capture.quickTagTitle')} subtitle={t('capture.quickTagHint')} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SquadPicker value={squadId} onChange={setSquadId} />
          <h3 className="mt-4">{t('capture.skill')}</h3>
          <SkillChooser value={skill} onChange={setSkill} single />
        </Card>
        <Card>
          <h3>{t('capture.level')}</h3>
          <div className="mt-2 grid grid-cols-2 gap-2" role="radiogroup">
            {[1, 2, 3, 4].map((l) => (
              <button
                key={l}
                role="radio"
                aria-checked={level === l}
                onClick={() => setLevel(l)}
                className={cx('min-h-[52px] rounded-lg border p-2', level === l ? 'border-brand ring-2 ring-brand/40' : 'border-slate-300')}
              >
                <LevelPill level={l} />
              </button>
            ))}
          </div>
          <h3 className="mt-4">{t('capture.participants')}</h3>
          <div className="mt-2 flex flex-wrap gap-2">
            {members.map((m) => {
              const on = selected.includes(m.student_id)
              return (
                <button
                  key={m.id}
                  aria-pressed={on}
                  onClick={() => setSelected((s) => (on ? s.filter((x) => x !== m.student_id) : [...s, m.student_id]))}
                  className={cx('min-h-[44px] rounded-lg border px-3 text-sm', on ? 'border-brand bg-brand text-brand-ink' : 'border-slate-300 bg-white')}
                >
                  {m.name}
                </button>
              )
            })}
          </div>
          <div className="mt-3">
            <TextInput label={t('capture.evidenceNote')} value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          <Button className="mt-4 w-full" size="lg" busy={busy} disabled={!skill[0] || selected.length === 0} onClick={() => void save()}>
            {t('capture.tagStudents', { count: selected.length })}
          </Button>
        </Card>
      </div>
    </>
  )
}

export function ConfirmAwardsPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [squadId, setSquadId] = useSquadParam()
  const q = useQuery({
    queryKey: ['proposed', squadId],
    queryFn: () => api<Award[]>(`/skills/awards/proposed${squadId ? `?squad_id=${squadId}` : ''}`),
  })
  const squad = useSquad(squadId || undefined)
  const names = new Map((squad.data?.members ?? []).map((m) => [m.student_id, m.name]))
  const [sel, setSel] = useState<string[]>([])
  const [levels, setLevels] = useState<Record<string, number>>({})
  const [toast, show] = useToast()
  const act = useMutation({
    mutationFn: (mode: 'confirm' | 'reject') =>
      api('/skills/awards/bulk-confirm', {
        method: 'POST',
        body: mode === 'confirm' ? { confirm: sel, level_overrides: Object.fromEntries(sel.filter((id) => levels[id]).map((id) => [id, levels[id]])) } : { reject: sel },
      }),
    onSuccess: () => {
      setSel([])
      show(t('common.saved'))
      void qc.invalidateQueries({ queryKey: ['proposed'] })
    },
    onError: (e) => show(errorMessage(e), 'error'),
  })
  const items = q.data ?? []
  return (
    <>
      {toast}
      <PageHeader title={t('capture.confirmTitle')} subtitle={t('capture.confirmHint')} />
      <Card>
        <div className="mb-4 max-w-sm">
          <SquadPicker value={squadId} onChange={setSquadId} />
        </div>
        {q.isLoading ? (
          <Loading />
        ) : items.length === 0 ? (
          <Empty>{t('capture.nothingToConfirm')}</Empty>
        ) : (
          <>
            <div className="mb-2 flex flex-wrap gap-2">
              <Button variant="secondary" size="sm" onClick={() => setSel(sel.length === items.length ? [] : items.map((a) => a.id))}>
                {t('capture.selectAll')}
              </Button>
              <Button size="sm" disabled={!sel.length} busy={act.isPending} onClick={() => act.mutate('confirm')}>
                {t('capture.confirmSelected')} ({sel.length})
              </Button>
              <Button size="sm" variant="danger" disabled={!sel.length} onClick={() => act.mutate('reject')}>
                {t('capture.rejectSelected')}
              </Button>
            </div>
            <ul className="divide-y divide-slate-100">
              {items.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-3 py-2">
                  <input
                    type="checkbox"
                    className="h-5 w-5"
                    aria-label={`${a.student_name ?? ''} ${a.skill_code}`}
                    checked={sel.includes(a.id)}
                    onChange={(e) => setSel((s) => (e.target.checked ? [...s, a.id] : s.filter((x) => x !== a.id)))}
                  />
                  <div className="min-w-[160px] flex-1">
                    <div className="font-medium">{a.student_name ?? names.get(a.student_id) ?? '—'}</div>
                    <div className="text-xs text-slate-600">
                      <span className="font-mono">{a.skill_code}</span> {a.skill_name}
                    </div>
                    <div className="text-xs text-slate-500">
                      {a.source === 'self' ? t('capture.selfAssessed') : a.rubric_criterion ? t('capture.fromRubric', { criterion: a.rubric_criterion }) : a.source} · {a.evidence_note}
                    </div>
                  </div>
                  <select
                    className="input w-40"
                    aria-label={t('capture.level')}
                    value={levels[a.id] ?? a.level}
                    onChange={(e) => setLevels((l) => ({ ...l, [a.id]: Number(e.target.value) }))}
                  >
                    {[1, 2, 3, 4].map((l) => (
                      <option key={l} value={l}>
                        {t(`levels.${l}` as 'levels.1')}
                      </option>
                    ))}
                  </select>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>
    </>
  )
}
