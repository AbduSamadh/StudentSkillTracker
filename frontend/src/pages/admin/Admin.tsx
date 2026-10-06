import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Alert, Badge, Button, Card, Empty, Loading, PageHeader, SelectInput, TableWrap, TextInput, useToast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { fmtDate, fmtDateTime, fmtMoney } from '@/lib/format'
import { useEditions, useSquads } from '@/lib/queries'

// ---------------- MIS import ----------------
interface Batch {
  id: string
  status: string
  filename: string | null
  summary: Record<string, number>
  mapping: { columns: Record<string, string>; headers: string[] }
  diff: {
    creates: { external_mis_id: string; name: string; year_group: number }[]
    updates: { external_mis_id: string; changes: Record<string, { from: unknown; to: unknown }> }[]
    leavers: { external_mis_id: string; name: string }[]
    errors: { row: number; external_mis_id?: string; errors: string[] }[]
  }
  created_at: string
}

export function ImportsPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [leavers, setLeavers] = useState(false)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [batch, setBatch] = useState<Batch | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const fields = useQuery({ queryKey: ['import-fields'], queryFn: () => api<{ targets: string[]; required: string[] }>('/imports/fields') })
  const history = useQuery({ queryKey: ['imports'], queryFn: () => api<Batch[]>('/imports') })
  const dry = useMutation({
    mutationFn: () => {
      const fd = new FormData()
      fd.append('file', file!)
      fd.append('mapping', JSON.stringify(mapping))
      fd.append('mark_missing_as_left', String(leavers))
      return api<Batch>('/imports/mis/dry-run', { method: 'POST', form: fd })
    },
    onSuccess: (b) => {
      setBatch(b)
      setMapping(b.mapping.columns)
      setErr(null)
      setDone(false)
    },
    onError: (e) => setErr(errorMessage(e)),
  })
  const commit = useMutation({
    mutationFn: () => api<Batch>('/imports/mis/commit', { method: 'POST', body: { batch_id: batch!.id } }),
    onSuccess: () => {
      setDone(true)
      void qc.invalidateQueries({ queryKey: ['imports'] })
    },
    onError: (e) => setErr(errorMessage(e)),
  })
  return (
    <>
      <PageHeader title={t('imports.title')} subtitle={t('imports.hint')} />
      <Card>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="csv" className="text-sm font-medium">
              {t('imports.file')}
            </label>
            <input id="csv" type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-sm" />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={leavers} onChange={(e) => setLeavers(e.target.checked)} /> {t('imports.markLeavers')}
          </label>
          <div className="flex items-end">
            <Button disabled={!file} busy={dry.isPending} onClick={() => dry.mutate()}>
              {t('imports.dryRun')}
            </Button>
          </div>
        </div>
        {err && <div className="mt-3"><Alert tone="error">{err}</Alert></div>}
      </Card>
      {batch && (
        <>
          <Card title={t('imports.mapping')} className="mt-4">
            <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-4">
              {(fields.data?.targets ?? []).map((target) => (
                <SelectInput
                  key={target}
                  label={`${target}${fields.data?.required.includes(target) ? ' *' : ''}`}
                  value={mapping[target] ?? ''}
                  onChange={(e) => setMapping((m) => ({ ...m, [target]: e.target.value }))}
                >
                  <option value="">—</option>
                  {batch.mapping.headers.map((h) => (
                    <option key={h}>{h}</option>
                  ))}
                </SelectInput>
              ))}
            </div>
            <Button className="mt-3" variant="secondary" busy={dry.isPending} onClick={() => dry.mutate()}>
              {t('imports.remap')}
            </Button>
          </Card>
          <Card className="mt-4">
            <div className="flex flex-wrap gap-2">
              {(['creates', 'updates', 'unchanged', 'leavers', 'errors', 'guardian_changes'] as const).map((k) => (
                <Badge key={k} tone={k === 'errors' && batch.summary[k] ? 'red' : 'slate'}>
                  {t(`imports.${k === 'guardian_changes' ? 'guardianChanges' : k}`)}: {batch.summary[k] ?? 0}
                </Badge>
              ))}
            </div>
            {batch.diff.errors.length > 0 && (
              <div className="mt-3">
                <Alert tone="error" title={t('imports.errors')}>
                  <ul>
                    {batch.diff.errors.map((e) => (
                      <li key={e.row}>
                        {t('imports.row')} {e.row}: {e.errors.join('; ')}
                      </li>
                    ))}
                  </ul>
                </Alert>
              </div>
            )}
            <div className="mt-3 grid gap-4 lg:grid-cols-3">
              <div>
                <h3>{t('imports.creates')}</h3>
                <ul className="text-sm">
                  {batch.diff.creates.slice(0, 50).map((c) => (
                    <li key={c.external_mis_id}>
                      {c.external_mis_id} · {c.name} · {c.year_group}
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <h3>{t('imports.updates')}</h3>
                <ul className="text-sm">
                  {batch.diff.updates.slice(0, 50).map((u) => (
                    <li key={u.external_mis_id}>
                      {u.external_mis_id}:{' '}
                      {Object.entries(u.changes)
                        .map(([k, v]) => `${k} ${String(v.from)} → ${String(v.to)}`)
                        .join(', ')}
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <h3>{t('imports.leavers')}</h3>
                <ul className="text-sm">
                  {batch.diff.leavers.slice(0, 50).map((l) => (
                    <li key={l.external_mis_id}>
                      {l.external_mis_id} · {l.name}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
            {done ? (
              <div className="mt-4"><Alert tone="success">{t('imports.applied')}</Alert></div>
            ) : (
              <Button className="mt-4" disabled={batch.diff.errors.length > 0} busy={commit.isPending} onClick={() => commit.mutate()}>
                {t('imports.apply')}
              </Button>
            )}
          </Card>
        </>
      )}
      <Card title={t('imports.history')} className="mt-4">
        <ul className="space-y-1 text-sm">
          {(history.data ?? []).map((b) => (
            <li key={b.id}>
              {fmtDateTime(b.created_at)} · {b.filename} · <Badge>{b.status}</Badge> · +{b.summary.creates ?? 0} / ~{b.summary.updates ?? 0}
            </li>
          ))}
        </ul>
      </Card>
    </>
  )
}

// ---------------- Inventory ----------------
interface Asset {
  id: string
  tag: string
  name: string
  category: string
  condition: string
  is_consumable: boolean
  quantity_on_hand: number
  on_loan: number
  needs_reorder: boolean
  service_overdue: boolean
}

export function InventoryPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const qc = useQueryClient()
  const [toast, show] = useToast()
  const editions = useEditions()
  const assets = useQuery({ queryKey: ['assets'], queryFn: () => api<Asset[]>('/inventory') })
  const loans = useQuery({ queryKey: ['loans'], queryFn: () => api<{ id: string; asset_id: string; edition_id: string | null; quantity: number; due_back_on: string | null }[]>('/inventory/loans?open_only=true') })
  const requests = useQuery({ queryKey: ['kit-requests'], queryFn: () => api<{ id: string; description: string; quantity: number; status: string; created_at: string }[]>('/inventory/requests'), enabled: can('request_kit') })
  const [newAsset, setNewAsset] = useState({ tag: '', name: '', category: '', quantity_on_hand: 1, is_consumable: false })
  const [loan, setLoan] = useState({ asset_id: '', edition_id: '', quantity: 1, due_back_on: '' })
  const [reqText, setReqText] = useState('')
  const inv = () => {
    void qc.invalidateQueries({ queryKey: ['assets'] })
    void qc.invalidateQueries({ queryKey: ['loans'] })
    void qc.invalidateQueries({ queryKey: ['kit-requests'] })
  }
  const onErr = (e: unknown) => show(errorMessage(e), 'error')
  const add = useMutation({ mutationFn: () => api('/inventory', { method: 'POST', body: newAsset }), onSuccess: inv, onError: onErr })
  const issue = useMutation({
    mutationFn: () => api('/inventory/loans', { method: 'POST', body: { ...loan, edition_id: loan.edition_id || null, due_back_on: loan.due_back_on || null } }),
    onSuccess: inv,
    onError: onErr,
  })
  const ret = useMutation({
    mutationFn: ({ id, qty, condition }: { id: string; qty: number; condition: string }) => api(`/inventory/loans/${id}/return`, { method: 'POST', body: { returned_quantity: qty, return_condition: condition } }),
    onSuccess: inv,
    onError: onErr,
  })
  const request = useMutation({ mutationFn: () => api('/inventory/requests', { method: 'POST', body: { description: reqText } }), onSuccess: () => { setReqText(''); inv() }, onError: onErr })
  const decide = useMutation({ mutationFn: ({ id, status }: { id: string; status: string }) => api(`/inventory/requests/${id}/decide`, { method: 'POST', body: { status } }), onSuccess: inv })
  const names = new Map((assets.data ?? []).map((a) => [a.id, `${a.tag} ${a.name}`]))
  const edNames = new Map((editions.data ?? []).map((e) => [e.id, e.name]))
  const manage = can('manage_inventory')
  return (
    <>
      {toast}
      <PageHeader title={t('inventory.title')} />
      <Card>
        {assets.isLoading ? (
          <Loading />
        ) : (
          <TableWrap>
            <table>
              <thead>
                <tr>
                  <th>{t('inventory.tag')}</th>
                  <th>{t('common.name')}</th>
                  <th>{t('inventory.category')}</th>
                  <th>{t('inventory.condition')}</th>
                  <th>{t('inventory.quantity')}</th>
                  <th>{t('inventory.onLoan')}</th>
                </tr>
              </thead>
              <tbody>
                {(assets.data ?? []).map((a) => (
                  <tr key={a.id}>
                    <td className="font-mono text-xs">{a.tag}</td>
                    <td>
                      {a.name}
                      <div className="flex gap-1">
                        {a.needs_reorder && <Badge tone="amber">⚠ {t('inventory.reorder')}</Badge>}
                        {a.service_overdue && <Badge tone="red">⚠ {t('inventory.serviceDue')}</Badge>}
                      </div>
                    </td>
                    <td>{a.category}</td>
                    <td>{t(`conditions.${a.condition}` as 'conditions.good')}</td>
                    <td>{a.quantity_on_hand}</td>
                    <td>{a.on_loan}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {manage && (
          <Card title={t('inventory.issue')}>
            <div className="grid gap-3 sm:grid-cols-2">
              <SelectInput label={t('inventory.tag')} value={loan.asset_id} onChange={(e) => setLoan({ ...loan, asset_id: e.target.value })}>
                <option value="" />
                {(assets.data ?? []).map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.tag} — {a.name}
                  </option>
                ))}
              </SelectInput>
              <SelectInput label={t('capture.edition')} value={loan.edition_id} onChange={(e) => setLoan({ ...loan, edition_id: e.target.value })}>
                <option value="" />
                {(editions.data ?? []).map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.name}
                  </option>
                ))}
              </SelectInput>
              <TextInput label={t('inventory.quantity')} type="number" min={1} value={loan.quantity} onChange={(e) => setLoan({ ...loan, quantity: Number(e.target.value) })} />
              <TextInput label={t('inventory.dueBack')} type="date" value={loan.due_back_on} onChange={(e) => setLoan({ ...loan, due_back_on: e.target.value })} />
            </div>
            <Button className="mt-3" disabled={!loan.asset_id} busy={issue.isPending} onClick={() => issue.mutate()}>
              {t('inventory.issue')}
            </Button>
          </Card>
        )}
        <Card title={t('inventory.loans')}>
          {!loans.data?.length ? (
            <Empty />
          ) : (
            <ul className="space-y-2 text-sm">
              {loans.data.map((l) => (
                <li key={l.id} className="flex flex-wrap items-center justify-between gap-2">
                  <span>
                    {names.get(l.asset_id)} × {l.quantity}
                    <span className="block text-xs text-slate-500">
                      {l.edition_id ? edNames.get(l.edition_id) : ''} {l.due_back_on && `· ${t('inventory.dueBack')} ${fmtDate(l.due_back_on)}`}
                    </span>
                  </span>
                  {manage && (
                    <span className="flex gap-1">
                      <Button size="sm" variant="secondary" onClick={() => ret.mutate({ id: l.id, qty: l.quantity, condition: 'good' })}>
                        {t('inventory.return')}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          const q = window.prompt(t('inventory.returnedQty'), String(l.quantity))
                          if (q !== null) ret.mutate({ id: l.id, qty: Number(q), condition: 'needs_repair' })
                        }}
                      >
                        {t('conditions.needs_repair')}
                      </Button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
        {manage && (
          <Card title={t('inventory.addAsset')}>
            <div className="grid gap-3 sm:grid-cols-2">
              <TextInput label={t('inventory.tag')} value={newAsset.tag} onChange={(e) => setNewAsset({ ...newAsset, tag: e.target.value })} />
              <TextInput label={t('common.name')} value={newAsset.name} onChange={(e) => setNewAsset({ ...newAsset, name: e.target.value })} />
              <TextInput label={t('inventory.category')} value={newAsset.category} onChange={(e) => setNewAsset({ ...newAsset, category: e.target.value })} />
              <TextInput label={t('inventory.quantity')} type="number" min={0} value={newAsset.quantity_on_hand} onChange={(e) => setNewAsset({ ...newAsset, quantity_on_hand: Number(e.target.value) })} />
            </div>
            <Button className="mt-3" busy={add.isPending} disabled={!newAsset.tag || !newAsset.name || !newAsset.category} onClick={() => add.mutate()}>
              {t('common.add')}
            </Button>
          </Card>
        )}
        {can('request_kit') && (
          <Card title={t('inventory.requests')}>
            <div className="flex gap-2">
              <div className="flex-1">
                <TextInput label={t('inventory.description')} value={reqText} onChange={(e) => setReqText(e.target.value)} />
              </div>
              <Button className="self-end" disabled={!reqText} onClick={() => request.mutate()}>
                {t('inventory.newRequest')}
              </Button>
            </div>
            <ul className="mt-3 space-y-1 text-sm">
              {(requests.data ?? []).map((r) => (
                <li key={r.id} className="flex items-center justify-between gap-2">
                  <span>
                    {r.description} × {r.quantity} <Badge>{r.status}</Badge>
                  </span>
                  {manage && r.status === 'open' && (
                    <span className="flex gap-1">
                      <Button size="sm" onClick={() => decide.mutate({ id: r.id, status: 'approved' })}>
                        {t('common.approve')}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => decide.mutate({ id: r.id, status: 'declined' })}>
                        {t('common.reject')}
                      </Button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </>
  )
}

// ---------------- Budget ----------------
interface BudgetLine {
  id: string
  edition_id: string | null
  category: string
  description: string
  planned_amount: string
  actual_amount: string | null
  status: string
}

interface BudgetOverview {
  season: { id: string; name: string; envelope: string | null; currency: string } | null
  planned_total: string
  actual_total: string
  awaiting_approval: number
  by_edition: { edition_id: string | null; edition_name: string; planned: string; actual: string; lines: BudgetLine[] }[]
}

export function BudgetPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const qc = useQueryClient()
  const [toast, show] = useToast()
  const editions = useEditions()
  const q = useQuery({ queryKey: ['budget'], queryFn: () => api<BudgetOverview>('/budget') })
  const [line, setLine] = useState({ edition_id: '', category: 'entry_fee', description: '', planned_amount: '' })
  const inv = () => void qc.invalidateQueries({ queryKey: ['budget'] })
  const onErr = (e: unknown) => show(errorMessage(e), 'error')
  const add = useMutation({
    mutationFn: () => api('/budget/lines', { method: 'POST', body: { ...line, edition_id: line.edition_id || null, season_id: q.data?.season?.id ?? null } }),
    onSuccess: inv,
    onError: onErr,
  })
  const decide = useMutation({ mutationFn: ({ id, decision }: { id: string; decision: string }) => api(`/budget/lines/${id}/decide`, { method: 'POST', body: { decision } }), onSuccess: inv, onError: onErr })
  const actual = useMutation({ mutationFn: ({ id, amount }: { id: string; amount: string }) => api(`/budget/lines/${id}`, { method: 'PATCH', body: { actual_amount: amount } }), onSuccess: inv, onError: onErr })
  if (q.isLoading) return <Loading />
  if (q.error) return <Alert tone="error">{errorMessage(q.error)}</Alert>
  const d = q.data!
  const cur = d.season?.currency ?? 'AED'
  return (
    <>
      {toast}
      <PageHeader title={t('budget.title')} subtitle={d.season?.name} />
      <div className="grid gap-4 sm:grid-cols-4">
        <Card title={t('budget.envelope')}>
          <div className="text-2xl font-bold">{fmtMoney(d.season?.envelope, cur)}</div>
        </Card>
        <Card title={t('budget.planned')}>
          <div className="text-2xl font-bold">{fmtMoney(d.planned_total, cur)}</div>
        </Card>
        <Card title={t('budget.actual')}>
          <div className="text-2xl font-bold">{fmtMoney(d.actual_total, cur)}</div>
        </Card>
        <Card title={t('budget.statuses.proposed')}>
          <div className="text-2xl font-bold">{d.awaiting_approval}</div>
        </Card>
      </div>
      <div className="mt-4 space-y-4">
        {d.by_edition.map((e) => (
          <Card key={e.edition_id ?? 'season'} title={e.edition_name} actions={<span className="text-sm text-slate-600">{fmtMoney(e.actual, cur)} / {fmtMoney(e.planned, cur)}</span>}>
            <TableWrap>
              <table>
                <thead>
                  <tr>
                    <th>{t('budget.category')}</th>
                    <th>{t('budget.description')}</th>
                    <th>{t('budget.amount')}</th>
                    <th>{t('budget.actual')}</th>
                    <th>{t('common.status')}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {e.lines.map((l) => (
                    <tr key={l.id}>
                      <td>{t(`budget.categories.${l.category}` as 'budget.categories.kit')}</td>
                      <td>{l.description}</td>
                      <td>{fmtMoney(l.planned_amount, cur)}</td>
                      <td>{fmtMoney(l.actual_amount, cur)}</td>
                      <td>
                        <Badge tone={l.status === 'approved' ? 'green' : l.status === 'rejected' ? 'red' : 'amber'}>{t(`budget.statuses.${l.status}` as 'budget.statuses.approved')}</Badge>
                      </td>
                      <td className="space-x-1 rtl:space-x-reverse">
                        {can('approve_spend') && l.status === 'proposed' && (
                          <>
                            <Button size="sm" onClick={() => decide.mutate({ id: l.id, decision: 'approved' })}>
                              {t('common.approve')}
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => decide.mutate({ id: l.id, decision: 'rejected' })}>
                              {t('common.reject')}
                            </Button>
                          </>
                        )}
                        {can('manage_budget') && l.status === 'approved' && (
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => {
                              const v = window.prompt(t('budget.recordActual'), l.actual_amount ?? l.planned_amount)
                              if (v) actual.mutate({ id: l.id, amount: v })
                            }}
                          >
                            {t('budget.recordActual')}
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </Card>
        ))}
        {can('manage_budget') && (
          <Card title={t('budget.addLine')}>
            <div className="grid gap-3 sm:grid-cols-4">
              <SelectInput label={t('capture.edition')} value={line.edition_id} onChange={(e) => setLine({ ...line, edition_id: e.target.value })}>
                <option value="">{t('budget.seasonWide')}</option>
                {(editions.data ?? []).map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.name}
                  </option>
                ))}
              </SelectInput>
              <SelectInput label={t('budget.category')} value={line.category} onChange={(e) => setLine({ ...line, category: e.target.value })}>
                {['entry_fee', 'transport', 'kit', 'cover', 'accommodation', 'other'].map((c) => (
                  <option key={c} value={c}>
                    {t(`budget.categories.${c}` as 'budget.categories.kit')}
                  </option>
                ))}
              </SelectInput>
              <TextInput label={t('budget.description')} value={line.description} onChange={(e) => setLine({ ...line, description: e.target.value })} />
              <TextInput label={t('budget.amount')} type="number" min={0} value={line.planned_amount} onChange={(e) => setLine({ ...line, planned_amount: e.target.value })} />
            </div>
            <Button className="mt-3" disabled={!line.description || !line.planned_amount} busy={add.isPending} onClick={() => add.mutate()}>
              {t('common.add')}
            </Button>
          </Card>
        )}
      </div>
    </>
  )
}

// ---------------- Audit ----------------
export function AuditPage() {
  const { t } = useTranslation()
  const [action, setAction] = useState('')
  const [subject, setSubject] = useState('')
  const params = new URLSearchParams({ limit: '200' })
  if (action) params.set('action', action)
  if (subject) params.set('subject_id', subject)
  const q = useQuery({ queryKey: ['audit', action, subject], queryFn: () => api<{ total: number; items: { id: string; at: string; actor_user_id: string | null; actor_roles: string[]; action: string; subject_type: string | null; subject_id: string | null; reason: string | null; ip: string | null }[] }>(`/audit?${params}`) })
  return (
    <>
      <PageHeader title={t('audit.title')} subtitle={t('audit.hint')} />
      <Card>
        <div className="mb-3 grid gap-3 sm:grid-cols-2">
          <TextInput label={t('audit.action')} value={action} onChange={(e) => setAction(e.target.value)} placeholder="student.read" dir="ltr" />
          <TextInput label={t('audit.subject')} value={subject} onChange={(e) => setSubject(e.target.value)} dir="ltr" />
        </div>
        {q.isLoading ? (
          <Loading />
        ) : (
          <TableWrap>
            <table className="text-xs">
              <thead>
                <tr>
                  <th>{t('audit.at')}</th>
                  <th>{t('audit.action')}</th>
                  <th>{t('audit.actor')}</th>
                  <th>{t('audit.subject')}</th>
                  <th>{t('common.reason')}</th>
                </tr>
              </thead>
              <tbody>
                {(q.data?.items ?? []).map((e) => (
                  <tr key={e.id}>
                    <td className="whitespace-nowrap">{fmtDateTime(e.at)}</td>
                    <td className="font-mono">{e.action}</td>
                    <td>
                      <span className="font-mono">{e.actor_user_id?.slice(0, 8) ?? 'system'}</span> {e.actor_roles.join(', ')}
                    </td>
                    <td className="font-mono">
                      {e.subject_type} {e.subject_id?.slice(0, 8)}
                    </td>
                    <td>{e.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-slate-500">{q.data?.total}</p>
          </TableWrap>
        )}
      </Card>
    </>
  )
}

// ---------------- Users ----------------
interface UserRow {
  id: string
  email: string
  display_name: string
  is_active: boolean
  mfa_enabled: boolean
  sso_linked: boolean
  roles: { id: string; role: string; scope_type: string; scope_id: string | null }[]
}

export function UsersPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [toast, show] = useToast()
  const squads = useSquads()
  const q = useQuery({ queryKey: ['users'], queryFn: () => api<UserRow[]>('/admin/users') })
  const [nu, setNu] = useState({ email: '', display_name: '', role: 'teacher' })
  const inv = () => void qc.invalidateQueries({ queryKey: ['users'] })
  const onErr = (e: unknown) => show(errorMessage(e), 'error')
  const create = useMutation({ mutationFn: () => api('/admin/users', { method: 'POST', body: { email: nu.email, display_name: nu.display_name, roles: [{ role: nu.role }] } }), onSuccess: () => { setNu({ email: '', display_name: '', role: 'teacher' }); inv() }, onError: onErr })
  const grant = useMutation({ mutationFn: ({ id, body }: { id: string; body: object }) => api(`/admin/users/${id}/roles`, { method: 'POST', body }), onSuccess: inv, onError: onErr })
  const revoke = useMutation({ mutationFn: ({ id, rid }: { id: string; rid: string }) => api(`/admin/users/${id}/roles/${rid}`, { method: 'DELETE' }), onSuccess: inv, onError: onErr })
  const active = useMutation({ mutationFn: ({ id, v }: { id: string; v: boolean }) => api(`/admin/users/${id}/active`, { method: 'POST', body: { is_active: v } }), onSuccess: inv, onError: onErr })
  const reset = useMutation({ mutationFn: (id: string) => api(`/admin/users/${id}/reset-mfa`, { method: 'POST' }), onSuccess: inv, onError: onErr })
  const squadName = new Map((squads.data ?? []).map((s) => [s.id, s.name]))
  if (q.isLoading) return <Loading />
  return (
    <>
      {toast}
      <PageHeader title={t('users.title')} />
      <Card>
        <TableWrap>
          <table>
            <thead>
              <tr>
                <th>{t('common.name')}</th>
                <th>{t('users.roles')}</th>
                <th>{t('common.status')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(q.data ?? []).map((u) => (
                <tr key={u.id} className={u.is_active ? '' : 'opacity-60'}>
                  <td>
                    {u.display_name}
                    <div className="text-xs text-slate-500" dir="ltr">
                      {u.email}
                    </div>
                  </td>
                  <td>
                    <div className="flex flex-wrap gap-1">
                      {u.roles.map((r) => (
                        <Badge key={r.id} tone="brand">
                          {t(`users.roleNames.${r.role}` as 'users.roleNames.teacher')}
                          {r.scope_type === 'squad' && ` · ${squadName.get(r.scope_id ?? '') ?? ''}`}
                          <button className="ms-1" aria-label={t('common.remove')} onClick={() => revoke.mutate({ id: u.id, rid: r.id })}>
                            ✕
                          </button>
                        </Badge>
                      ))}
                    </div>
                    <select
                      className="input mt-1 w-56 text-xs"
                      aria-label={t('users.grant')}
                      value=""
                      onChange={(e) => {
                        const [role, scope] = e.target.value.split(':')
                        if (role) grant.mutate({ id: u.id, body: scope ? { role, scope_type: 'squad', scope_id: scope } : { role } })
                      }}
                    >
                      <option value="">{t('users.grant')}…</option>
                      <option value="teacher">{t('users.roleNames.teacher')}</option>
                      <option value="programme_admin">{t('users.roleNames.programme_admin')}</option>
                      <option value="leader">{t('users.roleNames.leader')}</option>
                      {(squads.data ?? []).map((s) => (
                        <option key={s.id} value={`teacher:${s.id}`}>
                          {t('users.roleNames.teacher')} · {s.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="text-xs">
                    {u.mfa_enabled && <Badge tone="green">{t('users.mfaOn')}</Badge>} {u.sso_linked && <Badge>{t('users.sso')}</Badge>}
                  </td>
                  <td className="space-x-1 rtl:space-x-reverse">
                    <Button size="sm" variant="ghost" onClick={() => active.mutate({ id: u.id, v: !u.is_active })}>
                      {u.is_active ? t('users.deactivate') : t('users.activate')}
                    </Button>
                    {u.mfa_enabled && (
                      <Button size="sm" variant="ghost" onClick={() => reset.mutate(u.id)}>
                        {t('users.resetMfa')}
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </Card>
      <Card title={t('users.newUser')} className="mt-4">
        <div className="grid gap-3 sm:grid-cols-4">
          <TextInput label={t('auth.email')} value={nu.email} onChange={(e) => setNu({ ...nu, email: e.target.value })} dir="ltr" />
          <TextInput label={t('common.name')} value={nu.display_name} onChange={(e) => setNu({ ...nu, display_name: e.target.value })} />
          <SelectInput label={t('users.roles')} value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}>
            {['teacher', 'programme_admin', 'leader'].map((r) => (
              <option key={r} value={r}>
                {t(`users.roleNames.${r}` as 'users.roleNames.teacher')}
              </option>
            ))}
          </SelectInput>
          <div className="flex items-end">
            <Button disabled={!nu.email || !nu.display_name} busy={create.isPending} onClick={() => create.mutate()}>
              {t('common.create')}
            </Button>
          </div>
        </div>
      </Card>
    </>
  )
}

// ---------------- Settings ----------------
interface SettingsResponse {
  name: string
  settings: {
    tier_weights: Record<string, string>
    readiness_threshold: string
    plateau_window_days: number
    quiet_hours: { start: string; end: string }
    weekly_message_cap: number
    min_base_for_percent: number
    min_base_for_cell: number
    branding: { primary: string; accent: string }
  }
}

export function SettingsPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const qc = useQueryClient()
  const [toast, show] = useToast()
  const q = useQuery({ queryKey: ['settings'], queryFn: () => api<SettingsResponse>('/admin/settings') })
  const [s, setS] = useState<SettingsResponse['settings'] | null>(null)
  useEffect(() => {
    if (q.data) setS(q.data.settings)
  }, [q.data])
  const save = useMutation({
    mutationFn: () => api('/admin/settings', { method: 'PUT', body: { settings: s } }),
    onSuccess: () => {
      show(t('common.saved'))
      void qc.invalidateQueries({ queryKey: ['settings'] })
      void qc.invalidateQueries({ queryKey: ['me'] })
    },
    onError: (e) => show(errorMessage(e), 'error'),
  })
  const windows = useQuery({ queryKey: ['exam-windows'], queryFn: () => api<{ id: string; name: string; starts_on: string; ends_on: string; year_groups: number[] }[]>('/exam-windows') })
  const [win, setWin] = useState({ name: '', starts_on: '', ends_on: '', year_groups: '' })
  const addWin = useMutation({
    mutationFn: () => api('/exam-windows', { method: 'POST', body: { ...win, year_groups: win.year_groups.split(',').map((x) => Number(x.trim())).filter(Boolean) } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['exam-windows'] }),
    onError: (e) => show(errorMessage(e), 'error'),
  })
  const hooks = useQuery({ queryKey: ['webhooks'], queryFn: () => api<{ id: string; url: string; events: string[] }[]>('/admin/webhooks'), enabled: can('manage_webhooks') })
  const [hookUrl, setHookUrl] = useState('')
  const [secret, setSecret] = useState<string | null>(null)
  const addHook = useMutation({
    mutationFn: () => api<{ secret: string }>('/admin/webhooks', { method: 'POST', body: { url: hookUrl, events: ['result.recorded', 'skill.verified', 'student.flagged'] } }),
    onSuccess: (r) => {
      setSecret(r.secret)
      setHookUrl('')
      void qc.invalidateQueries({ queryKey: ['webhooks'] })
    },
    onError: (e) => show(errorMessage(e), 'error'),
  })
  if (q.isLoading || !s) return <Loading />
  return (
    <>
      {toast}
      <PageHeader title={t('settings.title')} subtitle={q.data?.name} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t('settings.tierWeights')}>
          <div className="grid grid-cols-2 gap-3">
            {Object.entries(s.tier_weights).map(([tier, w]) => (
              <TextInput key={tier} label={t(`tiers.${tier}` as 'tiers.school')} type="number" step="0.01" min="0" value={w} onChange={(e) => setS({ ...s, tier_weights: { ...s.tier_weights, [tier]: e.target.value } })} />
            ))}
          </div>
        </Card>
        <Card title={t('nav.skills')}>
          <div className="grid grid-cols-2 gap-3">
            <TextInput label={t('settings.readinessThreshold')} type="number" step="0.05" min="0.05" max="1" value={s.readiness_threshold} onChange={(e) => setS({ ...s, readiness_threshold: e.target.value })} />
            <TextInput label={t('settings.plateauWindow')} type="number" min={14} value={s.plateau_window_days} onChange={(e) => setS({ ...s, plateau_window_days: Number(e.target.value) })} />
          </div>
        </Card>
        <Card title={t('nav.messages')}>
          <div className="grid grid-cols-3 gap-3">
            <TextInput label={t('settings.quietStart')} type="time" value={s.quiet_hours.start.slice(0, 5)} onChange={(e) => setS({ ...s, quiet_hours: { ...s.quiet_hours, start: e.target.value } })} />
            <TextInput label={t('settings.quietEnd')} type="time" value={s.quiet_hours.end.slice(0, 5)} onChange={(e) => setS({ ...s, quiet_hours: { ...s.quiet_hours, end: e.target.value } })} />
            <TextInput label={t('settings.weeklyCap')} type="number" min={1} value={s.weekly_message_cap} onChange={(e) => setS({ ...s, weekly_message_cap: Number(e.target.value) })} />
          </div>
        </Card>
        <Card title={t('settings.suppression')}>
          <div className="grid grid-cols-2 gap-3">
            <TextInput label={t('settings.minPercentBase')} type="number" min={20} value={s.min_base_for_percent} onChange={(e) => setS({ ...s, min_base_for_percent: Number(e.target.value) })} />
            <TextInput label={t('settings.minCell')} type="number" min={5} value={s.min_base_for_cell} onChange={(e) => setS({ ...s, min_base_for_cell: Number(e.target.value) })} />
          </div>
        </Card>
        <Card title={t('settings.branding')}>
          <TextInput label={t('settings.primaryColour')} type="color" value={s.branding.primary} onChange={(e) => setS({ ...s, branding: { ...s.branding, primary: e.target.value } })} />
        </Card>
        <div className="flex items-end">
          <Button size="lg" busy={save.isPending} onClick={() => save.mutate()}>
            {t('settings.saveSettings')}
          </Button>
        </div>
        <Card title={t('settings.examWindows')}>
          <ul className="mb-3 space-y-1 text-sm">
            {(windows.data ?? []).map((w) => (
              <li key={w.id}>
                {w.name}: {fmtDate(w.starts_on)} – {fmtDate(w.ends_on)} {w.year_groups.length > 0 && `(${w.year_groups.join(', ')})`}
              </li>
            ))}
          </ul>
          <div className="grid gap-2 sm:grid-cols-2">
            <TextInput label={t('common.name')} value={win.name} onChange={(e) => setWin({ ...win, name: e.target.value })} />
            <TextInput label={t('settings.yearGroups')} value={win.year_groups} onChange={(e) => setWin({ ...win, year_groups: e.target.value })} placeholder="11, 13" />
            <TextInput label={t('settings.starts')} type="date" value={win.starts_on} onChange={(e) => setWin({ ...win, starts_on: e.target.value })} />
            <TextInput label={t('settings.ends')} type="date" value={win.ends_on} onChange={(e) => setWin({ ...win, ends_on: e.target.value })} />
          </div>
          <Button className="mt-2" size="sm" disabled={!win.name || !win.starts_on || !win.ends_on} onClick={() => addWin.mutate()}>
            {t('common.add')}
          </Button>
        </Card>
        {can('manage_webhooks') && (
          <Card title={t('settings.webhooks')}>
            <ul className="mb-3 space-y-1 text-sm">
              {(hooks.data ?? []).map((h) => (
                <li key={h.id} dir="ltr">
                  {h.url} <span className="text-xs text-slate-500">{h.events.join(', ')}</span>
                </li>
              ))}
            </ul>
            {secret && (
              <Alert tone="warn" title={t('settings.webhookSecretOnce')}>
                <code className="select-all break-all" dir="ltr">
                  {secret}
                </code>
              </Alert>
            )}
            <div className="mt-2 flex gap-2">
              <div className="flex-1">
                <TextInput label="URL" value={hookUrl} onChange={(e) => setHookUrl(e.target.value)} placeholder="https://…" dir="ltr" />
              </div>
              <Button className="self-end" disabled={!hookUrl.startsWith('https://')} onClick={() => addHook.mutate()}>
                {t('common.add')}
              </Button>
            </div>
          </Card>
        )}
      </div>
    </>
  )
}
