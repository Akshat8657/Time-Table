import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { ChevronDown, LogOut, Menu, X } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Button } from '../components/Button'
import { Logo } from '../components/Logo'
import { ThemeToggle } from '../components/ThemeToggle'
import { useConsent } from '../consent/ConsentProvider'
import { COOKIE_NAMES, storeConvenience } from '../lib/cookies'
import { initials } from '../lib/format'
import { findItem, NAV, ROLE_LABEL, type NavItem } from '../lib/nav'

function SidebarLink({ item, onNavigate }: { item: NavItem; onNavigate: () => void }) {
  const Icon = item.icon
  return (
    <NavLink
      to={item.path}
      end
      onClick={onNavigate}
      className={({ isActive }) =>
        `group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
          isActive ? 'bg-brand-soft text-brand-text' : 'text-muted hover:bg-hover hover:text-ink'
        }`
      }
    >
      <Icon className="size-[18px] shrink-0" aria-hidden="true" />
      <span className="truncate">{item.label}</span>
    </NavLink>
  )
}

function SidebarGroup({ item, onNavigate }: { item: NavItem; onNavigate: () => void }) {
  const [open, setOpen] = useState(true)
  const Icon = item.icon
  return (
    <div className="pt-2">
      <p className="px-3 pb-1.5 pt-3 text-[11px] font-semibold uppercase tracking-wider text-subtle">Teacher mode</p>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-muted transition-colors hover:bg-hover hover:text-ink"
      >
        <Icon className="size-[18px] shrink-0" aria-hidden="true" />
        <span className="flex-1 truncate text-left">{item.label}</span>
        <ChevronDown className={`size-4 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
      </button>
      {open && (
        <div className="ml-[1.4rem] mt-1 space-y-0.5 border-l border-line pl-2">
          {item.children?.map((child) => (
            <SidebarLink key={child.path} item={child} onNavigate={onNavigate} />
          ))}
        </div>
      )}
    </div>
  )
}

function SidebarContent({ onNavigate }: { onNavigate: () => void }) {
  const { user, logout } = useAuth()
  const { openSettings } = useConsent()
  const navigate = useNavigate()
  if (!user) return null

  function signOut() {
    navigate('/login', { replace: true })
    void logout()
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 shrink-0 items-center px-5">
        <Logo size={32} />
      </div>

      <nav aria-label="Main menu" className="flex-1 space-y-0.5 overflow-y-auto px-3 pb-4 pt-2">
        {NAV[user.role].map((item) =>
          item.children ? (
            <SidebarGroup key={item.path} item={item} onNavigate={onNavigate} />
          ) : (
            <SidebarLink key={item.path} item={item} onNavigate={onNavigate} />
          ),
        )}
      </nav>

      <div className="shrink-0 border-t border-line p-4">
        <div className="flex items-center gap-3">
          <span
            aria-hidden="true"
            className="flex size-10 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-indigo-700 text-sm font-semibold text-white"
          >
            {initials(user.full_name)}
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-ink">{user.full_name}</p>
            <p className="truncate text-xs text-muted">
              {ROLE_LABEL[user.role]}
              {user.school_name ? ` · ${user.school_name}` : ''}
            </p>
          </div>
        </div>
        <Button variant="secondary" fullWidth className="mt-3" icon={<LogOut className="size-4" />} onClick={signOut}>
          Sign out
        </Button>
        <button
          type="button"
          onClick={openSettings}
          className="mt-3 block w-full text-center text-xs text-subtle underline-offset-2 hover:text-ink hover:underline"
        >
          Cookie settings
        </button>
      </div>
    </div>
  )
}

export function AppShell() {
  const { user } = useAuth()
  const location = useLocation()
  const [drawerOpen, setDrawerOpen] = useState(false)

  const item = user ? findItem(user.role, location.pathname) : undefined

  // remember the last opened screen (a cookie - only if the person accepted cookies)
  useEffect(() => {
    if (item) storeConvenience(COOKIE_NAMES.lastView, location.pathname)
  }, [item, location.pathname])

  useEffect(() => {
    document.title = `${item?.label ?? 'TimeTablePro'} · TimeTablePro`
  }, [item])

  // mobile menu: close after navigating, close with Esc, and do not scroll the page behind it
  useEffect(() => setDrawerOpen(false), [location.pathname])
  useEffect(() => {
    if (!drawerOpen) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setDrawerOpen(false)
    document.addEventListener('keydown', onKey)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previous
    }
  }, [drawerOpen])

  if (!user) return null

  return (
    <div className="flex min-h-screen bg-bg">
      {/* desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen w-72 shrink-0 border-r border-line bg-surface lg:block">
        <SidebarContent onNavigate={() => undefined} />
      </aside>

      {/* mobile drawer */}
      {drawerOpen && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="absolute inset-0 bg-slate-950/50 backdrop-blur-[2px]" onClick={() => setDrawerOpen(false)} />
          <aside className="animate-slide absolute inset-y-0 left-0 w-[18.5rem] max-w-[85vw] border-r border-line bg-surface shadow-pop">
            <button
              type="button"
              onClick={() => setDrawerOpen(false)}
              aria-label="Close menu"
              className="absolute right-3 top-3.5 z-10 rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"
            >
              <X className="size-5" />
            </button>
            <SidebarContent onNavigate={() => setDrawerOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-16 shrink-0 items-center gap-3 border-b border-line bg-surface/85 px-4 backdrop-blur sm:px-6">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            aria-label="Open menu"
            className="-ml-1 rounded-lg p-2 text-muted hover:bg-hover hover:text-ink lg:hidden"
          >
            <Menu className="size-5" />
          </button>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[15px] font-semibold text-ink">{item?.label ?? 'TimeTablePro'}</p>
            <p className="hidden truncate text-xs text-muted sm:block">
              {ROLE_LABEL[user.role]}
              {user.school_name ? ` · ${user.school_name}` : ' · All schools'}
            </p>
          </div>
          <ThemeToggle />
        </header>

        <main className="flex-1 px-4 py-6 sm:px-8 sm:py-8">
          <div className="mx-auto w-full max-w-6xl">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
