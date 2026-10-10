import { useQuery } from '@tanstack/react-query'
import { GraduationCap } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { Alert, Card, EmptyState, Loading, PageHeader, SelectInput, TableWrap, TextInput } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import type { Page, Student } from '@/lib/types'

export default function StudentsPage() {
  const { t } = useTranslation()
  const [q, setQ] = useState('')
  const [year, setYear] = useState('')
  const [flag, setFlag] = useState('')
  const params = new URLSearchParams({ limit: '200' })
  if (q) params.set('q', q)
  if (year) params.set('year_group', year)
  if (flag) params.set('flag', flag)
  const res = useQuery({ queryKey: ['students', q, year, flag], queryFn: () => api<Page<Student>>(`/students?${params}`) })
  return (
    <>
      <PageHeader icon={GraduationCap} title={t('students.title')} subtitle={t('students.intro')} />
      <Card>
        <div className="mb-4 grid gap-3 sm:grid-cols-3">
          <TextInput label={t('common.search')} placeholder={t('students.searchPlaceholder')} value={q} onChange={(e) => setQ(e.target.value)} />
          <SelectInput label={t('common.yearGroup')} value={year} onChange={(e) => setYear(e.target.value)}>
            <option value="">{t('common.all')}</option>
            {Array.from({ length: 13 }, (_, i) => i + 1).map((y) => (
              <option key={y} value={y}>
                {t('common.year', { n: y })}
              </option>
            ))}
          </SelectInput>
          <SelectInput label={t('students.flag')} value={flag} onChange={(e) => setFlag(e.target.value)}>
            <option value="">{t('students.anyFlag')}</option>
            {(['plateau', 'stretch', 'attendance'] as const).map((k) => (
              <option key={k} value={k}>
                {t(`flagKinds.${k}`)}
              </option>
            ))}
          </SelectInput>
        </div>
        {res.isLoading ? (
          <Loading />
        ) : res.error ? (
          <Alert tone="error">{errorMessage(res.error)}</Alert>
        ) : res.data!.items.length === 0 ? (
          <EmptyState icon={GraduationCap} title={t('students.none')}>
            {t('students.noneHint')}
          </EmptyState>
        ) : (
          <TableWrap>
            <table>
              <caption className="sr-only">{t('students.title')}</caption>
              <thead>
                <tr>
                  <th>{t('common.name')}</th>
                  <th>{t('common.yearGroup')}</th>
                  <th>{t('students.house')}</th>
                  <th>{t('students.misId')}</th>
                </tr>
              </thead>
              <tbody>
                {res.data!.items.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <Link to={`/students/${s.id}`} className="font-medium text-brand hover:underline">
                        {s.display_name}
                      </Link>
                      {s.full_name_ar && <div className="text-xs text-slate-500" lang="ar" dir="rtl">{s.full_name_ar}</div>}
                    </td>
                    <td>{s.year_group}</td>
                    <td>{s.house ?? '—'}</td>
                    <td className="text-slate-500">{s.external_mis_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-3 text-sm text-slate-500">{t('students.showing', { n: res.data!.items.length, m: res.data!.total })}</p>
          </TableWrap>
        )}
      </Card>
    </>
  )
}
