// Helpers for editing string-list plugin parameters as plain text.
// Accepts both comma- and newline-separated input (e.g. "car, person" or one
// per line) so users don't have to write JSON brackets.

export function parseList(text) {
  return String(text ?? '')
    .split(/[\n,]+/)
    .map(item => item.trim())
    .filter(Boolean)
}

export function formatList(value) {
  if (Array.isArray(value)) return value.join('\n')
  return value == null ? '' : String(value)
}
