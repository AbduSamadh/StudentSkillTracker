import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ClaimBadge, FigureTile, LevelPill } from '@/components/ui'
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
    expect(m.textContent).toContain('Measured')
    expect(i.textContent).toContain('Inferred')
    expect(i.className).toContain('border-dashed')
    expect(m.className).not.toContain('border-dashed')
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
