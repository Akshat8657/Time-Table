export function greeting(date = new Date()): string {
  const hour = date.getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 17) return 'Good afternoon'
  return 'Good evening'
}

export function firstName(fullName: string): string {
  return fullName.trim().split(/\s+/)[0] || 'there'
}

export function initials(fullName: string): string {
  const parts = fullName.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  const first = parts[0][0]
  const last = parts.length > 1 ? parts[parts.length - 1][0] : ''
  return (first + last).toUpperCase()
}

/** 28800 -> "8 hours", 2592000 -> "30 days" */
export function formatDuration(seconds: number): string {
  if (seconds >= 86400) {
    const days = Math.round(seconds / 86400)
    return `${days} ${days === 1 ? 'day' : 'days'}`
  }
  if (seconds >= 3600) {
    const hours = Math.round((seconds / 3600) * 10) / 10
    return `${hours} ${hours === 1 ? 'hour' : 'hours'}`
  }
  const minutes = Math.max(1, Math.round(seconds / 60))
  return `${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`
}
