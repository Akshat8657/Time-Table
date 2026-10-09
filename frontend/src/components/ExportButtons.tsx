import { useState } from 'react'
import { FileSpreadsheet, FileText } from 'lucide-react'
import { apiBlob, ApiError } from '../lib/api'
import { downloadBlob } from '../lib/helpers'
import { Button } from './Button'
import { useToast } from './Toast'

/** "PDF" and "Excel" buttons: downloads the published timetable as a print-friendly file. */
export function ExportButtons({ path, stem, params = '', disabled = false }: { path: string; stem: string; params?: string; disabled?: boolean }) {
  const toast = useToast()
  const [busy, setBusy] = useState<'pdf' | 'xlsx' | null>(null)

  async function run(format: 'pdf' | 'xlsx') {
    setBusy(format)
    try {
      const blob = await apiBlob(`${path}?format=${format}${params ? `&${params}` : ''}`)
      downloadBlob(blob, `${stem}.${format}`)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'The file could not be made. Please try again.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex items-center gap-2" role="group" aria-label="Download">
      <Button variant="secondary" size="sm" icon={<FileText className="size-4" />} onClick={() => void run('pdf')} loading={busy === 'pdf'} disabled={disabled || busy !== null} data-testid="export-pdf">
        PDF
      </Button>
      <Button variant="secondary" size="sm" icon={<FileSpreadsheet className="size-4" />} onClick={() => void run('xlsx')} loading={busy === 'xlsx'} disabled={disabled || busy !== null} data-testid="export-xlsx">
        Excel
      </Button>
    </div>
  )
}
