import { useQuery } from '@tanstack/react-query'
import {
  BadgeCheck,
  CalendarDays,
  ChartColumn,
  ChevronRight,
  ClipboardCheck,
  FileText,
  Flag,
  GraduationCap,
  Hourglass,
  type LucideIcon,
  Medal,
  MessageSquare,
  NotebookPen,
  Package,
  PartyPopper,
  Sparkles,
  Trophy,
  Upload,
  UsersRound,
  Wallet,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ActionTile, EmptyState, Loading, Section, TodoItem } from '@/components/ui'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { fmtDate } from '@/lib/format'

import { SchoolOverview } from './Dashboard'

type TodoKey =
  | 'spend_to_approve'
  | 'messages_to_release'
  | 'skills_to_approve'
  | 'notes_to_review'
  | 'students_to_check'
  | 'templates_to_approve'
  | 'kit_requests'
  | 'competitions_to_approve'
  | 'drafts_awaiting_release'

interface HomeSummary {
  counts: Partial<Record<TodoKey, number>>
  squads: { id: string; name: string; discipline: string | null; member_count: number; next_event: { edition_id: string; name: string; starts: string } | null }[]
}

// Most time-sensitive first. Drafts waiting for someone else are shown, but nothing to click.
const TODOS: { key: TodoKey; to?: string; icon: LucideIcon }[] = [
  { key: 'spend_to_approve', to: '/budget', icon: Wallet },
  { key: 'messages_to_release', to: '/messages?status=draft', icon: MessageSquare },
  { key: 'skills_to_approve', to: '/capture/confirm', icon: BadgeCheck },
  { key: 'notes_to_review', to: '/insights', icon: NotebookPen },
  { key: 'students_to_check', to: '/flags', icon: Flag },
  { key: 'templates_to_approve', to: '/templates', icon: FileText },
  { key: 'kit_requests', to: '/inventory', icon: Package },
  { key: 'competitions_to_approve', to: '/competitions', icon: Trophy },
  { key: 'drafts_awaiting_release', icon: Hourglass },
]

function greeting(name: string, t: ReturnType<typeof useTranslation>['t']): string {
  const h = new Date().getHours()
  // "Dr Mariam Al Suwaidi" is greeted as "Dr Mariam", "Sara Haddad" as "Sara".
  const words = name.split(/\s+/)
  const first = /^(dr|mr|mrs|ms|miss|mx|prof)\.?$/i.test(words[0]) && words[1] ? `${words[0]} ${words[1]}` : words[0]
  return h < 12 ? t('home.morning', { name: first }) : h < 17 ? t('home.afternoon', { name: first }) : t('home.evening', { name: first })
}

