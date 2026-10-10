import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Bot, CircleAlert, Eraser, Send, ShieldCheck, Sparkles, UserRound } from 'lucide-react'
import { Alert } from '../components/Alert'
import { BlockView, ItemList } from '../components/assistantBlocks'
import { Badge } from '../components/Badge'
import { Button, Spinner } from '../components/Button'
import { Modal } from '../components/Modal'
import { PageHeader } from '../components/Panel'
import { useAuth } from '../auth/AuthProvider'
import { api, ApiError } from '../lib/api'
import { ROLE_PREFIX } from '../lib/nav'
import type { AssistantAction, AssistantReply, AssistantStatus, Role } from '../lib/types'
import { useApi } from '../lib/useApi'

type CardState = 'waiting' | 'confirmed' | 'cancelled' | 'expired' | 'failed'

interface Msg {
  id: number
  from: 'user' | 'bot'
  text: string
  reply?: AssistantReply
  /** an error from the server (shown in red, in the person's own words from the API) */
  error?: { message: string; status: number }
  card?: CardState
}

// The chat of this browser tab: it survives moving between screens, and it is gone when the page is reloaded or the person
// signs out - nothing is written to cookies or browser storage (so it also works when cookies were declined).
const MEMORY = new Map<string, Msg[]>()
let counter = 0

const CARD_WORDS: Record<CardState, string> = {
  waiting: 'Waiting for your Confirm',
  confirmed: 'Confirmed - done',
  cancelled: 'Cancelled - nothing was changed',
  expired: 'This card expired - ask again',
  failed: 'Could not be done',
}

/** "/reports" -> "/hod/reports"; links that are the same for everybody ("/my/...", "/substitutions") are redirected by the app. */
function linkFor(to: string, role: Role): string {
  return to.startsWith('/my/') || to.startsWith('/substitutions') ? to : `${ROLE_PREFIX[role]}${to}`
}

function unavailableBody(status: AssistantStatus | null, role: Role): string {
  switch (status?.reason) {
    case 'no_key':
    case 'bad_key':
      return role === 'admin'
        ? 'The free Gemini key is missing or not accepted. Put a free key from aistudio.google.com/apikey in the .env file as GEMINI_API_KEY and restart the app.'
        : 'The AI helper is switched off for now. Everything else in TimeTablePro works through the normal screens.'
    case 'quota':
      return 'The free AI limit is used up for the moment. It comes back by itself - please try again in a little while. Everything else works through the normal screens.'
    default:
      return 'The AI helper is not answering right now. Everything else in TimeTablePro works through the normal screens.'
  }
}

