import { render, screen } from '@testing-library/react'
import i18n from 'i18next'
import { describe, expect, it } from 'vitest'

import { ClaimBadge, FigureTile, LevelPill } from '@/components/ui'
import { figureCaption, withheldReason } from '@/lib/figures'
import { setLanguage } from '@/lib/i18n'
import type { Figure } from '@/lib/types'

const withheld: Figure = {
  label: 'Participation',
  kind: 'percent',
  value: null,
  numerator: null,
  denominator: 12,
  denominator_label: 'students on roll',
  display: 'Withheld',
  withheld: true,
  withheld_reason: 'the base of 12 students on roll is below the minimum of 20 for publishing a percentage',
  withheld_rule: 'base',
  withheld_min: 20,
  claim_type: 'measured',
  unit: '%',
}

describe('reporting components', () => {
  it('shows a withheld figure as withheld, with the reason', () => {
    setLanguage('en')
    render(<FigureTile figure={withheld} />)
    expect(screen.getByText('Withheld')).toBeInTheDocument()
    expect(screen.getByText(/below the minimum of 20/)).toBeInTheDocument()
  })

  it('renders measured and inferred claims differently', () => {
    setLanguage('en')
    const { container } = render(
      <>
        <ClaimBadge type="measured" />
        <ClaimBadge type="inferred" />
      </>,
    )
    const [m, i] = container.querySelectorAll('span[title]')
    expect(m.textContent).toContain('Recorded')
    expect(i.textContent).toContain('Worked out')
    expect(m.getAttribute('title')).not.toBe(i.getAttribute('title'))
    expect(i.className).toContain('border-dashed')
    expect(m.className).not.toContain('border-dashed')
  })

  it('builds figure captions in the reader\'s language from the structured fields', () => {
    const base = { withheld: false, withheld_reason: null, withheld_rule: null, withheld_min: null, claim_type: 'measured' } as const
    const pct: Figure = { ...base, label: 'Participation', kind: 'percent', value: 25, numerator: 39, denominator: 155, denominator_label: 'students on roll', display: '25% (39 of 155 students on roll)', unit: '%' }
    const money: Figure = { ...base, label: 'Spend', kind: 'money', value: 1150, numerator: 1150, denominator: 60000, denominator_label: 'season envelope', display: 'AED 1,150 of AED 60,000 season envelope', unit: 'AED' }
    const en = i18n.getFixedT('en')
    const ar = i18n.getFixedT('ar')
    // English matches the API's own wording exactly.
    expect(figureCaption(en, pct, 'en')).toBe(pct.display)
    expect(figureCaption(en, money, 'en')).toBe(money.display)
    expect(withheldReason(en, withheld)).toBe(withheld.withheld_reason)
    // Arabic has no English words left in it (AED and % are shared symbols).
    for (const text of [figureCaption(ar, pct, 'ar'), withheldReason(ar, withheld)]) expect(text).not.toMatch(/[a-z]/i)
    expect(figureCaption(ar, pct, 'ar')).toContain('من أصل 155')
  })

  it('renders in Arabic', () => {
    setLanguage('ar')
    render(
      <>
        <FigureTile figure={withheld} />
        <LevelPill level={3} />
      </>,
    )
    expect(screen.getByText('محجوب')).toBeInTheDocument()
    expect(screen.getByText(/متمكّن/)).toBeInTheDocument()
    setLanguage('en')
  })
})
