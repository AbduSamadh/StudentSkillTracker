// Phone-first capture screens, each laid out as numbered steps. All of them work offline: data
// they need (rosters, skills, editions) is persisted by the query cache, and writes go through
// the IndexedDB outbox.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BadgeCheck, ClipboardCheck, Medal, Search, Sparkles, UsersRound } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { Alert, Button, ChoiceCard, EmptyState, InfoNote, LevelPill, Loading, PageHeader, SelectInput, Step, TextArea, TextInput, cx, useToast } from '@/components/ui'
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

/** Step 1 of every capture task: pick the squad from large cards, then collapse to its name. */
function SquadStep({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useTranslation()
  const squads = useSquads()
  const chosen = squads.data?.find((s) => s.id === value)
  return (
    <Step n={1} title={chosen ? t('capture.squadChosen', { name: chosen.name }) : t('capture.chooseSquad')} done={!!chosen}>
      {chosen ? (
        squads.data!.length > 1 && (
          <Button size="sm" variant="secondary" onClick={() => onChange('')}>
            {t('capture.changeSquad')}
          </Button>
        )
      ) : squads.isLoading ? (
        <Loading />
      ) : !squads.data?.length ? (
        <EmptyState icon={UsersRound} title={t('home.noSquads')}>
          {t('home.noSquadsHint')}
        </EmptyState>
      ) : (
        <div role="radiogroup" aria-label={t('capture.chooseSquad')} className="grid gap-2 sm:grid-cols-2">
          {squads.data.map((s) => (
            <ChoiceCard
              key={s.id}
              selected={false}
              onSelect={() => onChange(s.id)}
              title={s.name}
              meta={s.discipline && t(`domains.${s.discipline}` as 'domains.Coding', { defaultValue: s.discipline })}
              icon={UsersRound}
            />
          ))}
        </div>
      )}
    </Step>
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

/** Tap-to-select student chips, with a select-all shortcut. */
function StudentChips({ members, selected, onChange }: { members: Membership[]; selected: string[]; onChange: (ids: string[]) => void }) {
  const { t } = useTranslation()
  const all = members.length > 0 && selected.length === members.length
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm text-slate-500">{t('capture.selectedCount', { n: selected.length, m: members.length })}</span>
        <Button size="sm" variant="ghost" onClick={() => onChange(all ? [] : members.map((m) => m.student_id))}>
          {all ? t('capture.clearAll') : t('capture.selectAll')}
        </Button>
      </div>
      <div className="flex flex-wrap gap-2">
        {members.map((m) => {
          const on = selected.includes(m.student_id)
          return (
            <button
              type="button"
              key={m.id}
              aria-pressed={on}
              onClick={() => onChange(on ? selected.filter((x) => x !== m.student_id) : [...selected, m.student_id])}
              className={cx(
                'min-h-11 rounded-full border px-4 text-sm font-medium transition-colors',
                on ? 'border-brand bg-brand text-brand-ink' : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50',
              )}
            >
              {m.name}
            </button>
          )
        })}
      </div>
    </div>
  )
}

const STATUSES = ['present', 'late', 'absent', 'excused'] as const
type AttendanceStatus = (typeof STATUSES)[number]
const STATUS_ON: Record<AttendanceStatus, string> = {
  present: 'border-emerald-600 bg-emerald-600 text-white',
  late: 'border-amber-500 bg-amber-500 text-white',
  absent: 'border-red-600 bg-red-600 text-white',
  excused: 'border-slate-600 bg-slate-600 text-white',
}

export function AttendancePage() {
  const { t } = useTranslation()
  const [squadId, setSquadId] = useSquadParam()
  const squad = useSquad(squadId || undefined)
  const [startsAt, setStartsAt] = useState(localDateTimeInput())
  const [location, setLocation] = useState('')
  const [covered, setCovered] = useState<string[]>([])
  const [marks, setMarks] = useState<Record<string, AttendanceStatus>>({})
  const [busy, setBusy] = useState(false)
  const [toast, report] = useSyncToast()
  const members: Membership[] = (squad.data?.members ?? []).filter((m) => m.status === 'active')
  const notPresent = Object.values(marks).filter((s) => s !== 'present').length

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
      <PageHeader icon={ClipboardCheck} title={t('capture.attendanceTitle')} subtitle={t('capture.attendanceIntro')} />
      <div className="max-w-3xl space-y-3">
        <SquadStep value={squadId} onChange={setSquadId} />
        <Step n={2} title={t('capture.whenWhere')} active={!!squadId}>
          <div className="grid gap-4 sm:grid-cols-2">
            <TextInput label={t('capture.startsAt')} type="datetime-local" value={startsAt} onChange={(e) => setStartsAt(e.target.value)} />
            <TextInput label={`${t('capture.location')} (${t('common.optional')})`} value={location} onChange={(e) => setLocation(e.target.value)} placeholder={t('capture.locationPlaceholder')} />
          </div>
          <details className="mt-4 rounded-xl border border-slate-200 p-3">
            <summary className="text-sm font-medium text-slate-700">
              {t('capture.skillsCovered')} {covered.length > 0 && <span className="text-brand">({covered.length})</span>}
            </summary>
            <p className="mt-2 text-sm text-slate-500">{t('capture.skillsCoveredHint')}</p>
            <SkillChooser value={covered} onChange={setCovered} />
          </details>
        </Step>
        <Step n={3} title={t('capture.whoWasThere')} hint={t('capture.whoWasThereHint')} active={!!squadId}>
          {squad.isLoading ? (
            <Loading />
          ) : members.length === 0 ? (
            <EmptyState icon={UsersRound} title={t('capture.noMembers')} />
          ) : (
            <>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm text-slate-500">{t('capture.notPresentCount', { n: notPresent, m: members.length })}</span>
                <Button variant="ghost" size="sm" onClick={() => setMarks(Object.fromEntries(members.map((m) => [m.student_id, 'present'])))}>
                  {t('capture.markAllPresent')}
                </Button>
              </div>
              <ul className="divide-y divide-slate-100">
                {members.map((m) => {
                  const current = marks[m.student_id] ?? 'present'
                  return (
                    <li key={m.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
                      <span className="font-medium text-slate-900">{m.name}</span>
                      <div role="radiogroup" aria-label={m.name} className="grid w-full grid-cols-4 gap-1 sm:w-auto">
                        {STATUSES.map((st) => (
                          <button
                            key={st}
                            role="radio"
                            aria-checked={current === st}
                            onClick={() => setMarks((x) => ({ ...x, [m.student_id]: st }))}
                            className={cx(
                              'min-h-11 rounded-lg border px-2 text-sm font-medium transition-colors sm:min-w-20',
                              current === st ? STATUS_ON[st] : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50',
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
              <Button className="mt-5 w-full" size="lg" icon={ClipboardCheck} busy={busy} onClick={() => void save()}>
                {t('capture.saveAttendance')}
              </Button>
            </>
          )}
        </Step>
      </div>
    </>
  )
}

/** Search the skills list by everyday words; the code is shown small, for those who use it. */
function SkillChooser({ value, onChange, single }: { value: string[]; onChange: (v: string[]) => void; single?: boolean }) {
  const { t } = useTranslation()
  const skills = useSkills()
  const [q, setQ] = useState('')
  const list = useMemo(() => {
    const all = skills.data ?? []
    const needle = q.toLowerCase()
    return needle ? all.filter((s) => s.code.toLowerCase().includes(needle) || s.name.toLowerCase().includes(needle) || s.domain.toLowerCase().includes(needle)) : all
  }, [skills.data, q])
  const chosen = (skills.data ?? []).filter((s) => value.includes(s.id))
  return (
    <div className="mt-3 space-y-3">
      {single && chosen[0] && (
        <div className="flex items-center gap-2 rounded-xl bg-brand-soft px-3 py-2 text-sm">
          <Sparkles aria-hidden className="h-4 w-4 shrink-0 text-brand" />
          <span className="font-medium text-slate-900">{chosen[0].name}</span>
        </div>
      )}
      <div className="relative">
        <Search aria-hidden className="pointer-events-none absolute inset-s-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
        <input
          className="input ps-9"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t('capture.skillSearch')}
          aria-label={t('capture.skillSearchLabel')}
        />
      </div>
      <ul className="max-h-72 divide-y divide-slate-100 overflow-auto rounded-xl border border-slate-200">
        {list.slice(0, 80).map((s) => {
          const on = value.includes(s.id)
          return (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => onChange(single ? [s.id] : on ? value.filter((x) => x !== s.id) : [...value, s.id])}
                className={cx('flex w-full items-start justify-between gap-3 px-3 py-2.5 text-start text-sm', on ? 'bg-brand-soft' : 'hover:bg-slate-50')}
                aria-pressed={on}
              >
                <span className="min-w-0">
                  <span className="block text-slate-900">{s.name}</span>
                  <span className="block text-xs text-slate-500">{t(`domains.${s.domain}` as 'domains.Coding', { defaultValue: s.domain })}</span>
                </span>
                <span className="shrink-0 font-mono text-[11px] text-slate-400">{s.code}</span>
              </button>
            </li>
          )
        })}
        {list.length === 0 && <li className="px-3 py-4 text-sm text-slate-500">{t('capture.noSkillMatch')}</li>}
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
      setParticipants([])
    }
  }

  return (
    <>
      {toast}
      <PageHeader icon={Medal} title={t('capture.resultTitle')} subtitle={t('capture.resultIntro')} />
      <form
        className="max-w-3xl space-y-3"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <SquadStep value={squadId} onChange={setSquadId} />
        <Step n={2} title={t('capture.whichCompetition')} active={!!squadId} done={!!editionId}>
          <SelectInput label={t('capture.edition')} value={editionId} onChange={(e) => setEditionId(e.target.value)} required>
            <option value="">{t('capture.chooseCompetition')}</option>
            {(editions.data ?? []).map((e) => (
              <option key={e.id} value={e.id}>
                {e.name}
              </option>
            ))}
          </SelectInput>
        </Step>
        <Step n={3} title={t('capture.whoTookPart')} active={!!squadId && !!editionId} done={participants.length > 0}>
          <StudentChips members={members} selected={participants} onChange={setParticipants} />
        </Step>
        <Step n={4} title={t('capture.howDidTheyDo')} hint={t('capture.howDidTheyDoHint')} active={!!squadId && !!editionId}>
          <div className="grid gap-4 sm:grid-cols-2">
            <TextInput label={t('capture.placement')} type="number" min={1} inputMode="numeric" value={form.placement} onChange={set('placement')} placeholder="1" />
            <TextInput label={t('capture.fieldSize')} type="number" min={1} inputMode="numeric" value={form.field_size} onChange={set('field_size')} placeholder="24" />
          </div>
          <p className="mt-2 text-sm text-slate-500">{t('capture.fieldSizeHint')}</p>
          {form.placement && !form.field_size && (
            <div className="mt-3">
              <Alert tone="warn">{t('profile.fieldMissing')}</Alert>
            </div>
          )}
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <TextInput label={`${t('capture.score')} (${t('common.optional')})`} type="number" step="any" value={form.score} onChange={set('score')} />
            <TextInput label={`${t('capture.maxScore')} (${t('common.optional')})`} type="number" step="any" value={form.max_score} onChange={set('max_score')} />
          </div>
          <details className="mt-4 rounded-xl border border-slate-200 p-3">
            <summary className="text-sm font-medium text-slate-700">{t('common.moreDetail')}</summary>
            <div className="mt-3 grid gap-4">
              <TextInput label={t('capture.entryName')} value={form.entry_name} onChange={set('entry_name')} placeholder={squad.data?.name} />
              {edition && edition.rubric.length > 0 && (
                <fieldset>
                  <legend className="mb-1 text-sm font-medium text-slate-800">{t('capture.rubric')}</legend>
                  <p className="mb-2 text-sm text-slate-500">{t('capture.rubricHint')}</p>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {edition.rubric.map((c) => (
                      <TextInput
                        key={c.key}
                        label={`${c.label} (/${c.max_score})`}
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
            </div>
          </details>
          <Button type="submit" size="lg" icon={Medal} className="mt-5 w-full" busy={busy} disabled={!editionId || participants.length === 0}>
            {t('capture.saveResult')}
          </Button>
          {(!editionId || participants.length === 0) && <p className="mt-2 text-center text-sm text-slate-500">{t('capture.saveResultNeeds')}</p>}
        </Step>
      </form>
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
      <PageHeader icon={Sparkles} title={t('capture.quickTagTitle')} subtitle={t('capture.quickTagHint')} />
      <div className="max-w-3xl space-y-3">
        <SquadStep value={squadId} onChange={setSquadId} />
        <Step n={2} title={t('capture.whichSkill')} active={!!squadId} done={!!skill[0]}>
          <SkillChooser value={skill} onChange={setSkill} single />
        </Step>
        <Step n={3} title={t('capture.howWell')} active={!!squadId && !!skill[0]}>
          <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label={t('capture.level')}>
            {[1, 2, 3, 4].map((l) => (
              <button
                key={l}
                type="button"
                role="radio"
                aria-checked={level === l}
                onClick={() => setLevel(l)}
                className={cx(
                  'flex flex-col items-start gap-1.5 rounded-xl border bg-white p-3 text-start transition',
                  level === l ? 'border-brand ring-2 ring-brand/25' : 'border-slate-200 hover:bg-slate-50',
                )}
              >
                <LevelPill level={l} />
                <span className="text-sm text-slate-600">{t(`capture.levelHelp.${l}` as 'capture.levelHelp.1')}</span>
              </button>
            ))}
          </div>
        </Step>
        <Step n={4} title={t('capture.whoShowedIt')} active={!!squadId && !!skill[0]}>
          <StudentChips members={members} selected={selected} onChange={setSelected} />
          <div className="mt-4">
            <TextInput label={t('capture.evidenceNote')} value={note} onChange={(e) => setNote(e.target.value)} placeholder={t('capture.evidencePlaceholder')} />
          </div>
          <Button className="mt-5 w-full" size="lg" icon={Sparkles} busy={busy} disabled={!skill[0] || selected.length === 0} onClick={() => void save()}>
            {t('capture.tagStudents', { count: selected.length })}
          </Button>
        </Step>
      </div>
    </>
  )
}

export function ConfirmAwardsPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const squadId = params.get('squad') ?? ''
  const squads = useSquads()
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
      void qc.invalidateQueries({ queryKey: ['home'] })
    },
    onError: (e) => show(errorMessage(e), 'error'),
  })
  const items = q.data ?? []
  return (
    <>
      {toast}
      <PageHeader icon={BadgeCheck} title={t('capture.confirmTitle')} subtitle={t('capture.confirmHint')} />
      <div className="mb-4 max-w-xs">
        <SelectInput label={t('capture.showFor')} value={squadId} onChange={(e) => setParams(e.target.value ? { squad: e.target.value } : {})}>
          <option value="">{t('capture.allMySquads')}</option>
          {(squads.data ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </SelectInput>
      </div>
      {q.isLoading ? (
        <Loading />
      ) : items.length === 0 ? (
        <EmptyState icon={BadgeCheck} title={t('capture.nothingToConfirm')}>
          {t('capture.nothingToConfirmHint')}
        </EmptyState>
      ) : (
        <>
          <section className="card p-0">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-5 py-3">
              <label className="flex items-center gap-2 text-sm font-medium text-slate-700">
                <input type="checkbox" className="h-4 w-4 accent-brand" checked={sel.length === items.length} onChange={(e) => setSel(e.target.checked ? items.map((a) => a.id) : [])} />
                {t('capture.selectAll')}
              </label>
              <span className="text-sm text-slate-500">{t('capture.waitingCount', { n: items.length })}</span>
            </div>
            <ul className="divide-y divide-slate-100">
              {items.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-3 px-5 py-3.5">
                  <input
                    type="checkbox"
                    className="h-5 w-5 accent-brand"
                    aria-label={`${a.student_name ?? ''} ${a.skill_code}`}
                    checked={sel.includes(a.id)}
                    onChange={(e) => setSel((s) => (e.target.checked ? [...s, a.id] : s.filter((x) => x !== a.id)))}
                  />
                  <div className="min-w-[180px] flex-1">
                    <div className="font-medium text-slate-900">{a.student_name ?? names.get(a.student_id) ?? '—'}</div>
                    <div className="text-sm text-slate-700">{a.skill_name}</div>
                    <div className="mt-0.5 text-sm text-slate-500">
                      {a.source === 'self' ? t('capture.selfAssessed') : a.rubric_criterion ? t('capture.fromRubric', { criterion: a.rubric_criterion }) : t(`profile.sources.${a.source}`)}
                      {a.evidence_note && ` · “${a.evidence_note}”`}
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
          </section>
          <div className="sticky bottom-20 z-20 mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white/95 p-3 shadow-raised backdrop-blur-sm lg:bottom-4">
            <span className="px-2 text-sm text-slate-600">{t('capture.selectedCount', { n: sel.length, m: items.length })}</span>
            <div className="flex gap-2">
              <Button variant="danger" disabled={!sel.length} onClick={() => act.mutate('reject')}>
                {t('capture.rejectSelected')}
              </Button>
              <Button icon={BadgeCheck} disabled={!sel.length} busy={act.isPending} onClick={() => act.mutate('confirm')}>
                {t('capture.confirmSelected')}
              </Button>
            </div>
          </div>
          <div className="mt-4">
            <InfoNote>{t('capture.confirmFootnote')}</InfoNote>
          </div>
        </>
      )}
    </>
  )
}