export default function AssistantPage() {
  const { user } = useAuth()
  const role = user!.role
  const status = useApi<AssistantStatus>('/assistant/status')
  const [messages, setMessages] = useState<Msg[]>(() => MEMORY.get(user!.id) ?? [])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [card, setCard] = useState<{ msgId: number; action: AssistantAction } | null>(null)
  const [cardBusy, setCardBusy] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const messagesRef = useRef(messages)
  messagesRef.current = messages

  useEffect(() => {
    MEMORY.set(user!.id, messages)
    endRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [messages, busy, user])

  const add = useCallback((m: Omit<Msg, 'id'>): number => {
    const id = ++counter
    setMessages((list) => [...list, { ...m, id }])
    return id
  }, [])

  const setCardState = useCallback((id: number, state: CardState) => {
    setMessages((list) => list.map((m) => (m.id === id ? { ...m, card: state } : m)))
  }, [])

  const addReply = useCallback(
    (reply: AssistantReply) => {
      const id = add({ from: 'bot', text: reply.text, reply, card: reply.kind === 'confirm' ? 'waiting' : undefined })
      if (reply.kind === 'confirm' && reply.action) setCard({ msgId: id, action: reply.action })
      if (reply.kind === 'unavailable') status.reload()
    },
    [add, status],
  )

  const addError = useCallback(
    (err: unknown) => {
      const e = err instanceof ApiError ? err : new ApiError(0, 'UNKNOWN', 'Something went wrong. Please try again.')
      add({ from: 'bot', text: e.message, error: { message: e.message, status: e.status } })
    },
    [add],
  )

  const send = useCallback(
    async (raw: string) => {
      const clean = raw.trim()
      if (!clean || busy) return
      const history = messagesRef.current
        .slice(-8)
        .filter((m) => m.text)
        .map((m) => ({ role: m.from === 'user' ? ('user' as const) : ('assistant' as const), text: m.text.slice(0, 700) }))
      add({ from: 'user', text: clean })
      setText('')
      setBusy(true)
      try {
        addReply(await api<AssistantReply>('/assistant/chat', { method: 'POST', body: { message: clean, history } }))
      } catch (err) {
        addError(err)
      } finally {
        setBusy(false)
        inputRef.current?.focus()
      }
    },
    [add, addError, addReply, busy],
  )

  async function pick(option: AssistantReply['options'][number]) {
    if (busy) return
    if (!option.choice_id) {
      await send(option.message ?? option.label)
      return
    }
    add({ from: 'user', text: option.label })
    setBusy(true)
    try {
      addReply(await api<AssistantReply>('/assistant/choose', { method: 'POST', body: { choice_id: option.choice_id } }))
    } catch (err) {
      addError(err)
    } finally {
      setBusy(false)
    }
  }

  async function confirmCard() {
    if (!card) return
    setCardBusy(true)
    try {
      const reply = await api<AssistantReply>('/assistant/confirm', { method: 'POST', body: { action_id: card.action.id } })
      setCardState(card.msgId, 'confirmed')
      setCard(null)
      addReply(reply)
    } catch (err) {
      setCardState(card.msgId, err instanceof ApiError && err.code === 'EXPIRED' ? 'expired' : 'failed')
      setCard(null)
      addError(err)
    } finally {
      setCardBusy(false)
    }
  }

  async function cancelCard() {
    if (!card || cardBusy) return
    const { msgId, action } = card
    setCard(null)
    setCardState(msgId, 'cancelled')
    try {
      await api('/assistant/cancel', { method: 'POST', body: { action_id: action.id } })
    } catch {
      /* the card simply expires by itself */
    }
    add({ from: 'bot', text: 'Cancelled. Nothing was changed.' })
  }

  function clearChat() {
    MEMORY.delete(user!.id)
    setMessages([])
    setCard(null)
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    void send(text)
  }

  const st = status.data
  const examples = st?.examples ?? []
  const down = st !== null && !st.available

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        title="AI Assistant"
        badge={st?.limited ? 'Teacher version' : undefined}
        subtitle={
          role === 'teacher'
            ? 'Ask about your own day and about free teachers - in English or Hinglish. Answers come from the live timetable.'
            : 'Ask about free teachers, teachers, class timetables and your own schedule - in English or Hinglish. Answers come from the live timetable.'
        }
      />

      {down && (
        <Alert tone="warning" title="AI abhi available nahi hai" className="mb-4">
          {unavailableBody(st, role)}
        </Alert>
      )}

      <section className="flex flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-card" aria-label="Chat with the assistant">
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
          <p className="flex items-center gap-2 text-[13px] text-muted">
            <ShieldCheck className="size-4 text-success" aria-hidden="true" />
            Nothing is changed until you press Confirm.
          </p>
          <Button variant="ghost" size="sm" icon={<Eraser className="size-4" aria-hidden="true" />} onClick={clearChat} disabled={messages.length === 0 || busy} data-testid="assistant-clear">
            <span className="hidden sm:inline">Clear chat</span>
          </Button>
        </div>

        <div className="h-[min(60vh,38rem)] min-h-[20rem] space-y-4 overflow-y-auto px-3 py-4 sm:px-5" aria-live="polite" data-testid="assistant-messages">
          {messages.length === 0 && (
            <div className="flex h-full flex-col items-center justify-center px-2 text-center" data-testid="assistant-empty">
              <span className="mb-3 flex size-14 items-center justify-center rounded-2xl bg-brand-soft text-brand-text">
                <Sparkles className="size-7" aria-hidden="true" />
              </span>
              <p className="text-base font-semibold text-ink">Kya jaanna hai? What would you like to know?</p>
              <p className="mt-1 max-w-md text-sm text-muted">Tap an example below or type your own. I understand Hinglish and English.</p>
            </div>
          )}
          {messages.map((m) => (
            <MessageView key={m.id} m={m} role={role} onPick={pick} onReview={() => m.reply?.action && setCard({ msgId: m.id, action: m.reply.action })} disabled={busy} />
          ))}
          {busy && (
            <div className="flex items-start gap-2.5" data-testid="assistant-typing" role="status">
              <BotAvatar />
              <div className="flex items-center gap-2 rounded-2xl rounded-tl-md border border-line bg-surface-2 px-4 py-3 text-sm text-muted">
                <Spinner className="size-4" />
                Checking the live timetable...
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        <div className="border-t border-line bg-surface-2/50 px-3 py-3 sm:px-5">
          {examples.length > 0 && (
            <div className="-mx-1 mb-2.5 flex gap-1.5 overflow-x-auto px-1 pb-1" data-testid="assistant-chips">
              {examples.map((e) => (
                <button
                  key={e.label}
                  type="button"
                  disabled={busy}
                  onClick={() => void send(e.message)}
                  className="shrink-0 rounded-full border border-line-strong bg-surface px-3 py-1.5 text-[13px] text-ink transition-colors hover:bg-hover disabled:opacity-60"
                  data-testid="assistant-chip"
                >
                  {e.label}
                </button>
              ))}
            </div>
          )}
          <form onSubmit={onSubmit} className="flex items-center gap-2" aria-label="Send a message">
            <input
              ref={inputRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              maxLength={st?.limit ?? 500}
              placeholder="Type here, e.g. Aaj kon kon free hai?"
              aria-label="Message to the assistant"
              autoComplete="off"
              className="h-11 min-w-0 flex-1 rounded-xl border border-line-strong bg-surface px-3.5 text-[15px] text-ink placeholder:text-subtle focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/25"
              data-testid="assistant-input"
            />
            <Button type="submit" size="lg" disabled={!text.trim() || busy} icon={<Send className="size-4" aria-hidden="true" />} data-testid="assistant-send">
              <span className="hidden sm:inline">Send</span>
              <span className="sr-only sm:hidden">Send</span>
            </Button>
          </form>
        </div>
      </section>

      <Modal
        open={card !== null}
        onClose={cancelCard}
        title={card?.action.title ?? 'Please confirm'}
        description="Please check the details. Nothing happens until you press the button."
        size="md"
        dismissible={!cardBusy}
        footer={
          <>
            {/* the focus starts on Cancel, never on Confirm: a stray Enter key press must not run an action */}
            <Button variant="secondary" onClick={cancelCard} disabled={cardBusy} data-autofocus data-testid="assistant-cancel">
              {card?.action.cancel_label ?? 'Cancel'}
            </Button>
            <Button onClick={confirmCard} loading={cardBusy} data-testid="assistant-confirm">
              {card?.action.confirm_label ?? 'Confirm'}
            </Button>
          </>
        }
      >
        {card && (
          <div className="space-y-4">
            <dl className="grid grid-cols-[auto_1fr] gap-x-5 gap-y-1.5 text-sm">
              {card.action.lines.map((l) => (
                <div key={l.label} className="contents">
                  <dt className="text-muted">{l.label}</dt>
                  <dd className="font-medium text-ink">{l.value}</dd>
                </div>
              ))}
            </dl>
            {card.action.items.length > 0 && <ItemList block={{ title: 'Lectures that day', items: card.action.items, empty_text: '' }} />}
            <p className="flex items-start gap-2 rounded-lg bg-info-soft px-3 py-2.5 text-[13px] leading-relaxed text-info">
              <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <span>{card.action.note}</span>
            </p>
          </div>
        )}
      </Modal>
    </div>
  )
}

function BotAvatar() {
  return (
    <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-xl bg-brand-soft text-brand-text" aria-hidden="true">
      <Bot className="size-[18px]" />
    </span>
  )
}

function MessageView({
  m, role, onPick, onReview, disabled,
}: {
  m: Msg
  role: Role
  onPick: (o: AssistantReply['options'][number]) => void
  onReview: () => void
  disabled: boolean
}) {
  if (m.from === 'user') {
    return (
      <div className="flex items-start justify-end gap-2.5" data-testid="assistant-msg-user">
        <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-tr-md bg-brand px-4 py-2.5 text-[15px] leading-snug text-white">{m.text}</p>
        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-xl bg-surface-2 text-muted ring-1 ring-line" aria-hidden="true">
          <UserRound className="size-[18px]" />
        </span>
      </div>
    )
  }
  const r = m.reply
  const tone = m.error ? (m.error.status === 403 ? 'warning' : 'danger') : 'plain'
  return (
    <div className="flex items-start gap-2.5" data-testid="assistant-msg-bot" data-kind={m.error ? 'error' : r?.kind ?? 'text'}>
      <BotAvatar />
      <div className="min-w-0 max-w-[calc(100%-2.75rem)] flex-1">
        <div
          className={`rounded-2xl rounded-tl-md border px-4 py-3 ${
            tone === 'danger' ? 'border-danger-line bg-danger-soft text-danger' : tone === 'warning' ? 'border-warning-line bg-warning-soft text-warning' : 'border-line bg-surface-2 text-ink'
          }`}
        >
          <p className="whitespace-pre-wrap break-words text-[15px] leading-snug">{m.text}</p>
          {r?.hint && <p className="mt-1.5 text-[13px] opacity-80">{r.hint}</p>}
          {r?.kind === 'done' && <Badge tone="success" className="mt-2">Done</Badge>}
          {r?.kind === 'confirm' && (
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <Badge tone={m.card === 'waiting' ? 'warning' : m.card === 'confirmed' ? 'success' : 'neutral'}>{CARD_WORDS[m.card ?? 'waiting']}</Badge>
              {m.card === 'waiting' && (
                <Button size="sm" variant="secondary" onClick={onReview} data-testid="assistant-review">
                  Review &amp; confirm
                </Button>
              )}
            </div>
          )}
        </div>
        {r?.blocks.map((b, i) => (
          <BlockView key={i} block={b} />
        ))}
        {r && r.options.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2" data-testid="assistant-options">
            {r.options.map((o) => (
              <button
                key={o.label}
                type="button"
                disabled={disabled}
                onClick={() => onPick(o)}
                className="rounded-full border border-brand/40 bg-brand-soft px-3.5 py-1.5 text-sm font-medium text-brand-text transition-colors hover:bg-brand-soft/70 disabled:opacity-60"
                data-testid="assistant-option"
              >
                {o.label}
              </button>
            ))}
          </div>
        )}
        {r && r.links.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {r.links.map((l) => (
              <Link
                key={l.to + l.label}
                to={linkFor(l.to, role)}
                className="rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-[13px] font-medium text-ink transition-colors hover:bg-hover"
              >
                {l.label}
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
