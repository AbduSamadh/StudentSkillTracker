import { useEffect, useState } from 'react'

import type { QueuedWrite } from './db'
import { list, subscribe } from './queue'

export function useOnline(): boolean {
  const [online, setOnline] = useState(typeof navigator === 'undefined' ? true : navigator.onLine)
  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])
  return online
}

export function useOutbox(): QueuedWrite[] {
  const [items, setItems] = useState<QueuedWrite[]>([])
  useEffect(() => {
    let alive = true
    const load = () => void list().then((x) => alive && setItems(x))
    load()
    const unsub = subscribe(load)
    return () => {
      alive = false
      unsub()
    }
  }, [])
  return items
}
