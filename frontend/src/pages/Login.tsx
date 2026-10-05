import { useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { LanguageToggle } from '@/components/Layout'
import { Alert, Button, TextInput } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { lastTenant, rememberTenant, useAuth } from '@/lib/auth'

interface TokenOut {
  status: 'ok' | 'mfa_required' | 'mfa_enrolment_required'
  access_token: string | null
  login_token: string | null
}

export function AuthFrame({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen place-items-center bg-linear-to-b from-brand-soft to-slate-50 p-4">
      <div className="w-full max-w-md">
        <div className="mb-4 flex justify-end">
          <LanguageToggle />
        </div>
        <div className="card p-6">
          <div className="mb-5 flex items-center gap-3">
            <span aria-hidden className="grid h-10 w-10 place-items-center rounded-xl bg-brand text-lg font-bold text-brand-ink">S</span>
            <h1 className="text-xl">{title}</h1>
          </div>
          {children}
        </div>
      </div>
    </div>
  )
}

/** Shared second-factor step for password and SSO sign-in. */
export function useMfaStep(onDone: (token: string) => Promise<void>) {
  const [loginToken, setLoginToken] = useState<string | null>(null)
  const [enrol, setEnrol] = useState<{ secret: string; otpauth_uri: string } | null>(null)
  const handle = async (out: TokenOut) => {
    if (out.status === 'ok' && out.access_token) return onDone(out.access_token)
    setLoginToken(out.login_token)
    if (out.status === 'mfa_enrolment_required' && out.login_token) {
      setEnrol(await api('/auth/mfa/enrol', { method: 'POST', body: { token: out.login_token } }))
    }
  }
  const verify = async (code: string) => {
    const out = await api<TokenOut>('/auth/mfa/verify', { method: 'POST', body: { login_token: loginToken, code } })
    if (out.access_token) await onDone(out.access_token)
  }
  return { needed: loginToken !== null, enrol, handle, verify }
}

function MfaForm({ enrol, verify }: { enrol: { secret: string; otpauth_uri: string } | null; verify: (code: string) => Promise<void> }) {
  const { t } = useTranslation()
  const [code, setCode] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setErr(null)
    try {
      await verify(code)
    } catch (ex) {
      setErr(errorMessage(ex))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form onSubmit={submit} className="space-y-4">
      <h2>{enrol ? t('auth.enrolTitle') : t('auth.mfaTitle')}</h2>
      {enrol ? (
        <div className="space-y-2 text-sm">
          <p>{t('auth.enrolPrompt')}</p>
          <p className="font-medium">{t('auth.setupKey')}</p>
          <code className="block select-all break-all rounded-sm bg-slate-100 p-2 font-mono text-base" dir="ltr">
            {enrol.secret}
          </code>
          <a className="text-brand underline" href={enrol.otpauth_uri}>
            {t('auth.openAuthenticator')}
          </a>
        </div>
      ) : (
        <p className="text-sm text-slate-600">{t('auth.mfaPrompt')}</p>
      )}
      <TextInput label={t('auth.code')} inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={code} onChange={(e) => setCode(e.target.value)} dir="ltr" />
      {err && <Alert tone="error">{err}</Alert>}
      <Button type="submit" busy={busy} className="w-full">
        {t('auth.verify')}
      </Button>
    </form>
  )
}

export default function Login() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { signInWithToken } = useAuth()
  const [tenant, setTenant] = useState(lastTenant() || 'demo')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [sso, setSso] = useState(false)
  const done = async (token: string) => {
    await signInWithToken(token)
    navigate('/')
  }
  const mfa = useMfaStep(done)

  useEffect(() => {
    if (!tenant) return
    const id = window.setTimeout(() => {
      api<{ sso_enabled: boolean }>(`/auth/tenant/${encodeURIComponent(tenant)}`)
        .then((r) => setSso(r.sso_enabled))
        .catch(() => setSso(false))
    }, 300)
    return () => window.clearTimeout(id)
  }, [tenant])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setErr(null)
    try {
      rememberTenant(tenant)
      await mfa.handle(await api<TokenOut>('/auth/password-login', { method: 'POST', body: { tenant, email, password } }))
    } catch (ex) {
      setErr(errorMessage(ex))
    } finally {
      setBusy(false)
    }
  }

  const startSso = async () => {
    rememberTenant(tenant)
    const r = await api<{ authorization_url: string }>(`/auth/oidc/start?tenant=${encodeURIComponent(tenant)}`)
    window.location.assign(r.authorization_url)
  }

  return (
    <AuthFrame title={t('auth.title')}>
      {mfa.needed ? (
        <MfaForm enrol={mfa.enrol} verify={mfa.verify} />
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <TextInput label={t('auth.school')} value={tenant} onChange={(e) => setTenant(e.target.value.trim())} required autoComplete="organization" dir="ltr" />
          {sso && (
            <>
              <Button type="button" variant="secondary" className="w-full" onClick={() => void startSso()}>
                {t('auth.sso')}
              </Button>
              <p className="text-center text-xs text-slate-500">{t('auth.or')}</p>
            </>
          )}
          <TextInput label={t('auth.email')} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" dir="ltr" />
          <TextInput label={t('auth.password')} type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" dir="ltr" />
          {err && <Alert tone="error">{err}</Alert>}
          <Button type="submit" busy={busy} className="w-full">
            {t('auth.signIn')}
          </Button>
          <p className="text-center text-sm">
            <Link to="/portal/login" className="text-brand underline">
              {t('auth.parentLink')}
            </Link>
          </p>
        </form>
      )}
    </AuthFrame>
  )
}

