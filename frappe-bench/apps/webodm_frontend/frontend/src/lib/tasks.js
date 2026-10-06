// Task management after creation: edit settings, duplicate, (re)start.
//
// Thin client over `webodm_core.api.task` plus the pure helpers the map page
// uses to decide which buttons to show. The status rules mirror the backend
// (`EDITABLE_STATUSES` in api/task.py) so the UI never offers an action the
// server will refuse; the server remains the authority.

import { postMethod } from './api'

const API = 'webodm_core.api.task'

export const getTask = taskName => postMethod(`${API}.get_task_progress`, { task_name: taskName })
export const startTask = taskName => postMethod(`${API}.process_task`, { task_name: taskName })

/** Change the title and/or options; only the keys given are sent. */
export function updateTask(taskName, { title, options } = {}) {
  const body = { task_name: taskName }
  if (title !== undefined) body.title = title
  if (options !== undefined) body.options = options
  return postMethod(`${API}.update_task`, body)
}

/** Fresh Pending copy with the same dataset and options (title optional). */
export function duplicateTask(taskName, { title } = {}) {
  const body = { task_name: taskName }
  if (title) body.title = title
  return postMethod(`${API}.duplicate_task`, body)
}

// -- pure helpers -------------------------------------------------------------

/** Statuses in which the settings may change (Queued / Provisioning / Running are in flight). */
export const EDITABLE_STATUSES = Object.freeze(['Pending', 'Completed', 'Failed', 'Cancelled'])
export const IN_FLIGHT_STATUSES = Object.freeze(['Queued', 'Provisioning', 'Running'])

export function canEditTask(task) {
  if (!task) return false
  if (typeof task.can_edit === 'boolean') return task.can_edit
  return EDITABLE_STATUSES.includes(task.status)
}

export function isInFlight(task) {
  return !!task && IN_FLIGHT_STATUSES.includes(task.status)
}

/** Why editing is refused right now, for a tooltip; '' when it is allowed. */
export function editBlockedReason(task) {
  if (!task || canEditTask(task)) return ''
  return `Cancel the task before changing its settings (it is ${String(task.status || '').toLowerCase()})`
}

/**
 * Whether saving edited options should offer to run immediately, and what to
 * call that: a Pending task starts, a finished one re-runs (which wipes the
 * previous outputs — the backend's process_task does that).
 */
export function rerunLabel(task) {
  if (!task) return 'Save'
  if (task.status === 'Pending') return 'Save and start'
  return 'Save and re-run'
}

export function rerunWarning(task) {
  if (!task || task.status === 'Pending') return ''
  if (task.status === 'Completed') {
    return 'The current outputs come from the previous settings and stay until you re-run; re-running replaces them.'
  }
  return 'Re-running starts the task again with the saved settings.'
}

/**
 * `[{name, value}]` -> `{name: value}` for the options form. Accepts the
 * payload's decoded `options` list, a raw processing_options string of any
 * encoding depth, or a legacy dict. Unknown shapes decode to {}.
 */
export function optionsToValues(options) {
  let v = options
  for (let i = 0; i < 3 && typeof v === 'string'; i++) {
    try { v = JSON.parse(v) } catch { return {} }
  }
  if (Array.isArray(v)) {
    const out = {}
    for (const o of v) if (o && typeof o === 'object' && 'name' in o) out[o.name] = o.value
    return out
  }
  if (v && typeof v === 'object') return { ...v }
  return {}
}

/** `{name: value}` -> `[{name, value}]`, dropping empty strings/null/undefined like the Add Task dialog does. */
export function valuesToOptions(values) {
  const out = []
  for (const [name, value] of Object.entries(values || {})) {
    if (value === '' || value === null || value === undefined) continue
    out.push({ name, value })
  }
  return out
}

/** Default title for a duplicate, unique within `existingTitles` (mirrors api.task.duplicate_title). */
export function duplicateTitle(title, existingTitles = []) {
  const base = String(title || 'Task').trim() || 'Task'
  const taken = new Set(existingTitles)
  let candidate = `${base} (copy)`
  for (let n = 2; taken.has(candidate); n++) candidate = `${base} (copy ${n})`
  return candidate
}

/** Short description of a dataset's ground control for a task card / dialog. */
export function gcpSummaryText(gcp) {
  if (!gcp) return ''
  const pts = Number(gcp.points) || 0
  const parts = [`${pts} point${pts === 1 ? '' : 's'}`]
  if (gcp.crs) parts.push(gcp.crs)
  if (Array.isArray(gcp.unknown_images) && gcp.unknown_images.length) {
    parts.push(`${gcp.unknown_images.length} image name${gcp.unknown_images.length === 1 ? '' : 's'} not in the dataset`)
  }
  return parts.join(' · ')
}
