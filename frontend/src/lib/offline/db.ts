import { openDB, type DBSchema, type IDBPDatabase } from 'idb'

export interface QueuedWrite {
  id: string
  method: 'POST' | 'PATCH' | 'PUT'
  path: string
  body: unknown
  label: string
  createdAt: number // strictly increasing per device; the order writes are replayed in
  attempts: number
  status: 'pending' | 'failed' | 'conflict'
  error?: string
}

interface StemDB extends DBSchema {
  outbox: { key: string; value: QueuedWrite; indexes: { byCreated: number } }
  kv: { key: string; value: string }
}

let dbPromise: Promise<IDBPDatabase<StemDB>> | null = null

export function db(): Promise<IDBPDatabase<StemDB>> {
  if (!dbPromise) {
    dbPromise = openDB<StemDB>('stemtrack', 1, {
      upgrade(d) {
        const outbox = d.createObjectStore('outbox', { keyPath: 'id' })
        outbox.createIndex('byCreated', 'createdAt')
        d.createObjectStore('kv')
      },
    })
  }
  return dbPromise
}

/** Storage adapter for TanStack Query persistence (capture screens work offline). */
export const idbStorage = {
  getItem: async (key: string): Promise<string | null> => (await (await db()).get('kv', key)) ?? null,
  setItem: async (key: string, value: string): Promise<void> => {
    await (await db()).put('kv', value, key)
  },
  removeItem: async (key: string): Promise<void> => {
    await (await db()).delete('kv', key)
  },
}

export async function clearCachedData(): Promise<void> {
  await (await db()).clear('kv')
}
