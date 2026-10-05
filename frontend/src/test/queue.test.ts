import { beforeEach, describe, expect, it, vi } from 'vitest'

import { setAccessToken } from '@/lib/api'
import { db } from '@/lib/offline/db'
import { capture, flush, list } from '@/lib/offline/queue'

const ok = (body: unknown = {}) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })

describe('offline outbox', () => {
  beforeEach(async () => {
    await (await db()).clear('outbox')
    setAccessToken('test-token')
    vi.restoreAllMocks()
  })

  it('keeps writes on the device while offline and replays them in order', async () => {
    const calls: string[] = []
    let online = false
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (!online) throw new TypeError('Failed to fetch')
      calls.push(String(input))
      return ok()
    })
    const r1 = await capture({ method: 'POST', path: '/sessions', body: { id: 's1' }, label: 'session' })
    const r2 = await capture({ method: 'POST', path: '/sessions/s1/attendance', body: { records: [] }, label: 'attendance' })
    expect(r1.offline && r2.offline).toBe(true)
    expect((await list()).map((i) => i.label)).toEqual(['session', 'attendance'])

    online = true
    const r = await flush()
    expect(r).toMatchObject({ sent: 2, failed: 0, remaining: 0 })
    expect(calls).toEqual(['/api/v1/sessions', '/api/v1/sessions/s1/attendance'])
  })

  it('stops at the first network failure so later writes never overtake earlier ones', async () => {
    let n = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      n += 1
      if (n === 2) throw new TypeError('Failed to fetch')
      return ok()
    })
    await (await db()).clear('outbox')
    await capture({ method: 'POST', path: '/a', body: {}, label: 'a' })
    // second capture: 'b' fails on network, 'c' must not be attempted before it
    vi.spyOn(globalThis, 'fetch').mockImplementationOnce(async () => {
      throw new TypeError('Failed to fetch')
    })
    await capture({ method: 'POST', path: '/b', body: {}, label: 'b' })
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      throw new TypeError('Failed to fetch')
    })
    await capture({ method: 'POST', path: '/c', body: {}, label: 'c' })
    expect(fetchSpy).toHaveBeenCalledTimes(1) // only 'b' was retried, then the flush stopped
    expect((await list()).map((i) => i.label)).toEqual(['b', 'c'])
  })

  it('marks a conflict for review instead of retrying forever', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: 'released to parents' }), { status: 409 }))
    await capture({ method: 'PATCH', path: '/results/x', body: {}, label: 'edit result' })
    const [item] = await list()
    expect(item.status).toBe('conflict')
    expect(item.error).toContain('released to parents')
  })
})
