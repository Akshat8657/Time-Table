import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { CircleAlert, CircleCheck, Info, TriangleAlert, X } from 'lucide-react'

type Tone = 'success' | 'error' | 'warning' | 'info'

interface ToastItem {
  id: number
  tone: Tone
  message: string
}

interface ToastApi {
  success: (message: string) => void
  error: (message: string) => void
  warning: (message: string) => void
  info: (message: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

const STYLE: Record<Tone, { box: string; icon: ReactNode }> = {
  success: { box: 'border-success-line bg-success-soft text-success', icon: <CircleCheck className="size-[18px]" /> },
  error: { box: 'border-danger-line bg-danger-soft text-danger', icon: <CircleAlert className="size-[18px]" /> },
  warning: { box: 'border-warning-line bg-warning-soft text-warning', icon: <TriangleAlert className="size-[18px]" /> },
  info: { box: 'border-info-line bg-info-soft text-info', icon: <Info className="size-[18px]" /> },
}

/** Small messages in the corner ("Teacher saved"). They fade away by themselves. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const counter = useRef(0)

  const dismiss = useCallback((id: number) => setItems((list) => list.filter((t) => t.id !== id)), [])

  const push = useCallback(
    (tone: Tone, message: string) => {
      const id = ++counter.current
      setItems((list) => [...list.slice(-3), { id, tone, message }])
      window.setTimeout(() => dismiss(id), tone === 'success' || tone === 'info' ? 4500 : 9000)
    },
    [dismiss],
  )

  const api = useMemo<ToastApi>(
    () => ({
      success: (m) => push('success', m),
      error: (m) => push('error', m),
      warning: (m) => push('warning', m),
      info: (m) => push('info', m),
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={api}>
      {children}
      {createPortal(
        <div className="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex flex-col items-center gap-2 p-4 sm:items-end sm:p-6" aria-live="polite">
          {items.map((t) => (
            <div
              key={t.id}
              role={t.tone === 'error' || t.tone === 'warning' ? 'alert' : 'status'}
              className={`animate-rise pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl border px-4 py-3 text-sm leading-snug shadow-pop ${STYLE[t.tone].box}`}
            >
              <span className="mt-px shrink-0">{STYLE[t.tone].icon}</span>
              <p className="min-w-0 flex-1 break-words">{t.message}</p>
              <button type="button" onClick={() => dismiss(t.id)} aria-label="Dismiss" className="-m-1 shrink-0 rounded-md p-1 opacity-70 hover:opacity-100">
                <X className="size-4" />
              </button>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>')
  return ctx
}
