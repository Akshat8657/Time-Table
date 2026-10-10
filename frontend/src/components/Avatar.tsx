import { initials } from '../lib/format'
import { AuthedImage } from './AuthedImage'

const SIZES = { sm: 'size-8 text-xs', md: 'size-10 text-sm', lg: 'size-16 text-xl', xl: 'size-28 text-3xl' } as const

/** A round picture of a person - or their initials when there is no photo. */
export function Avatar({ name, photoUrl, size = 'md' }: { name: string; photoUrl?: string | null; size?: keyof typeof SIZES }) {
  const base = `${SIZES[size]} shrink-0 rounded-full`
  return (
    <AuthedImage
      src={photoUrl}
      alt={`Photo of ${name}`}
      className={`${base} object-cover ring-1 ring-line`}
      fallback={
        <span aria-hidden="true" className={`${base} flex items-center justify-center bg-gradient-to-br from-indigo-500 to-indigo-700 dark:from-[#1d8b94] dark:to-[#10606f] font-semibold text-white`}>
          {initials(name)}
        </span>
      }
    />
  )
}
