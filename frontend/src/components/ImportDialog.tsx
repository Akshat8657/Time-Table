import { useRef, useState } from 'react'
import { CircleCheck, Download, FileSpreadsheet, Upload } from 'lucide-react'
import { api, apiBlob, ApiError } from '../lib/api'
import { downloadBlob } from '../lib/helpers'
import { useSchoolScope } from '../lib/scope'
import type { ImportResult } from '../lib/types'
import { Alert } from './Alert'
import { Badge } from './Badge'
import { Button } from './Button'
import { CredentialsDialog } from './CredentialsDialog'
import { DataTable } from './DataTable'
import { Modal } from './Modal'
import { useToast } from './Toast'

const COPY = {
  teachers: {
    title: 'Import teachers from Excel',
    noun: 'teachers',
    filename: 'teachers_template.xlsx',
    steps: 'Fill one teacher per row. A temporary password is made for every imported teacher.',
  },
  subjects: {
    title: 'Import subjects from Excel',
    noun: 'subjects',
    filename: 'subjects_template.xlsx',
    steps: 'Fill one subject per row. Only classes that run in the active term are accepted.',
  },
} as const

/**
 * Excel bulk import: 1) download the sample  2) pick your file  3) "Check" shows every row (nothing is saved)
 * 4) "Import" saves only the correct rows - the others are listed with the reason.
 */
