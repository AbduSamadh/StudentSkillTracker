// Figure captions in the reader's language. The API's `display` and `withheld_reason` strings are
// English (they go into exports and logs); on screen, the same sentence is rebuilt from the
// structured fields so an Arabic page never shows an English clause scrambled by RTL layout.

import type { TFunction } from 'i18next'

import { isolate } from './format'
import type { Figure } from './types'

/** A denominator such as "students on roll", translated when it is a known phrase. */
export function figureUnit(t: TFunction, label: string | null): string {
  if (!label) return ''
  const known = t(`figureUnits.${label}`, { defaultValue: '' })
  return known || isolate(label)
}

function num(n: number | null, lang: string): string {
  return n === null ? '—' : new Intl.NumberFormat(lang === 'ar' ? 'ar-AE-u-nu-latn' : 'en-GB', { maximumFractionDigits: 1 }).format(n)
}

export function figureCaption(t: TFunction, f: Figure, lang: string): string {
  if (f.withheld) return t('common.withheld')
  if (f.value === null) return '—'
  const unit = figureUnit(t, f.denominator_label)
  const v = { value: num(f.value, lang), numerator: num(f.numerator, lang), denominator: num(f.denominator, lang), unit, currency: f.unit ?? '' }
  if (f.kind === 'percent') return t('figure.percent', v)
  if (f.kind === 'money') return f.denominator === null ? t('figure.money', v) : t('figure.moneyOf', v)
  if (f.kind === 'count' && f.denominator !== null) return t('figure.countOf', v)
  if (f.kind === 'number' && f.denominator !== null) return t('figure.sample', v)
  return v.value
}

export function withheldReason(t: TFunction, f: Figure): string {
  const unit = f.denominator_label ? figureUnit(t, f.denominator_label) : ''
  const v = { min: f.withheld_min ?? '', denominator: f.denominator ?? '', unit }
  switch (f.withheld_rule) {
    case 'no_base':
      return t('figure.withheld.noBase')
    case 'base':
      return t('figure.withheld.base', v)
    case 'cell':
      return unit ? t('figure.withheld.cell', v) : t('figure.withheld.smallCell', v)
    case 'remainder':
      return t('figure.withheld.remainder', v)
    case 'sample':
      return t('figure.withheld.sample', v)
    default:
      return f.withheld_reason ? isolate(f.withheld_reason) : ''
  }
}
