import {
  BadgeCheck,
  BookOpen,
  ChartColumn,
  ChevronDown,
  ClipboardCheck,
  CloudCheck,
  CloudOff,
  CloudUpload,
  FileText,
  Flag,
  GraduationCap,
  HeartHandshake,
  History,
  House,
  Inbox,
  type LucideIcon,
  LogOut,
  Medal,
  Menu,
  MessageSquare,
  NotebookPen,
  Package,
  Settings,
  Siren,
  SlidersHorizontal,
  Sparkles,
  Trophy,
  Upload,
  UserCog,
  UsersRound,
  Wallet,
  X,
} from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { useAuth } from '@/lib/auth'
import { setLanguage, type Lang } from '@/lib/i18n'
import { useOnline, useOutbox } from '@/lib/offline/hooks'
import { discard, flush, retry } from '@/lib/offline/queue'

import { Badge, Button, cx } from './ui'

export interface NavItem {
  to: string
  label: string
  /** Label under the icon in the phone tab bar. */
  short?: string
  icon: LucideIcon
  show: boolean
  /** Offered in the phone tab bar, in this order, when the person can use it. */
  tab?: boolean
  end?: boolean
}

export interface NavGroup {
  label?: string
  items: NavItem[]
}

export function LanguageToggle({ className }: { className?: string }) {
  const { i18n } = useTranslation()
  const next: Lang = i18n.language === 'ar' ? 'en' : 'ar'
  return (
    <button
      onClick={() => setLanguage(next)}
      className={cx('rounded-lg px-2.5 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900', className)}
      lang={next}
      aria-label={next === 'ar' ? 'التبديل إلى العربية' : 'Switch to English'}
    >
      {next === 'ar' ? 'العربية' : 'English'}
    </button>
  )
}

function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && close()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close()
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, close])
  return ref
}

