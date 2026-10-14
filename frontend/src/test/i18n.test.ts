import { describe, expect, it } from 'vitest'

import ar from '@/i18n/ar'
import en from '@/i18n/en'
import { setLanguage } from '@/lib/i18n'

type Tree = { [k: string]: string | Tree }

function flatten(t: Tree, prefix = ''): Record<string, string> {
  return Object.entries(t).reduce<Record<string, string>>((acc, [k, v]) => {
    const key = prefix ? `${prefix}.${k}` : k
    return typeof v === 'string' ? { ...acc, [key]: v } : { ...acc, ...flatten(v, key) }
  }, {})
}

const placeholders = (s: string) => [...s.matchAll(/{{\s*(\w+)\s*}}/g)].map((m) => m[1]).sort()

describe('Arabic translation', () => {
  const E = flatten(en as unknown as Tree)
  const A = flatten(ar as unknown as Tree)

  it('covers every English key with a non-empty string', () => {
    expect(Object.keys(A).sort()).toEqual(Object.keys(E).sort())
    for (const [k, v] of Object.entries(A)) expect(v.trim(), k).not.toBe('')
  })

  it('keeps the same interpolation placeholders', () => {
    for (const k of Object.keys(E)) expect(placeholders(A[k]), k).toEqual(placeholders(E[k]))
  })

  it('is actually Arabic for sentences (not copied English)', () => {
    const arabic = /[؀-ۿ]/
    const words = (v: string) => v.replace(/{{\s*\w+\s*}}/g, '').trim() // placeholders are not words
    const untranslated = Object.entries(A).filter(([k, v]) => words(v).length > 12 && !arabic.test(v) && !k.startsWith('reports.audience'))
    expect(untranslated).toEqual([])
  })
})

describe('direction', () => {
  it('switches the whole document to RTL for Arabic and back', () => {
    setLanguage('ar')
    expect(document.documentElement.dir).toBe('rtl')
    expect(document.documentElement.lang).toBe('ar')
    setLanguage('en')
    expect(document.documentElement.dir).toBe('ltr')
  })
})

describe('wording used by the screens', () => {
  it('exists in the dictionary for every literal key', async () => {
    const { readFileSync, readdirSync, statSync } = await import('node:fs')
    const { join } = await import('node:path')
    const files = (dir: string): string[] =>
      readdirSync(dir).flatMap((f) => {
        const p = join(dir, f)
        return statSync(p).isDirectory() ? files(p) : /\.tsx?$/.test(f) && !p.includes('/test/') ? [p] : []
      })
    const E = flatten(en as unknown as Tree)
    const missing: string[] = []
    for (const file of files(join(__dirname, '..'))) {
      for (const m of readFileSync(file, 'utf8').matchAll(/\bt\(\s*'([a-zA-Z][\w.]*\w)'/g)) {
        if (!(m[1] in E)) missing.push(`${file.split('/src/')[1]}: ${m[1]}`)
      }
    }
    expect(missing).toEqual([])
  })
})
