const VARIANTS = {
  completed: 'success',
  failed: 'destructive',
  running: 'default',
  'in progress': 'default',
  pending: 'warning',
  queued: 'warning',
  cancelled: 'secondary',
  canceled: 'secondary',
  planned: 'outline',
}

/**
 * Badge variant for a project or task status. Accepts either spelling of
 * "cancelled" because WebODM Project uses "Cancelled" and WebODM Task uses
 * "Canceled". Unknown or missing statuses degrade to a neutral badge.
 */
export function statusVariant(status) {
  return VARIANTS[String(status ?? '').toLowerCase()] || 'secondary'
}
