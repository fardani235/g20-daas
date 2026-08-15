import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * Merge class lists, letting later Tailwind utilities override earlier
 * conflicting ones. Every ui/ primitive routes its classes through this so a
 * caller's `class` prop can override the component's own defaults.
 */
export function cn(...inputs) {
  return twMerge(clsx(inputs))
}
