// Multipart uploads with honest progress.
//
// `fetch` cannot report upload progress, so the two upload endpoints go
// through XMLHttpRequest instead: same URL, same multipart body, same CSRF
// header, same `message` unwrapping — only the transport changes. Progress is
// bytes the browser has handed to the network, which is all a client can
// know; when every byte is out the server still has to read EXIF and write
// rows, so callers show that as a distinct "processing" phase rather than a
// bar stuck at 100 %.
//
// Everything below the transport is pure (file filtering, size maths,
// progress snapshots) so it can be unit-tested without a browser.

import { frappeErrorMessage } from './utils'

export const UPLOAD_PHASES = Object.freeze({
  idle: 'idle',
  uploading: 'uploading', // bytes still leaving the browser
  processing: 'processing', // all bytes sent; waiting for the server's answer
  done: 'done',
  failed: 'failed',
  cancelled: 'cancelled',
})

/** Raised when the user aborts an in-flight upload. */
export class UploadCancelled extends Error {
  constructor() {
    super('Upload cancelled')
    this.name = 'UploadCancelled'
    this.cancelled = true
  }
}

export function isCancelled(err) {
  return !!(err && err.cancelled)
}

/**
 * POST a FormData body to a Frappe method with progress events.
 *
 * `onProgress({ loaded, total })` fires with the bytes sent so far; `signal`
 * (an AbortSignal) cancels the request and rejects with `UploadCancelled`.
 * Resolves with the unwrapped `message`, rejects with the Frappe error text
 * like `postMethod` does, so existing callers can swap transports blindly.
 */
export function uploadForm(url, form, { onProgress, signal, xhrFactory } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = xhrFactory ? xhrFactory() : new XMLHttpRequest()
    let settled = false
    const finish = (fn, value) => {
      if (settled) return
      settled = true
      if (signal) signal.removeEventListener('abort', onAbort)
      fn(value)
    }
    const onAbort = () => {
      xhr.abort()
      finish(reject, new UploadCancelled())
    }

    if (signal) {
      if (signal.aborted) return finish(reject, new UploadCancelled())
      signal.addEventListener('abort', onAbort)
    }

    xhr.open('POST', url)
    xhr.responseType = 'text'
    xhr.setRequestHeader('Accept', 'application/json')
    if (window.csrf_token) xhr.setRequestHeader('X-Frappe-CSRF-Token', window.csrf_token)

    if (xhr.upload && onProgress) {
      xhr.upload.addEventListener('progress', e => {
        onProgress({ loaded: e.loaded, total: e.lengthComputable ? e.total : 0 })
      })
    }
    xhr.addEventListener('load', () => {
      let body = {}
      try { body = xhr.responseText ? JSON.parse(xhr.responseText) : {} } catch { body = {} }
      if (xhr.status >= 200 && xhr.status < 300) {
        finish(resolve, body.message !== undefined ? body.message : body)
      } else {
        finish(reject, new Error(frappeErrorMessage(body, `Upload failed (HTTP ${xhr.status})`)))
      }
    })
    xhr.addEventListener('error', () => finish(reject, new Error('Upload failed: network error')))
    xhr.addEventListener('abort', () => finish(reject, new UploadCancelled()))
    xhr.send(form)
  })
}

// -- files --------------------------------------------------------------------

// What ODM accepts as input imagery. Browsers often leave `type` empty for
// drag-dropped files or RAW formats, so the extension is the fallback.
export const IMAGE_EXTENSIONS = Object.freeze([
  'jpg', 'jpeg', 'jpe', 'png', 'tif', 'tiff', 'bmp', 'webp', 'dng',
])

export function extensionOf(name) {
  const m = /\.([a-z0-9]+)$/i.exec(String(name || ''))
  return m ? m[1].toLowerCase() : ''
}

/** True for a File (or {name, type}) that looks like an input image. */
export function isImageFile(file) {
  if (!file) return false
  const type = String(file.type || '').toLowerCase()
  if (type.startsWith('image/')) return true
  return IMAGE_EXTENSIONS.includes(extensionOf(file.name))
}

/** True for a plausible ground control file (what ODM calls gcp_list.txt). */
export function isGcpFile(file) {
  if (!file) return false
  return extensionOf(file.name) === 'txt'
}

/**
 * Split a mixed drop into images, a ground control file (first .txt) and the
 * rest. Lets a user drag an export folder — photos plus gcp_list.txt — onto
 * the dialog in one go, the way WebODM's own uploader behaves.
 */
export function partitionDrop(files) {
  const images = []
  const ignored = []
  let gcp = null
  for (const f of files || []) {
    if (isImageFile(f)) images.push(f)
    else if (!gcp && isGcpFile(f)) gcp = f
    else ignored.push(f)
  }
  return { images, gcp, ignored }
}

/** Files from a drop event's DataTransfer, descending into dropped folders where the browser allows it. */
export async function filesFromDataTransfer(dt) {
  if (!dt) return []
  const items = dt.items ? Array.from(dt.items) : []
  const entries = items
    .map(it => (typeof it.webkitGetAsEntry === 'function' ? it.webkitGetAsEntry() : null))
    .filter(Boolean)
  if (!entries.length || !entries.some(e => e.isDirectory)) {
    return Array.from(dt.files || [])
  }
  const out = []
  for (const entry of entries) await walkEntry(entry, out)
  return out
}

