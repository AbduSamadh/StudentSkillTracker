import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { api, hasToken, onAuthChange, refreshSession, setAccessToken } from './api'
import { hexToTriplet } from './format'
import { setLanguage } from './i18n'
import { clearCachedData } from './offline/db'
import { list as listOutbox } from './offline/queue'
import type { Me, Role } from './types'

interface AuthState {
  me: Me | undefined
  ready: boolean
  authed: boolean
  can: (cap: string) => boolean
  hasRole: (role: Role) => boolean
  isStaff: boolean
  signInWithToken: (token: string) => Promise<void>
  signOut: () => Promise<boolean>
}

const Ctx = createContext<AuthState | null>(null)
export const TENANT_KEY = 'stem.tenant'
const SESSION_HINT = 'stem.session' // a flag only; the session itself is the httpOnly cookie

export function rememberTenant(slug: string): void {
  try {
    localStorage.setItem(TENANT_KEY, slug)
  } catch {
    /* ignore */
  }
}

export function lastTenant(): string {
  try {
    return localStorage.getItem(TENANT_KEY) ?? ''
  } catch {
    return ''
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [authed, setAuthed] = useState(hasToken())
  const [bootstrapped, setBootstrapped] = useState(false)

  useEffect(() => onAuthChange(setAuthed), [])
  useEffect(() => {
    // On load, resume the session from the httpOnly refresh cookie if one was started here.
    if (localStorage.getItem(SESSION_HINT)) void refreshSession().finally(() => setBootstrapped(true))
    else setBootstrapped(true)
  }, [])

  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: () => api<Me>('/auth/me'),
    enabled: authed,
    staleTime: 5 * 60_000,
    meta: { persist: true },
  })
  const me = meQuery.data

  useEffect(() => {
    if (!me) return
    const root = document.documentElement
    root.style.setProperty('--brand', hexToTriplet(me.tenant.branding.primary))
    root.style.setProperty('--accent', hexToTriplet(me.tenant.branding.accent))
  }, [me])

  useEffect(() => {
    if (me?.locale && !localStorage.getItem('stem.lang')) setLanguage(me.locale)
  }, [me?.locale])

  const signInWithToken = useCallback(
    async (token: string) => {
      setAccessToken(token)
      localStorage.setItem(SESSION_HINT, '1')
      await qc.invalidateQueries({ queryKey: ['me'] })
    },
    [qc],
  )

  const signOut = useCallback(async () => {
    const pending = (await listOutbox()).length
    if (pending > 0 && !window.confirm(`${pending} change(s) have not synced yet and will be lost. Sign out anyway?`)) {
      return false
    }
    try {
      await api('/auth/logout', { method: 'POST' })
    } catch {
      /* already signed out */
    }
    setAccessToken(null)
    localStorage.removeItem(SESSION_HINT)
    qc.clear()
    await clearCachedData() // no student data left on a shared device
    return true
  }, [qc])

  const value = useMemo<AuthState>(() => {
    const caps = new Set(me?.capabilities ?? [])
    const roles = new Set(me?.roles.map((r) => r.role) ?? [])
    return {
      me,
      ready: bootstrapped && (!authed || !meQuery.isLoading),
      authed: authed && !!me,
      can: (c) => caps.has(c),
      hasRole: (r) => roles.has(r),
      isStaff: roles.has('teacher') || roles.has('programme_admin') || roles.has('leader'),
      signInWithToken,
      signOut,
    }
  }, [me, bootstrapped, authed, meQuery.isLoading, signInWithToken, signOut])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth(): AuthState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useAuth outside AuthProvider')
  return v
}
