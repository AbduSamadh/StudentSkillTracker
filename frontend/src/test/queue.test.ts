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
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(ok())
    await capture({ method: 'POST', path: '/a', body: {}, label: 'a' })
    fetchSpy.mockRejectedValue(new TypeError('Failed to fetch'))
    await capture({ method: 'POST', path: '/b', body: {}, label: 'b' })
    fetchSpy.mockClear()
    // 'b' is retried first and fails again, so 'c' must not be attempted before it
    await capture({ method: 'POST', path: '/c', body: {}, label: 'c' })
    expect(fetchSpy).toHaveBeenCalledTimes(1)
    expect(String(fetchSpy.mock.calls[0][0])).toBe('/api/v1/b')
    expect((await list()).map((i) => i.label)).toEqual(['b', 'c'])
  })

  it('replays writes captured in the same millisecond in the order they were made', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000)
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    const labels = Array.from({ length: 12 }, (_, i) => `w${i}`)
    for (const label of labels) await capture({ method: 'POST', path: `/${label}`, body: {}, label })
    expect((await list()).map((i) => i.label)).toEqual(labels)

    fetchSpy.mockClear()
    fetchSpy.mockImplementation(async () => ok())
    await flush()
    expect(fetchSpy.mock.calls.map((c) => String(c[0]))).toEqual(labels.map((l) => `/api/v1/${l}`))
  })

  it('marks a conflict for review instead of retrying forever', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: 'released to parents' }), { status: 409 }))
    await capture({ method: 'PATCH', path: '/results/x', body: {}, label: 'edit result' })
    const [item] = await list()
    expect(item.status).toBe('conflict')
    expect(item.error).toContain('released to parents')
  })
})
