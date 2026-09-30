// Dataset library client + the pure helpers the Datasets pages and the task
// dialog share. A dataset is an organization-scoped, reusable set of input
// images; a task points at exactly one. Every request goes through the shared
// CSRF-carrying client so a state change is never sent without the token.

import { postMethod } from './api'
import { frappeErrorMessage } from './utils'

const API = 'webodm_core.api.dataset'

function csrfHeaders() {
  const h = {}
  if (window.csrf_token) h['X-Frappe-CSRF-Token'] = window.csrf_token
  return h
}

async function unwrap(res) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(frappeErrorMessage(err))
  }
  const data = await res.json().catch(() => ({}))
  return data.message !== undefined ? data.message : data
}

export const listDatasets = () => postMethod(`${API}.list_datasets`)
export const getDataset = name => postMethod(`${API}.get_dataset`, { name })
export const updateDataset = (name, { title, description }) =>
  postMethod(`${API}.update_dataset`, { name, title, description })
export const deleteDataset = name => postMethod(`${API}.delete_dataset`, { name })

/**
 * Upload images as a new dataset. Multipart (not JSON), so it bypasses the
 * JSON client; the CSRF header still travels with it.
 */
export function createDataset({ files, title, description }) {
  const form = new FormData()
  for (const f of files || []) form.append('files', f)
  if (title) form.append('title', title)
  if (description) form.append('description', description)
  return fetch(`/api/method/${API}.create_dataset`, {
    method: 'POST',
    headers: csrfHeaders(),
    body: form,
  }).then(unwrap)
}

/**
 * Create a task, either over an existing dataset (`dataset`) or from new
 * uploads (`files`, which become a dataset titled `datasetTitle`). Mirrors
 * `api.task.upload_images`, which accepts exactly one of the two.
 */
export function createTask({ projectId, dataset, files, datasetTitle, title, options }) {
  const form = new FormData()
  form.append('project_id', projectId)
  if (dataset) form.append('dataset', dataset)
  for (const f of files || []) form.append('files', f)
  if (datasetTitle) form.append('dataset_title', datasetTitle)
  if (title) form.append('title', title)
  if (options !== undefined) form.append('options', JSON.stringify(options))
  return fetch('/api/method/webodm_core.api.task.upload_images', {
    method: 'POST',
    headers: csrfHeaders(),
    body: form,
  }).then(unwrap)
}

export const listTasks = projectId =>
  postMethod('webodm_core.api.task.list_tasks', { project_id: projectId })

// -- pure helpers -------------------------------------------------------------

/** "0 B", "1.5 MB" ... for the dataset size column. */
export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || bytes === '') return '—'
  const b = Number(bytes)
  if (!Number.isFinite(b) || b < 0) return '—'
  if (b < 1024) return `${b} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = b / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`
}

/** "3 images" / "1 image" / "no images". */
export function imageCountLabel(n) {
  const c = Number(n) || 0
  if (c === 0) return 'no images'
  return `${c} image${c === 1 ? '' : 's'}`
}

/**
 * The thumbnail URL for a dataset image row. The backend puts one on every
 * row it serialises; older payloads (or rows built client-side) fall back to
 * the endpoint URL, and as a last resort to the original file.
 */
export function thumbnailUrl(row, size = 256) {
  if (!row) return ''
  if (row.thumbnail) {
    return size === 256 ? row.thumbnail : row.thumbnail.replace(/([?&]size=)\d+/, `$1${size}`)
  }
  if (row.parent && row.name) {
    return `/api/method/${API}.thumbnail?dataset=${encodeURIComponent(row.parent)}&image=${encodeURIComponent(row.name)}&size=${size}`
  }
  return row.image || ''
}

/**
 * Whether a delete error is the "still used by a task" refusal, so the UI
 * can show it as guidance rather than as a failure.
 */
export function isInUseError(err) {
  const msg = String(err?.message || err || '')
  return /still used by \d+ task/i.test(msg)
}

/**
 * The two ways to give a task its inputs. `existing` needs a chosen dataset,
 * `upload` needs at least one file — `taskInputsValid` says whether the
 * dialog may submit.
 */
export const INPUT_MODES = Object.freeze({ existing: 'existing', upload: 'upload' })

export function taskInputsValid({ mode, dataset, files }) {
  if (mode === INPUT_MODES.existing) return !!dataset
  if (mode === INPUT_MODES.upload) return !!(files && files.length)
  return false
}

/** Default dataset title for an upload made from the task dialog. */
export function defaultDatasetTitle(projectTitle, fileCount, now = new Date()) {
  const stamp = now.toISOString().slice(0, 10)
  const base = projectTitle ? `${projectTitle} images` : 'Images'
  return `${base} ${stamp} (${imageCountLabel(fileCount)})`
}

/** Newest first; falls back to title when creation is missing. */
export function sortDatasets(rows) {
  return [...(rows || [])].sort((a, b) => {
    const ta = a.creation ? Date.parse(a.creation.replace(' ', 'T')) : 0
    const tb = b.creation ? Date.parse(b.creation.replace(' ', 'T')) : 0
    if (tb !== ta) return tb - ta
    return String(a.title || '').localeCompare(String(b.title || ''))
  })
}