async function walkEntry(entry, out) {
  if (entry.isFile) {
    const file = await new Promise((res, rej) => entry.file(res, rej)).catch(() => null)
    if (file) out.push(file)
    return
  }
  if (!entry.isDirectory) return
  const reader = entry.createReader()
  // readEntries returns results in batches; keep calling until it is empty.
  for (;;) {
    const batch = await new Promise((res, rej) => reader.readEntries(res, rej)).catch(() => [])
    if (!batch.length) break
    for (const child of batch) {
      if (!child.name.startsWith('.')) await walkEntry(child, out)
    }
  }
}

/** Drop exact duplicates (same name and size) when files are added in several rounds. */
export function mergeFiles(existing, added) {
  const seen = new Set((existing || []).map(f => `${f.name}:${f.size}`))
  const out = [...(existing || [])]
  for (const f of added || []) {
    const key = `${f.name}:${f.size}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(f)
  }
  return out
}

export function totalBytes(files) {
  return (files || []).reduce((n, f) => n + (Number(f.size) || 0), 0)
}

// -- progress -----------------------------------------------------------------

/**
 * A rendering-ready snapshot of an upload: percent, rate, remaining time.
 *
 * `loaded`/`total` are bytes, `startedAt`/`now` are ms timestamps. Percent is
 * capped at 100 and the rate is the average since the start (stable on a
 * bursty link, which is what a user can plan around). `eta` is in seconds or
 * null until enough has been sent to say anything.
 */
export function progressSnapshot({ loaded = 0, total = 0, startedAt = 0, now = Date.now() } = {}) {
  const sent = Math.max(0, Number(loaded) || 0)
  const size = Math.max(0, Number(total) || 0)
  const elapsed = Math.max(0, (now - startedAt) / 1000)
  const percent = size > 0 ? Math.min(100, Math.floor((sent / size) * 100)) : 0
  const rate = elapsed > 0.5 ? sent / elapsed : 0
  let eta = null
  if (size > 0 && rate > 0 && sent < size && elapsed > 1) eta = Math.ceil((size - sent) / rate)
  return { loaded: sent, total: size, percent, rate, eta, elapsed, complete: size > 0 && sent >= size }
}

export function formatRate(bytesPerSecond) {
  const r = Number(bytesPerSecond) || 0
  if (r <= 0) return ''
  if (r < 1024) return `${r.toFixed(0)} B/s`
  if (r < 1024 * 1024) return `${(r / 1024).toFixed(0)} KB/s`
  return `${(r / (1024 * 1024)).toFixed(1)} MB/s`
}

export function formatEta(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return ''
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s}s left`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s left`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m left`
}

/** One-line status for the progress bar, by phase. */
export function progressLabel(phase, snapshot, { formatBytes = String } = {}) {
  switch (phase) {
    case UPLOAD_PHASES.uploading: {
      const s = snapshot || progressSnapshot()
      const parts = [`${formatBytes(s.loaded)} of ${formatBytes(s.total)}`]
      const rate = formatRate(s.rate)
      if (rate) parts.push(rate)
      const eta = formatEta(s.eta)
      if (eta) parts.push(eta)
      return parts.join(' · ')
    }
    case UPLOAD_PHASES.processing:
      return 'Upload complete — the server is registering the images…'
    case UPLOAD_PHASES.done:
      return 'Done'
    case UPLOAD_PHASES.failed:
      return 'Upload failed'
    case UPLOAD_PHASES.cancelled:
      return 'Upload cancelled'
    default:
      return ''
  }
}

/**
 * The small state machine the upload dialogs drive: call `start()` before
 * sending, feed `onProgress` into `update`, then `finish()` / `fail()` /
 * `cancel()`. `state` is a plain object so a Vue `reactive()` wrapper can hold it.
 */
export function createUploadTracker(now = () => Date.now(), wrap = s => s) {
  // `wrap` lets a UI make the state observable (Vue: pass `reactive`); the
  // tracker mutates the wrapped object so every change is seen.
  const state = wrap({
    phase: UPLOAD_PHASES.idle,
    loaded: 0,
    total: 0,
    startedAt: 0,
    snapshot: progressSnapshot(),
    controller: null,
  })
  const refresh = () => {
    state.snapshot = progressSnapshot({ loaded: state.loaded, total: state.total, startedAt: state.startedAt, now: now() })
  }
  return {
    state,
    start(total) {
      state.phase = UPLOAD_PHASES.uploading
      state.loaded = 0
      state.total = total || 0
      state.startedAt = now()
      state.controller = typeof AbortController === 'function' ? new AbortController() : null
      refresh()
      return state.controller ? state.controller.signal : undefined
    },
    update({ loaded, total }) {
      if (state.phase !== UPLOAD_PHASES.uploading && state.phase !== UPLOAD_PHASES.processing) return
      state.loaded = loaded
      if (total) state.total = total
      refresh()
      if (state.total > 0 && state.loaded >= state.total) state.phase = UPLOAD_PHASES.processing
    },
    finish() { state.phase = UPLOAD_PHASES.done; refresh() },
    fail() { state.phase = UPLOAD_PHASES.failed },
    cancel() {
      if (state.controller) state.controller.abort()
      state.phase = UPLOAD_PHASES.cancelled
    },
    reset() {
      state.phase = UPLOAD_PHASES.idle
      state.loaded = 0
      state.total = 0
      state.controller = null
      refresh()
    },
  }
}
