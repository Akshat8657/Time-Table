import { useState } from 'react'
import { Check, Copy, Download, KeyRound } from 'lucide-react'
import { Alert } from './Alert'
import { Button } from './Button'
import { Modal } from './Modal'
import { Checkbox } from './Field'

export interface Credential {
  name: string
  email: string
  temp_password: string
}

function csvCell(value: string): string {
  // a value that starts with = + - @ could be run as a formula by Excel: make it plain text
  const safe = /^[=+\-@]/.test(value) ? `'${value}` : value
  return `"${safe.replace(/"/g, '""')}"`
}

function downloadCsv(items: Credential[]) {
  const lines = ['Name,Email,Temporary password', ...items.map((c) => [c.name, c.email, c.temp_password].map(csvCell).join(','))]
  const blob = new Blob([lines.join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'new-accounts.csv'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

/**
 * Shows a temporary password ONCE. Nothing is e-mailed - the password is handed over by word of mouth / on paper.
 * The dialog cannot be closed by accident (Esc / click outside are off): the person must confirm.
 */
export function CredentialsDialog({
  open,
  title = 'Temporary password',
  items,
  onClose,
}: {
  open: boolean
  title?: string
  items: Credential[]
  onClose: () => void
}) {
  const [copied, setCopied] = useState<string | null>(null)
  const [noted, setNoted] = useState(false)

  async function copy(key: string, text: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(key)
      window.setTimeout(() => setCopied((c) => (c === key ? null : c)), 1800)
    } catch {
      /* the clipboard may be blocked: the text is selectable on screen anyway */
    }
  }

  function close() {
    setNoted(false)
    setCopied(null)
    onClose()
  }

  const many = items.length > 1
  return (
    <Modal
      open={open}
      onClose={close}
      dismissible={false}
      size={many ? 'lg' : 'md'}
      title={title}
      description="Share this with the person by hand (nothing is sent by e-mail). They must choose their own password the first time they sign in."
      footer={
        <>
          {many && (
            <>
              <Button variant="secondary" icon={<Copy className="size-4" />} onClick={() => copy('all', items.map((c) => `${c.name} | ${c.email} | ${c.temp_password}`).join('\n'))}>
                {copied === 'all' ? 'Copied' : 'Copy all'}
              </Button>
              <Button variant="secondary" icon={<Download className="size-4" />} onClick={() => downloadCsv(items)}>
                Download CSV
              </Button>
            </>
          )}
          <Button onClick={close} disabled={!noted}>
            Close
          </Button>
        </>
      }
    >
      <Alert tone="warning" title="This is the only time you will see it" className="mb-4">
        The password is not stored anywhere in readable form. If it is lost, use “Reset password” to make a new one.
      </Alert>
      <ul className="space-y-3">
        {items.map((c, i) => (
          <li key={`${c.email}-${i}`} className="rounded-xl border border-line bg-surface-2 p-4">
            <p className="text-sm font-semibold text-ink">{c.name}</p>
            <p className="text-[13px] text-muted">{c.email}</p>
            <div className="mt-2.5 flex items-center gap-2">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-text">
                <KeyRound className="size-[18px]" aria-hidden="true" />
              </span>
              <code className="min-w-0 flex-1 select-all break-all rounded-lg border border-line bg-surface px-3 py-2 font-mono text-[15px] font-semibold tracking-wide text-ink">
                {c.temp_password}
              </code>
              <Button
                variant="secondary"
                size="sm"
                aria-label={`Copy the password of ${c.name}`}
                icon={copied === `${c.email}-${i}` ? <Check className="size-4 text-success" /> : <Copy className="size-4" />}
                onClick={() => copy(`${c.email}-${i}`, c.temp_password)}
              >
                {copied === `${c.email}-${i}` ? 'Copied' : 'Copy'}
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <Checkbox
        className="mt-5"
        checked={noted}
        onChange={(e) => setNoted(e.target.checked)}
        label={many ? 'I have saved / shared all these passwords' : 'I have saved / shared this password'}
        description="You cannot see it again after closing this window."
      />
    </Modal>
  )
}