/** Quiet when everything is saved; speaks up only when offline or something is waiting. */
function SyncStatus() {
  const { t } = useTranslation()
  const online = useOnline()
  const items = useOutbox()
  const [open, setOpen] = useState(false)
  const ref = useDismiss(open, () => setOpen(false))
  const pending = items.filter((i) => i.status === 'pending').length
  const problems = items.filter((i) => i.status !== 'pending')
  const Icon = !online ? CloudOff : pending > 0 ? CloudUpload : CloudCheck
  const label = !online ? t('common.offline') : pending > 0 ? t('sync.waiting', { count: pending }) : t('sync.allSynced')
  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label={label}
        title={label}
        className={cx(
          'flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm font-medium hover:bg-slate-100',
          !online ? 'text-amber-700' : problems.length ? 'text-red-700' : 'text-slate-500',
        )}
      >
        <Icon aria-hidden className="h-5 w-5" />
        <span className="hidden sm:inline">{!online ? t('common.offline') : pending > 0 ? t('sync.waitingShort') : t('sync.saved')}</span>
        {pending > 0 && <Badge tone="amber">{pending}</Badge>}
        {problems.length > 0 && <Badge tone="red">{problems.length}</Badge>}
      </button>
      {open && (
        <div className="absolute inset-e-0 z-40 mt-2 w-80 max-w-[calc(100vw-2rem)] rounded-2xl border border-slate-200 bg-white p-4 shadow-raised">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="text-sm">{t('sync.title')}</h2>
            <Button size="sm" variant="secondary" onClick={() => void flush()}>
              {t('sync.syncNow')}
            </Button>
          </div>
          <p className="text-sm text-slate-600" aria-live="polite">
            {items.length === 0 ? t('sync.allSyncedHint') : t('sync.waiting', { count: pending })}
          </p>
          {problems.length > 0 && <p className="mt-1 text-sm font-medium text-red-700">{t('sync.attention', { count: problems.length })}</p>}
          <ul className="mt-2 max-h-64 space-y-2 overflow-auto">
            {items.map((i) => (
              <li key={i.id} className="rounded-lg border border-slate-200 p-2 text-xs">
                <div className="font-medium text-slate-800">{i.label}</div>
                <div className="text-slate-500">{new Date(i.createdAt).toLocaleString()}</div>
                {i.status !== 'pending' && (
                  <>
                    <p className="mt-1 text-red-700">{i.status === 'conflict' ? t('sync.conflict') : t('sync.failed')}</p>
                    {i.error && <p className="mt-1 wrap-break-word text-slate-600">{i.error}</p>}
                    <div className="mt-1 flex gap-2">
                      <Button size="sm" variant="secondary" onClick={() => void retry(i.id)}>
                        {t('common.retry')}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => void discard(i.id)}>
                        {t('common.discard')}
                      </Button>
                    </div>
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0])
    .join('')
    .toUpperCase()
}

function AccountMenu() {
  const { t } = useTranslation()
  const { me, signOut } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const ref = useDismiss(open, () => setOpen(false))
  if (!me) return null
  const roles = [...new Set(me.roles.map((r) => r.role))]
  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label={t('nav.account')}
        className="flex items-center gap-2 rounded-full p-0.5 pe-2 hover:bg-slate-100"
      >
        <span aria-hidden className="grid h-8 w-8 place-items-center rounded-full bg-slate-200 text-xs font-semibold text-slate-700">
          {initials(me.display_name)}
        </span>
        <span className="hidden max-w-40 truncate text-sm font-medium text-slate-700 md:inline">{me.display_name}</span>
        <ChevronDown aria-hidden className="hidden h-4 w-4 text-slate-400 md:inline" />
      </button>
      {open && (
        <div className="absolute inset-e-0 z-40 mt-2 w-64 rounded-2xl border border-slate-200 bg-white p-2 shadow-raised">
          <div className="px-3 py-2">
            <div className="truncate font-medium text-slate-900">{me.display_name}</div>
            <div className="truncate text-sm text-slate-500">{me.email}</div>
            <div className="mt-2 flex flex-wrap gap-1">
              {roles.map((r) => (
                <Badge key={r}>{t(`users.roleNames.${r}`)}</Badge>
              ))}
            </div>
          </div>
          <div className="my-1 border-t border-slate-100" />
          <button
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-slate-700 hover:bg-slate-100"
            onClick={async () => {
              setOpen(false)
              if (await signOut()) navigate('/login')
            }}
          >
            <LogOut aria-hidden className="h-4 w-4 text-slate-400 rtl:rotate-180" />
            {t('common.signOut')}
          </button>
        </div>
      )}
    </div>
  )
}

export function OfflineBanner() {
  const { t } = useTranslation()
  const online = useOnline()
  if (online) return null
  return (
    <div role="status" className="flex items-center justify-center gap-2 bg-amber-50 px-4 py-2 text-center text-sm text-amber-900">
      <CloudOff aria-hidden className="h-4 w-4 shrink-0" />
      {t('sync.offlineBanner')}
    </div>
  )
}

function NavList({ groups, onNavigate }: { groups: NavGroup[]; onNavigate?: () => void }) {
  return (
    <div className="space-y-5">
      {groups.map((g, gi) => (
        <div key={g.label ?? gi}>
          {g.label && <h2 className="mb-1.5 px-3 text-xs font-semibold text-slate-400">{g.label}</h2>}
          <ul className="space-y-0.5">
            {g.items.map((n) => (
              <li key={n.to}>
                <NavLink
                  to={n.to}
                  end={n.end}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    cx(
                      'flex items-center gap-3 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors',
                      isActive ? 'bg-brand-soft text-brand' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900',
                    )
                  }
                >
                  <n.icon aria-hidden className="h-[18px] w-[18px] shrink-0" />
                  {n.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

function TabBar({ items, onMenu, menuOpen }: { items: NavItem[]; onMenu?: () => void; menuOpen: boolean }) {
  const { t } = useTranslation()
  const tab = 'flex flex-1 flex-col items-center gap-0.5 px-1 pb-1.5 pt-2 text-[11px] font-medium'
  return (
    <nav aria-label={t('nav.quick')} className="fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-white/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-sm lg:hidden">
      <ul className="mx-auto flex max-w-lg">
        {items.map((n) => (
          <li key={n.to} className="flex flex-1">
            <NavLink to={n.to} end={n.end} className={({ isActive }) => cx(tab, isActive ? 'text-brand' : 'text-slate-500')}>
              <n.icon aria-hidden className="h-5 w-5" />
              <span className="max-w-full truncate">{n.short ?? n.label}</span>
            </NavLink>
          </li>
        ))}
        {onMenu && (
          <li className="flex flex-1">
            <button onClick={onMenu} aria-expanded={menuOpen} className={cx(tab, menuOpen ? 'text-brand' : 'text-slate-500')}>
              <Menu aria-hidden className="h-5 w-5" />
              <span>{t('nav.menu')}</span>
            </button>
          </li>
        )}
      </ul>
    </nav>
  )
}

export function Shell({ groups, children }: { groups: NavGroup[]; children?: ReactNode }) {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const location = useLocation()
  const [menuOpen, setMenuOpen] = useState(false)
  useEffect(() => setMenuOpen(false), [location.pathname])
  const school = me ? (i18n.language === 'ar' && me.tenant.name_ar ? me.tenant.name_ar : me.tenant.name) : t('common.appName')
  const visible = groups.map((g) => ({ ...g, items: g.items.filter((n) => n.show) })).filter((g) => g.items.length > 0)
  const all = visible.flatMap((g) => g.items)
  // Phone tab bar: everything when it fits; otherwise home, three everyday tasks and Menu.
  const tabs = all.length <= 5 ? all : [all[0], ...all.slice(1).filter((n) => n.tab).slice(0, 3)]
  const needsMenu = tabs.length < all.length
  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:inset-s-2 focus:top-2 focus:z-50 focus:rounded-sm focus:bg-white focus:p-2">
        {t('common.skipToContent')}
      </a>
      <OfflineBanner />
      <header className="sticky top-0 z-30 border-b border-slate-200/80 bg-white/90 backdrop-blur-sm">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-3 px-4">
          <div className="flex min-w-0 items-center gap-2.5">
            <span aria-hidden className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand text-sm font-bold text-brand-ink">
              {initials(school).slice(0, 1) || 'S'}
            </span>
            <div className="min-w-0 leading-tight">
              <div className="truncate text-sm font-semibold text-slate-900">{school}</div>
              <div className="truncate text-xs text-slate-500">{t('common.appName')}</div>
            </div>
          </div>
          <div className="ms-auto flex shrink-0 items-center gap-1">
            <SyncStatus />
            <LanguageToggle />
            <AccountMenu />
          </div>
        </div>
      </header>
      <div className="mx-auto flex max-w-7xl gap-8 px-4 pb-28 pt-6 lg:pb-12">
        <nav aria-label="Main" className="sticky top-22 hidden max-h-[calc(100vh-6.5rem)] w-56 shrink-0 self-start overflow-y-auto pb-6 lg:block">
          <NavList groups={visible} />
        </nav>
        <main id="main" className="min-w-0 flex-1">
          {children ?? <Outlet />}
        </main>
      </div>
      {menuOpen && (
        <div className="fixed inset-0 z-40 overflow-y-auto bg-white px-4 pb-28 pt-4 lg:hidden" role="dialog" aria-modal="true" aria-label={t('nav.menu')}>
          <div className="mb-4 flex items-center justify-between">
            <span className="text-lg font-semibold text-slate-900">{t('nav.menu')}</span>
            <button className="rounded-lg p-2 text-slate-500 hover:bg-slate-100" onClick={() => setMenuOpen(false)} aria-label={t('common.close')}>
              <X aria-hidden className="h-5 w-5" />
            </button>
          </div>
          <NavList groups={visible} onNavigate={() => setMenuOpen(false)} />
        </div>
      )}
      <TabBar items={tabs} onMenu={needsMenu ? () => setMenuOpen((o) => !o) : undefined} menuOpen={menuOpen} />
    </div>
  )
}

export function StaffLayout() {
  const { t } = useTranslation()
  const { can, hasRole } = useAuth()
  const groups: NavGroup[] = [
    { items: [{ to: '/home', label: t('nav.home'), icon: House, show: true }] },
    {
      label: t('navGroups.record'),
      items: [
        { to: '/capture/attendance', label: t('nav.attendance'), short: t('nav.attendanceShort'), icon: ClipboardCheck, show: can('record_attendance'), tab: true },
        { to: '/capture/tag', label: t('nav.quickTag'), short: t('nav.quickTagShort'), icon: Sparkles, show: can('verify_skills'), tab: true },
        { to: '/capture/confirm', label: t('nav.confirmAwards'), icon: BadgeCheck, show: can('verify_skills') },
        { to: '/capture/result', label: t('nav.results'), icon: Medal, show: can('record_results') },
      ],
    },
    {
      label: t('navGroups.people'),
      items: [
        { to: '/squads', label: t('nav.squads'), icon: UsersRound, show: can('view_roster'), tab: true },
        { to: '/students', label: t('nav.students'), icon: GraduationCap, show: can('view_roster') },
        { to: '/flags', label: t('nav.flags'), icon: Flag, show: can('run_readiness') },
      ],
    },
    {
      label: t('navGroups.competitions'),
      items: [
        { to: '/competitions', label: t('nav.competitions'), icon: Trophy, show: can('view_roster') },
        { to: '/skills', label: t('nav.skills'), icon: BookOpen, show: can('view_roster') },
      ],
    },
    {
      label: t('navGroups.families'),
      items: [
        { to: '/messages', label: t('nav.messages'), short: t('nav.messagesShort'), icon: MessageSquare, show: can('draft_messages'), tab: true },
        { to: '/insights', label: t('nav.insights'), icon: NotebookPen, show: can('review_insights') },
        { to: '/templates', label: t('nav.templates'), icon: FileText, show: can('approve_templates') },
        { to: '/emergency', label: t('nav.emergency'), icon: Siren, show: can('emergency_broadcast') },
      ],
    },
    {
      label: t('navGroups.programme'),
      items: [
        { to: '/reports', label: t('nav.reports'), icon: ChartColumn, show: can('generate_reports'), tab: true },
        { to: '/inventory', label: t('nav.inventory'), icon: Package, show: can('view_inventory') },
        { to: '/budget', label: t('nav.budget'), icon: Wallet, show: can('view_budget') },
      ],
    },
    {
      label: t('navGroups.setup'),
      items: [
        { to: '/imports', label: t('nav.imports'), icon: Upload, show: can('manage_imports') },
        { to: '/users', label: t('nav.users'), icon: UserCog, show: can('manage_users') },
        { to: '/settings', label: t('nav.settings'), icon: Settings, show: can('manage_settings') },
        { to: '/audit', label: t('nav.audit'), icon: History, show: can('view_audit_own') },
      ],
    },
    { label: t('navGroups.family'), items: [{ to: '/portal', label: t('nav.portal'), icon: HeartHandshake, show: hasRole('parent'), end: true }] },
  ]
  return <Shell groups={groups} />
}

export function PortalLayout() {
  const { t } = useTranslation()
  const { hasRole, isStaff, me } = useAuth()
  const groups: NavGroup[] = [
    {
      items: [
        { to: '/portal', label: t('nav.myChildren'), short: t('nav.myChildrenShort'), icon: House, show: hasRole('parent'), end: true },
        { to: '/portal/messages', label: t('nav.inbox'), icon: Inbox, show: hasRole('parent'), tab: true },
        { to: '/portal/preferences', label: t('nav.preferences'), short: t('nav.preferencesShort'), icon: SlidersHorizontal, show: hasRole('parent'), tab: true },
        { to: '/me', label: t('nav.mySkills'), icon: Sparkles, show: !!me?.student_portal, tab: true },
        { to: '/home', label: t('nav.staffView'), short: t('nav.staffViewShort'), icon: ClipboardCheck, show: isStaff, tab: true },
      ],
    },
  ]
  return <Shell groups={groups} />
}
