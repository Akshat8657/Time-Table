import { useEffect, useState, type ReactNode } from 'react'
import { apiBlob } from '../lib/api'

// Pictures live behind the login, so the browser cannot just load them with <img src>.
// We fetch them with the access token, keep the result for the session and show it from memory.
const cache = new Map<string, string>()

export function forgetImage(url: string | null | undefined): void {
  if (!url) return
  const base = url.split('?')[0]
  for (const key of [...cache.keys()]) {
    if (key.split('?')[0] === base) {
      URL.revokeObjectURL(cache.get(key)!)
      cache.delete(key)
    }
  }
}

export function AuthedImage({ src, alt, className, fallback }: { src: string | null | undefined; alt: string; className?: string; fallback: ReactNode }) {
  const [objectUrl, setObjectUrl] = useState<string | null>(src ? (cache.get(src) ?? null) : null)

  useEffect(() => {
    if (!src) {
      setObjectUrl(null)
      return
    }
    const hit = cache.get(src)
    if (hit) {
      setObjectUrl(hit)
      return
    }
    setObjectUrl(null)
    const controller = new AbortController()
    // the address is "/api/..."; api() adds the prefix itself
    apiBlob(src.replace(/^\/api/, ''), controller.signal).then(
      (blob) => {
        const url = URL.createObjectURL(blob)
        cache.set(src, url)
        if (!controller.signal.aborted) setObjectUrl(url)
      },
      () => undefined, // no picture is fine: the initials are shown instead
    )
    return () => controller.abort()
  }, [src])

  if (!objectUrl) return <>{fallback}</>
  return <img src={objectUrl} alt={alt} className={className} />
}
