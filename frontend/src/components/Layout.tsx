import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'

import { useAuth } from '@/lib/auth'
import { setLanguage, type Lang } from '@/lib/i18n'
import { useOnline, useOutbox } from '@/lib/offline/hooks'
import { discard, flush, retry } from '@/lib/offline/queue'

import { Badge, Button, cx } from './ui'

interface NavItem {
  to: string
  label: string
  show: boolean
}

export function LanguageToggle() {
  const { i18n } = useTranslation()
  const next: Lang = i18n.language === 'ar' ? 'en' : 'ar'
  return (
    <button
      onClick={() => setLanguage(next)}
      className="rounded-lg border border-slate-300 px-2.5 py-1 text-sm font-medium text-slate-700 hover:bg-slate-50"
      lang={next}
      aria-label={next === 'ar' ? 'التبديل إلى العربية' : 'Switch to English'}
    >
      {next === 'ar' ? 'العربية' : 'English'}
    </button>
  )
}

function SyncStatus() {
  const { t } = useTranslation()
  const online = useOnline()
  const items = useOutbox()
  const [open, setOpen] = useState(false)
  const pending = items.filter((i) => i.status === 'pending').length
  const problems = items.filter((i) => i.status !== 'pending')
  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex items-center gap-2 rounded-lg border border-slate-300 px-2.5 py-1 text-sm"
      >
        <span aria-hidden className={cx('h-2.5 w-2.5 rounded-full', online ? 'bg-emerald-500' : 'bg-amber-500')} />
        <span className="sr-only sm:not-sr-only">{online ? t('common.online') : t('common.offline')}</span>
        {pending > 0 && <Badge tone="amber">{pending}</Badge>}
        {problems.length > 0 && <Badge tone="red">{problems.length}</Badge>}
      </button>
      {open && (
        <div className="absolute inset-e-0 z-40 mt-2 w-80 rounded-xl border border-slate-200 bg-white p-3 shadow-lg">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm">{t('sync.title')}</h2>
            <Button size="sm" variant="secondary" onClick={() => void flush()}>
              {t('sync.syncNow')}
            </Button>
          </div>
          <p className="text-sm text-slate-600" aria-live="polite">
            {items.length === 0 ? t('sync.allSynced') : t('sync.waiting', { count: pending })}
          </p>
          {problems.length > 0 && <p className="text-sm font-medium text-red-700">{t('sync.attention', { count: problems.length })}</p>}
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

export function OfflineBanner() {
  const { t } = useTranslation()
  const online = useOnline()
  if (online) return null
  return (
    <div role="status" className="bg-amber-100 px-4 py-2 text-center text-sm text-amber-900">
      {t('sync.offlineBanner')}
    </div>
  )
}

export function Shell({ nav, children }: { nav: NavItem[]; children?: ReactNode }) {
  const { t, i18n } = useTranslation()
  const { me, signOut } = useAuth()
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)
  const school = me ? (i18n.language === 'ar' && me.tenant.name_ar ? me.tenant.name_ar : me.tenant.name) : t('common.appName')
  const items = nav.filter((n) => n.show)
  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:inset-s-2 focus:top-2 focus:z-50 focus:rounded-sm focus:bg-white focus:p-2">
        {t('common.skipToContent')}
      </a>
      <OfflineBanner />
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur-sm">
        <div className="mx-auto flex max-w-7xl items-center gap-3 px-4 py-2">
          <button className="rounded-lg p-2 lg:hidden" aria-label={t('nav.menu')} aria-expanded={menuOpen} onClick={() => setMenuOpen((o) => !o)}>
            <span aria-hidden>☰</span>
          </button>
          <div className="flex items-center gap-2">
            <span aria-hidden className="grid h-8 w-8 place-items-center rounded-lg bg-brand font-bold text-brand-ink">S</span>
            <div className="leading-tight">
              <div className="text-sm font-semibold">{school}</div>
              <div className="text-xs text-slate-500">{t('common.appName')}</div>
            </div>
          </div>
          <div className="ms-auto flex items-center gap-2">
            <SyncStatus />
            <LanguageToggle />
            {me && (
              <Button
                variant="ghost"
                size="sm"
                onClick={async () => {
                  if (await signOut()) navigate('/login')
                }}
              >
                {t('common.signOut')}
              </Button>
            )}
          </div>
        </div>
      </header>
      <div className="mx-auto flex max-w-7xl gap-6 px-4 py-6">
        <nav aria-label="Main" className={cx('w-52 shrink-0 lg:block', menuOpen ? 'fixed inset-0 z-40 block w-full bg-white p-4 lg:static lg:w-52 lg:p-0' : 'hidden')}>
          {menuOpen && (
            <button className="mb-3 lg:hidden" onClick={() => setMenuOpen(false)} aria-label={t('common.close')}>
              ✕
            </button>
          )}
          <ul className="space-y-0.5">
            {items.map((n) => (
              <li key={n.to}>
                <NavLink
                  to={n.to}
                  onClick={() => setMenuOpen(false)}
                  className={({ isActive }) =>
                    cx('block rounded-lg px-3 py-2 text-sm font-medium', isActive ? 'bg-brand-soft text-brand' : 'text-slate-700 hover:bg-slate-100')
                  }
                >
                  {n.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <main id="main" className="min-w-0 flex-1">
          {children ?? <Outlet />}
        </main>
      </div>
    </div>
  )
}

export function StaffLayout() {
  const { t } = useTranslation()
  const { can, hasRole } = useAuth()
  const nav: NavItem[] = [
    { to: '/dashboard', label: t('nav.dashboard'), show: can('view_school_analytics') },
    { to: '/squads', label: t('nav.squads'), show: can('view_roster') },
    { to: '/capture/attendance', label: t('nav.attendance'), show: can('record_attendance') },
    { to: '/capture/result', label: t('nav.results'), show: can('record_results') },
    { to: '/capture/tag', label: t('nav.quickTag'), show: can('verify_skills') },
    { to: '/capture/confirm', label: t('nav.confirmAwards'), show: can('verify_skills') },
    { to: '/students', label: t('nav.students'), show: can('view_roster') },
    { to: '/competitions', label: t('nav.competitions'), show: can('view_roster') },
    { to: '/skills', label: t('nav.skills'), show: can('view_roster') },
    { to: '/flags', label: t('nav.flags'), show: can('run_readiness') },
    { to: '/insights', label: t('nav.insights'), show: can('review_insights') },
    { to: '/messages', label: t('nav.messages'), show: can('draft_messages') },
    { to: '/templates', label: t('nav.templates'), show: can('approve_templates') },
    { to: '/emergency', label: t('nav.emergency'), show: can('emergency_broadcast') },
    { to: '/reports', label: t('nav.reports'), show: can('generate_reports') },
    { to: '/inventory', label: t('nav.inventory'), show: can('view_inventory') },
    { to: '/budget', label: t('nav.budget'), show: can('view_budget') },
    { to: '/imports', label: t('nav.imports'), show: can('manage_imports') },
    { to: '/audit', label: t('nav.audit'), show: can('view_audit_own') },
    { to: '/users', label: t('nav.users'), show: can('manage_users') },
    { to: '/settings', label: t('nav.settings'), show: can('manage_settings') },
    { to: '/portal', label: t('nav.portal'), show: hasRole('parent') },
  ]
  return <Shell nav={nav} />
}

export function PortalLayout() {
  const { t } = useTranslation()
  const { hasRole, isStaff, me } = useAuth()
  const nav: NavItem[] = [
    { to: '/portal', label: t('nav.myChildren'), show: hasRole('parent') },
    { to: '/portal/messages', label: t('nav.inbox'), show: hasRole('parent') },
    { to: '/portal/preferences', label: t('nav.preferences'), show: hasRole('parent') },
    { to: '/me', label: t('nav.mySkills'), show: !!me?.student_portal },
    { to: '/', label: t('nav.dashboard'), show: isStaff },
  ]
  return <Shell nav={nav} />
}
