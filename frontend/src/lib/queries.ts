import { useQuery } from '@tanstack/react-query'

import { api } from './api'
import type { Competition, Edition, Skill, Squad, SquadDetail } from './types'

// Queries marked persist:true are kept in IndexedDB so capture screens open offline.
const persist = { persist: true }

export function useSquads() {
  return useQuery({ queryKey: ['squads'], queryFn: () => api<Squad[]>('/squads'), meta: persist, staleTime: 60_000 })
}

export function useSquad(id: string | undefined) {
  return useQuery({
    queryKey: ['squad', id],
    queryFn: () => api<SquadDetail>(`/squads/${id}`),
    enabled: !!id,
    meta: persist,
    staleTime: 60_000,
  })
}

export function useSkills() {
  return useQuery({ queryKey: ['skills'], queryFn: () => api<Skill[]>('/skills'), meta: persist, staleTime: 60 * 60_000 })
}

export function useEditions(upcoming = false) {
  return useQuery({
    queryKey: ['editions', upcoming],
    queryFn: () => api<Edition[]>(`/editions${upcoming ? '?upcoming=true' : ''}`),
    meta: persist,
    staleTime: 5 * 60_000,
  })
}

export function useCompetitions() {
  return useQuery({ queryKey: ['competitions'], queryFn: () => api<Competition[]>('/competitions') })
}

export function useStaffDirectory() {
  const q = useQuery({
    queryKey: ['staff-directory'],
    queryFn: () => api<{ id: string; display_name: string }[]>('/admin/directory'),
    staleTime: 30 * 60_000,
    meta: persist,
  })
  const names = new Map((q.data ?? []).map((s) => [s.id, s.display_name]))
  return (id: string | null | undefined) => (id ? names.get(id) ?? '—' : '—')
}
