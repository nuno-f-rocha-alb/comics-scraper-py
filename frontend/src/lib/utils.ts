import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Parse a form string to an int, or null for empty/invalid (never NaN).
 *  Only whole-number strings pass — decimals/exponents are rejected (these feed
 *  integer fields like year and volume IDs). */
export function parseIntOrNull(v: string): number | null {
  const t = v.trim()
  if (!/^\d+$/.test(t)) return null
  return Number(t)
}
