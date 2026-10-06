import i18n from './i18n'

// Latin digits in both languages (the convention for UAE school communications).
const locale = (): string => (i18n.language === 'ar' ? 'ar-AE-u-nu-latn' : 'en-GB')

export function fmtDate(value: string | Date | null | undefined, opts?: Intl.DateTimeFormatOptions): string {
  if (!value) return '—'
  const d = typeof value === 'string' ? new Date(value.length === 10 ? `${value}T00:00:00` : value) : value
  return new Intl.DateTimeFormat(locale(), opts ?? { day: 'numeric', month: 'short', year: 'numeric' }).format(d)
}

export function fmtDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat(locale(), { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(
    new Date(value),
  )
}

export function fmtMoney(value: number | string | null | undefined, currency = 'AED'): string {
  if (value === null || value === undefined || value === '') return '—'
  return new Intl.NumberFormat(locale(), { style: 'currency', currency, maximumFractionDigits: 0 }).format(Number(value))
}

export function fmtNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat(locale(), { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value)
}

export function localDateTimeInput(d = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function hexToTriplet(hex: string): string {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim())
  if (!m) return '15 118 110'
  return `${parseInt(m[1], 16)} ${parseInt(m[2], 16)} ${parseInt(m[3], 16)}`
}

/** Pick the Arabic or English variant of a bilingual pair. */
export function pick<T>(en: T, ar: T | null | undefined): T {
  return i18n.language === 'ar' && ar ? ar : en
}

/** Wrap inserted data (a season like "2026-27", a code) in Unicode isolates so the text around
 *  it cannot reorder it: in an Arabic sentence "2026-27" would otherwise display as "27-2026". */
export function isolate(s: string): string {
  return `\u2068${s}\u2069`
}
