// Write-ahead outbox for capture screens (attendance, results, skill tagging).
//
// Every capture write is stored in IndexedDB *before* any network attempt, then flushed
// in order. Nothing is lost if the phone has no signal, the tab is closed, or a response
// is dropped: the server de-duplicates replays by idempotency key and returns the
// canonical record (see backend app/api/v1/capture.py, "Sync contract").

import { ApiError, NetworkError, api, hasToken, refreshSession } from '../api'
import { db, type QueuedWrite } from './db'

type Listener = () => void
const listeners = new Set<Listener>()
let flushing: Promise<FlushResult> | null = null

export interface FlushResult {
  sent: number
  failed: number
  remaining: number
  offline: boolean
}

export function newId(): string {
  return crypto.randomUUID()
}

export function subscribe(fn: Listener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function notify(): void {
  listeners.forEach((l) => l())
}

export async function list(): Promise<QueuedWrite[]> {
  return (await db()).getAllFromIndex('outbox', 'byCreated')
}

export async function enqueue(w: Pick<QueuedWrite, 'method' | 'path' | 'body' | 'label'>): Promise<QueuedWrite> {
  const item: QueuedWrite = { ...w, id: newId(), createdAt: Date.now(), attempts: 0, status: 'pending' }
  await (await db()).put('outbox', item)
  notify()
  return item
}

export async function discard(id: string): Promise<void> {
  await (await db()).delete('outbox', id)
  notify()
}

export async function retry(id: string): Promise<void> {
  const d = await db()
  const item = await d.get('outbox', id)
  if (item) {
    await d.put('outbox', { ...item, status: 'pending', error: undefined })
    notify()
  }
  void flush()
}

/** Store a capture write, then try to send everything queued. */
export async function capture(w: Pick<QueuedWrite, 'method' | 'path' | 'body' | 'label'>): Promise<FlushResult> {
  await enqueue(w)
  return flush()
}

export function flush(): Promise<FlushResult> {
  if (!flushing) {
    flushing = doFlush().finally(() => {
      flushing = null
    })
  }
  return flushing
}

async function doFlush(): Promise<FlushResult> {
  const d = await db()
  let sent = 0
  let failed = 0
  let offline = false
  if (typeof navigator !== 'undefined' && navigator.onLine === false) offline = true
  const pending = (await list()).filter((i) => i.status === 'pending')
  if (pending.length === 0) {
    notify()
    return { sent, failed, remaining: (await list()).length, offline }
  }
  if (!offline && !hasToken()) {
    // e.g. the app was reopened at the venue: get a fresh access token from the cookie.
    if (!(await refreshSession())) offline = true
  }
  if (!offline) {
    for (const item of pending) {
      try {
        await api(item.path, { method: item.method, body: item.body })
        await d.delete('outbox', item.id)
        sent++
      } catch (e) {
        if (e instanceof NetworkError || (e instanceof ApiError && e.status >= 500) || (e instanceof ApiError && e.status === 401)) {
          await d.put('outbox', { ...item, attempts: item.attempts + 1, error: (e as Error).message })
          offline = true
          break // keep order: later writes may depend on this one
        }
        const status = e instanceof ApiError && e.status === 409 ? 'conflict' : 'failed'
        const detail = e instanceof ApiError ? JSON.stringify(e.detail) : String(e)
        await d.put('outbox', { ...item, attempts: item.attempts + 1, status, error: detail })
        failed++
      }
    }
  }
  notify()
  const remaining = (await list()).length
  return { sent, failed, remaining, offline }
}

let started = false

/** Flush on reconnect, when the app regains focus, and every 30 seconds. */
export function startBackgroundSync(): void {
  if (started || typeof window === 'undefined') return
  started = true
  window.addEventListener('online', () => void flush())
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void flush()
  })
  setInterval(() => void flush(), 30_000)
  void flush()
}