/** Where every member of staff starts: what is waiting for them, and the tasks they do most. */
export default function HomePage() {
  const { t } = useTranslation()
  const { me, can, hasRole } = useAuth()
  const q = useHomeSummary()
  if (!me) return <Loading />
  const wholeSchool = can('view_school_analytics')

  const actions: { show: boolean; to: string; icon: LucideIcon; title: string; description: string }[] = [
    { show: can('record_attendance'), to: '/capture/attendance', icon: ClipboardCheck, title: t('nav.attendance'), description: t('home.attendanceDesc') },
    { show: can('verify_skills'), to: '/capture/tag', icon: Sparkles, title: t('nav.quickTag'), description: t('home.tagDesc') },
    { show: can('record_results'), to: '/capture/result', icon: Medal, title: t('capture.resultTitle'), description: t('home.resultDesc') },
    { show: can('draft_messages'), to: '/messages/new', icon: MessageSquare, title: t('messages.compose'), description: t('home.messageDesc') },
    { show: can('manage_imports'), to: '/imports', icon: Upload, title: t('nav.imports'), description: t('home.importDesc') },
    { show: wholeSchool, to: '/students', icon: GraduationCap, title: t('nav.students'), description: t('home.studentsDesc') },
    { show: can('generate_reports'), to: '/reports', icon: ChartColumn, title: t('nav.reports'), description: t('home.reportDesc') },
    { show: true, to: '/competitions', icon: Trophy, title: t('nav.competitions'), description: t('home.competitionsDesc') },
  ]
  const todos = TODOS.map((td) => ({ ...td, count: q.data?.counts[td.key] ?? 0 })).filter((td) => td.count > 0)
  const squads = q.data?.squads ?? []

  const quick = (
    <Section title={t('home.quickActions')}>
      <div className="grid gap-3 sm:grid-cols-2">
        {actions
          .filter((a) => a.show)
          .slice(0, 4)
          .map((a) => (
            <ActionTile key={a.to} to={a.to} icon={a.icon} title={a.title} description={a.description} />
          ))}
      </div>
    </Section>
  )
  const attention = (
    <Section title={t('home.attention')}>
      {q.isLoading ? (
        <Loading />
      ) : todos.length === 0 ? (
        <div className="card flex items-center gap-3 text-sm text-slate-600">
          <PartyPopper aria-hidden className="h-5 w-5 text-emerald-600" />
          {t('home.allClear')}
        </div>
      ) : (
        <div className="card divide-y divide-slate-100 p-0">
          {todos.map((td) => (
            <TodoItem key={td.key} to={td.to} icon={td.icon} count={td.count} label={t(`home.todo.${td.key}`)} action={td.to ? t('common.review') : undefined} />
          ))}
        </div>
      )}
    </Section>
  )
  // Leaders land on the six numbers (spec §6.4: one screen); everyone else on their tasks.
  const leader = hasRole('leader')
  return (
    <div className="space-y-10">
      <header>
        <h1>{greeting(me.display_name, t)}</h1>
        <p className="mt-1 text-[15px] text-slate-600">{t('home.intro')}</p>
      </header>
      {leader ? (
        <>
          <SchoolOverview />
          {attention}
          {quick}
        </>
      ) : (
        <>
          {quick}
          {attention}
          {wholeSchool && <SchoolOverview />}
        </>
      )}
      {(squads.length > 0 || (!wholeSchool && q.isSuccess)) && (
        <Section title={wholeSchool ? t('home.allSquads') : t('home.yourSquads')} description={t('home.squadsHint')}>
          {squads.length === 0 ? (
            <EmptyState icon={UsersRound} title={t('home.noSquads')}>
              {t('home.noSquadsHint')}
            </EmptyState>
          ) : (
            <SquadCards />
          )}
        </Section>
      )}
    </div>
  )
}

function useHomeSummary() {
  return useQuery({ queryKey: ['home'], queryFn: () => api<HomeSummary>('/home'), meta: { persist: true } })
}

/** Each squad with its size and next competition; shared by the home and squads pages. */
export function SquadCards() {
  const { t } = useTranslation()
  const q = useHomeSummary()
  if (q.isLoading) return <Loading />
  const squads = q.data?.squads ?? []
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {squads.map((s) => (
        <Link key={s.id} to={`/squads/${s.id}`} className="group card flex min-w-0 flex-col gap-3 transition hover:border-brand/40 hover:shadow-raised">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="font-semibold text-slate-900">{s.name}</div>
              <div className="text-sm text-slate-500">
                {[s.discipline && t(`domains.${s.discipline}` as 'domains.Coding', { defaultValue: s.discipline }), t('home.members', { n: s.member_count })].filter(Boolean).join(' · ')}
              </div>
            </div>
            <ChevronRight aria-hidden className="mt-1 h-4 w-4 shrink-0 text-slate-300 group-hover:text-brand rtl:rotate-180" />
          </div>
          <div className="flex items-start gap-2 text-sm text-slate-600">
            <CalendarDays aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
            {s.next_event ? (
              <span className="min-w-0">
                <span className="line-clamp-2 block text-slate-800" dir="auto">
                  {s.next_event.name}
                </span>
                <span className="block text-slate-500">{t('home.nextOn', { date: fmtDate(s.next_event.starts, { day: 'numeric', month: 'short' }) })}</span>
              </span>
            ) : (
              <span>{t('home.noNextEvent')}</span>
            )}
          </div>
        </Link>
      ))}
    </div>
  )
}