export function AuthCallback() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { signInWithToken } = useAuth()
  const [err, setErr] = useState<string | null>(null)
  const mfa = useMfaStep(async (token) => {
    await signInWithToken(token)
    navigate('/')
  })
  useEffect(() => {
    const code = params.get('code')
    const state = params.get('state')
    if (!code || !state) return
    api<TokenOut>('/auth/login', { method: 'POST', body: { code, state } })
      .then(mfa.handle)
      .catch((e) => setErr(errorMessage(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return (
    <AuthFrame title={t('auth.title')}>
      {err ? <Alert tone="error">{err}</Alert> : mfa.needed ? <MfaForm enrol={mfa.enrol} verify={mfa.verify} /> : <p>{t('auth.callback')}</p>}
    </AuthFrame>
  )
}

export function ParentLogin() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { signInWithToken } = useAuth()
  const [tenant, setTenant] = useState(lastTenant() || 'demo')
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const token = params.get('token')

  useEffect(() => {
    if (!token) return
    api<TokenOut>('/auth/parent/verify', { method: 'POST', body: { token } })
      .then(async (out) => {
        if (out.access_token) {
          await signInWithToken(out.access_token)
          navigate('/portal')
        }
      })
      .catch(() => setErr(t('parentAuth.expired')))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    rememberTenant(tenant)
    try {
      await api('/auth/parent/request-link', { method: 'POST', body: { tenant, email } })
      setSent(true)
    } catch (ex) {
      setErr(errorMessage(ex))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthFrame title={t('parentAuth.title')}>
      {token && !err ? (
        <p>{t('parentAuth.verifying')}</p>
      ) : sent ? (
        <Alert tone="success">{t('parentAuth.sent')}</Alert>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <p className="text-sm text-slate-600">{t('parentAuth.prompt')}</p>
          <TextInput label={t('auth.school')} value={tenant} onChange={(e) => setTenant(e.target.value.trim())} required dir="ltr" />
          <TextInput label={t('auth.email')} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" dir="ltr" />
          {err && <Alert tone="error">{err}</Alert>}
          <Button type="submit" busy={busy} className="w-full">
            {t('parentAuth.send')}
          </Button>
          <p className="text-center text-sm">
            <Link to="/login" className="text-brand underline">
              {t('auth.staffLink')}
            </Link>
          </p>
        </form>
      )}
    </AuthFrame>
  )
}

export function OptOutPage() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const [state, setState] = useState<'ask' | 'done' | 'error'>('ask')
  const [category, setCategory] = useState('')
  const confirm = async () => {
    try {
      const r = await api<{ category: string }>('/portal/opt-out-link', { method: 'POST', body: { token: params.get('token') } })
      setCategory(r.category)
      setState('done')
    } catch {
      setState('error')
    }
  }
  return (
    <AuthFrame title={t('portal.optOutTitle')}>
      {state === 'ask' && (
        <div className="space-y-4">
          <p>{t('portal.optOutConfirm')}</p>
          <Button onClick={() => void confirm()} className="w-full">
            {t('portal.stop')}
          </Button>
        </div>
      )}
      {state === 'done' && (
        <Alert tone="success">
          <strong>{t(`messageTypes.${category}` as 'messageTypes.logistics')}</strong> — {t('portal.optOutDone')}
        </Alert>
      )}
      {state === 'error' && <Alert tone="error">{t('parentAuth.expired')}</Alert>}
    </AuthFrame>
  )
}