export function ImportDialog({
  kind,
  open,
  onClose,
  onDone,
}: {
  kind: 'teachers' | 'subjects'
  open: boolean
  onClose: () => void
  onDone: () => void
}) {
  const copy = COPY[kind]
  const scope = useSchoolScope()
  const toast = useToast()
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState<'check' | 'import' | 'template' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [credentials, setCredentials] = useState<ImportResult['credentials']>(undefined)

  function reset() {
    setFile(null)
    setResult(null)
    setError(null)
    setShowAll(false)
    if (input.current) input.current.value = ''
  }

  function close() {
    reset()
    onClose()
  }

  async function template() {
    setBusy('template')
    setError(null)
    try {
      downloadBlob(await apiBlob(`/imports/templates/${kind}`), copy.filename)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The template could not be downloaded.')
    } finally {
      setBusy(null)
    }
  }

  async function send(dryRun: boolean) {
    if (!file) return
    setBusy(dryRun ? 'check' : 'import')
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await api<ImportResult>(scope.path(`/imports/${kind}?dry_run=${dryRun}`), { method: 'POST', form })
      setResult(res)
      setShowAll(false)
      if (!dryRun) {
        onDone()
        if (res.credentials && res.credentials.length > 0) setCredentials(res.credentials)
        toast.success(`${res.imported} ${copy.noun} imported${res.invalid + res.failed > 0 ? `, ${res.invalid + res.failed} not imported` : ''}.`)
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The file could not be read. Please try again.')
    } finally {
      setBusy(null)
    }
  }

  const finished = result !== null && !result.dry_run
  const rows = result ? (showAll || finished ? result.rows : result.rows.filter((r) => r.status === 'error' || r.status === 'failed')) : []

  return (
    <>
      <Modal
        open={open && !credentials}
        onClose={close}
        size="lg"
        title={copy.title}
        description={copy.steps}
        footer={
          finished ? (
            <Button onClick={close}>Done</Button>
          ) : (
            <>
              <Button variant="secondary" onClick={close}>
                Cancel
              </Button>
              <Button variant="secondary" disabled={!file} loading={busy === 'check'} onClick={() => send(true)} icon={<CircleCheck className="size-4" />}>
                Check the file
              </Button>
              <Button
                disabled={!file || !result || !result.dry_run || result.valid === 0}
                loading={busy === 'import'}
                onClick={() => send(false)}
                icon={<Upload className="size-4" />}
              >
                {result && result.dry_run && result.valid > 0 ? `Import ${result.valid} correct ${result.valid === 1 ? 'row' : 'rows'}` : 'Import'}
              </Button>
            </>
          )
        }
      >
        {!finished && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface-2 p-4">
              <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-success-soft text-success">
                <FileSpreadsheet className="size-5" aria-hidden="true" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-ink">1. Get the sample sheet</p>
                <p className="text-[13px] text-muted">It has the right columns, two example rows and a “How to fill” page.</p>
              </div>
              <Button variant="secondary" size="sm" loading={busy === 'template'} onClick={template} icon={<Download className="size-4" />}>
                Download sample
              </Button>
            </div>

            <div>
              <p className="mb-2 text-sm font-semibold text-ink">2. Choose your filled sheet</p>
              <label
                htmlFor="import-file"
                className="flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed border-line-strong bg-surface px-4 py-6 text-center transition-colors hover:border-accent hover:bg-hover"
              >
                <Upload className="size-6 text-subtle" aria-hidden="true" />
                <span className="text-sm font-medium text-ink">{file ? file.name : 'Click to choose an .xlsx file'}</span>
                <span className="text-xs text-muted">{file ? `${Math.max(1, Math.round(file.size / 1024))} KB` : 'Excel files only, up to 2 MB'}</span>
                <input
                  ref={input}
                  id="import-file"
                  type="file"
                  accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  className="sr-only"
                  onChange={(e) => {
                    setFile(e.target.files?.[0] ?? null)
                    setResult(null)
                    setError(null)
                  }}
                />
              </label>
            </div>
          </div>
        )}

        {error && (
          <Alert tone="danger" className="mt-4">
            {error}
          </Alert>
        )}

        {result && (
          <div className="mt-5 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="neutral">{result.total} rows</Badge>
              {finished ? (
                <>
                  <Badge tone="success">{result.imported} imported</Badge>
                  {result.invalid + result.failed > 0 && <Badge tone="danger">{result.invalid + result.failed} not imported</Badge>}
                </>
              ) : (
                <>
                  <Badge tone="success">{result.valid} correct</Badge>
                  {result.invalid > 0 && <Badge tone="danger">{result.invalid} with problems</Badge>}
                </>
              )}
              {!finished && result.rows.length > 0 && (
                <button type="button" onClick={() => setShowAll((v) => !v)} className="ml-auto text-[13px] font-medium text-brand-text hover:underline">
                  {showAll ? 'Show only problems' : 'Show all rows'}
                </button>
              )}
            </div>
            {!finished && result.invalid === 0 && (
              <Alert tone="success">Every row is correct. Nothing has been saved yet - press “Import” to save them.</Alert>
            )}
            {!finished && result.invalid > 0 && result.valid > 0 && (
              <Alert tone="info">Only the correct rows will be imported. Fix the others in Excel and upload the sheet again, or add them by hand.</Alert>
            )}
            {finished && result.warnings && result.warnings.length > 0 && (
              <Alert tone="warning" title="Please note">
                {result.warnings.join(' ')}
              </Alert>
            )}
            {rows.length > 0 && (
              <DataTable
                caption="Rows of the Excel file"
                dense
                rows={rows}
                rowKey={(r) => String(r.row)}
                columns={[
                  { key: 'row', header: 'Excel row', className: 'w-24 tabular-nums', cell: (r) => r.row },
                  { key: 'label', header: copy.noun === 'teachers' ? 'Teacher' : 'Subject', cell: (r) => <span className="font-medium text-ink">{r.label}</span> },
                  {
                    key: 'status',
                    header: 'Result',
                    cell: (r) =>
                      r.errors.length > 0 ? (
                        <ul className="list-disc space-y-0.5 pl-4 text-danger">
                          {r.errors.map((e) => (
                            <li key={e}>{e}</li>
                          ))}
                        </ul>
                      ) : (
                        <Badge tone="success">{r.status === 'imported' ? 'Imported' : 'OK'}</Badge>
                      ),
                  },
                ]}
              />
            )}
          </div>
        )}
      </Modal>

      <CredentialsDialog
        open={!!credentials}
        title="Temporary passwords of the imported teachers"
        items={(credentials ?? []).map((c) => ({ name: c.name, email: c.email, temp_password: c.temp_password }))}
        onClose={() => {
          setCredentials(undefined)
          close()
        }}
      />
    </>
  )
}
