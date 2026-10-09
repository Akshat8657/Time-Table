import { useState, type ReactNode } from 'react'
import { FileSpreadsheet } from 'lucide-react'
import { apiBlob, ApiError } from '../lib/api'
import { downloadBlob, queryString, todayIso } from '../lib/helpers'
import type { ReportName } from '../lib/types'
import { useApi } from '../lib/useApi'
import { Alert } from './Alert'
import { Button } from './Button'
import { TextField } from './Field'
import { Skeleton } from './Panel'
import { useToast } from './Toast'

export type Params = Record<string, string | number | boolean | null | undefined>

/** Loads one report. `school` = '' means "every school" (the admin's filter); an HOD's school is decided by the server anyway. */
export function useReport<T>(name: ReportName, school: string, extra: Params = {}) {
  return useApi<T>(`/reports/${name}${queryString({ school_id: school || null, ...extra })}`)
}

/** "Excel" button for one report: downloads the SAME numbers the screen shows (same filters). */
export function ExcelButton({ name, school, extra = {}, disabled = false }: { name: ReportName; school: string; extra?: Params; disabled?: boolean }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  async function go() {
    setBusy(true)
    try {
      const blob = await apiBlob(`/reports/export/${name}${queryString({ school_id: school || null, ...extra })}`)
      downloadBlob(blob, `Report_${name}_${todayIso()}.xlsx`)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'The Excel file could not be made. Please try again.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Button variant="secondary" size="sm" icon={<FileSpreadsheet className="size-4" />} loading={busy} disabled={disabled} onClick={() => void go()} data-testid={`excel-${name}`}>
      Excel
    </Button>
  )
}

/** Loading / error / "no active term" handling shared by every report tab. */
export function ReportFrame<T extends { term: unknown | null; empty_text?: string }>({ state, children }: {
  state: { data: T | null; error: ApiError | null; loading: boolean; reload: () => void }
  children: (data: T) => ReactNode
}) {
  const { data, error, loading, reload } = state
  if (error) {
    return (
      <Alert tone="danger">
        {error.message}{' '}
        <button type="button" onClick={reload} className="font-semibold underline">Try again</button>
      </Alert>
    )
  }
  if (!data) return loading ? <div className="space-y-3" aria-busy="true"><Skeleton className="h-24" /><Skeleton className="h-64" /></div> : null
  if (data.term === null) return <Alert tone="warning" title="No active term">{data.empty_text ?? 'Choose the active term first (Active Term screen).'}</Alert>
  return <div className={loading ? 'opacity-70 transition-opacity' : ''}>{children(data)}</div>
}

export function MonthField({ value, onChange, label = 'Month' }: { value: string; onChange: (v: string) => void; label?: string }) {
  return <TextField label={label} type="month" value={value} onChange={(e) => onChange(e.target.value)} className="w-44" data-testid="month-input" />
}

export function thisMonth(): string {
  return todayIso().slice(0, 7)
}
