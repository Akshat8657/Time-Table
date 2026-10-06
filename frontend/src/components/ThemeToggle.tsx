import { Moon, Sun } from 'lucide-react'
import { useTheme } from '../theme/ThemeProvider'

export function ThemeToggle({ className = '' }: { className?: string }) {
  const { theme, toggle } = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className={`inline-flex size-9 items-center justify-center rounded-lg border border-line bg-surface text-muted shadow-sm transition-colors hover:bg-hover hover:text-ink ${className}`}
    >
      {theme === 'dark' ? <Sun className="size-[18px]" /> : <Moon className="size-[18px]" />}
    </button>
  )
}
