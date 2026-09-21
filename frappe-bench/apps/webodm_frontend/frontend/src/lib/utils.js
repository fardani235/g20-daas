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

/**
 * Human-readable message from a Frappe error response body.
 *
 * `frappe.throw` does not populate `message`; it puts the text in
 * `_server_messages`, a JSON-encoded list of JSON-encoded `{message}` objects.
 * Fall back to `exception` (last traceback line, only present when tracebacks
 * are allowed) and finally to `fallback`.
 */
export function frappeErrorMessage(err, fallback = 'Request failed') {
  if (!err || typeof err !== 'object') return fallback
  if (typeof err.message === 'string' && err.message) return err.message
  if (err._server_messages) {
    try {
      const raw = typeof err._server_messages === 'string'
        ? JSON.parse(err._server_messages)
        : err._server_messages
      const texts = (Array.isArray(raw) ? raw : [raw])
        .map(m => (typeof m === 'string' ? JSON.parse(m) : m))
        .map(m => (typeof m === 'string' ? m : m?.message))
        .filter(Boolean)
        .map(m => String(m).replace(/<[^>]*>/g, '').trim())
        .filter(Boolean)
      if (texts.length) return texts.join(' ')
    } catch {}
  }
  if (typeof err.exception === 'string' && err.exception) {
    // "frappe.exceptions.ValidationError: Invalid plugin package" -> text after the type
    return err.exception.replace(/^[\w.]+(Error|Exception):\s*/, '')
  }
  return fallback
}
