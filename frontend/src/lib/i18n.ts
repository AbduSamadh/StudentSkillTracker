import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import ar from '@/i18n/ar'
import en from '@/i18n/en'

export type Lang = 'en' | 'ar'
const STORAGE_KEY = 'stem.lang'

function initialLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'en' || saved === 'ar') return saved
  } catch {
    /* storage unavailable */
  }
  return typeof navigator !== 'undefined' && navigator.language?.startsWith('ar') ? 'ar' : 'en'
}

export function applyDirection(lang: Lang): void {
  const html = document.documentElement
  html.lang = lang
  html.dir = lang === 'ar' ? 'rtl' : 'ltr'
}

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, ar: { translation: ar } },
  lng: initialLang(),
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
  returnNull: false,
})

applyDirection(i18n.language as Lang)

export function setLanguage(lang: Lang): void {
  void i18n.changeLanguage(lang)
  applyDirection(lang)
  try {
    localStorage.setItem(STORAGE_KEY, lang)
  } catch {
    /* ignore */
  }
}

export default i18n
