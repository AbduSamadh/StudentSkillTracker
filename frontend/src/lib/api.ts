// Thin fetch wrapper. The access token lives in memory only; the refresh token is an
// httpOnly cookie that JavaScript never sees. A 401 triggers one refresh attempt.

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : (detail as { message?: string })?.message ?? `HTTP ${status}`)
    this.status = status
    this.detail = detail
  }
}

export class NetworkError extends Error {}

const BASE = '/api/v1'
let accessToken: string | null = null
let refreshing: Promise<boolean> | null = null
const listeners = new Set<(authed: boolean) => void>()

export function setAccessToken(token: string | null): void {
  accessToken = token
  listeners.forEach((l) => l(token !== null))
}

export function hasToken(): boolean {
  return accessToken !== null
}

export function onAuthChange(fn: (authed: boolean) => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export async function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const r = await fetch(`${BASE}/auth/refresh`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'X-Requested-With': 'stemtrack' },
        })
        if (!r.ok) return false
        const body = await r.json()
        setAccessToken(body.access_token)
        return true
      } catch {
        return false
      } finally {
        setTimeout(() => (refreshing = null), 0)
      }
    })()
  }
  return refreshing
}

export interface RequestOptions {
  method?: string
  body?: unknown
  form?: FormData
  headers?: Record<string, string>
  raw?: boolean
}

export async function api<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  const doFetch = async (): Promise<Response> => {
    const headers: Record<string, string> = { ...(opts.headers ?? {}) }
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`
    let body: BodyInit | undefined
    if (opts.form) body = opts.form
    else if (opts.body !== undefined) {
      headers['Content-Type'] = 'application/json'
      body = JSON.stringify(opts.body)
    }
    try {
      return await fetch(`${BASE}${path}`, { method: opts.method ?? 'GET', headers, body, credentials: 'same-origin' })
    } catch (e) {
      throw new NetworkError((e as Error).message)
    }
  }
  let res = await doFetch()
  if (res.status === 401 && !path.startsWith('/auth/')) {
    if (await refreshSession()) res = await doFetch()
  }
  if (opts.raw) {
    if (!res.ok) throw new ApiError(res.status, await safeJson(res))
    return res as unknown as T
  }
  if (res.status === 204) return undefined as T
  const data = await safeJson(res)
  if (!res.ok) throw new ApiError(res.status, (data as { detail?: unknown })?.detail ?? data)
  return data as T
}

async function safeJson(res: Response): Promise<unknown> {
  const text = await res.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

export async function downloadBlob(path: string, fallbackName: string): Promise<void> {
  const res = await api<Response>(path, { raw: true })
  const blob = await res.blob()
  const cd = res.headers.get('Content-Disposition') ?? ''
  const name = /filename="([^"]+)"/.exec(cd)?.[1] ?? fallbackName
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    const d = e.detail as unknown
    if (typeof d === 'string') return d
    if (Array.isArray(d)) return d.map((x: { msg?: string; loc?: unknown[] }) => `${(x.loc ?? []).slice(1).join('.')}: ${x.msg}`).join('; ')
    if (d && typeof d === 'object' && 'message' in d) return String((d as { message: string }).message)
    return `Request failed (${e.status})`
  }
  if (e instanceof NetworkError) return 'You appear to be offline.'
  return (e as Error)?.message ?? 'Something went wrong'
}
