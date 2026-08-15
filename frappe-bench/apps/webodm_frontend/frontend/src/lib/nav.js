import {
  FileText,
  Folder,
  LayoutGrid,
  Puzzle,
  Settings,
  SlidersHorizontal,
} from 'lucide-vue-next'

/**
 * The primary tab bar. Order is the order shown.
 */
export const PRIMARY_TABS = Object.freeze([
  { to: '/dashboard', label: 'Dashboard', icon: LayoutGrid },
  { to: '/projects', label: 'Projects', icon: Folder },
  { to: '/presets', label: 'Presets', icon: SlidersHorizontal },
  { to: '/invoices', label: 'Invoices', icon: FileText },
  { to: '/settings', label: 'Settings', icon: Settings },
  { to: '/plugins', label: 'Plugins', icon: Puzzle },
])

// A project detail page is /project/:id — singular, so it is not a path prefix
// of the /projects tab. Matching it explicitly keeps Projects highlighted while
// the user is inside a project.
const PROJECT_DETAIL = /^\/project\/([^/]+)/
const PROJECT_TASK = /^\/project\/([^/]+)\/task\/([^/]+)\/(model|console)$/

// Strip a trailing slash so "/projects/" and "/projects" behave the same.
// Returns '' for nullish input so every caller can treat the result as a string.
function normalize(path) {
  const p = String(path ?? '')
  return p.length > 1 && p.endsWith('/') ? p.slice(0, -1) : p
}

/**
 * The `to` of the primary tab that should render as active for a path, or null.
 * Derived from the path on every call — never cached, never stored — so deep
 * links and refreshes highlight correctly.
 */
export function activePrimaryTab(path) {
  const p = normalize(path)
  if (!p) return null
  if (PROJECT_DETAIL.test(p)) return '/projects'
  for (const tab of PRIMARY_TABS) {
    // Exact match, or a genuine child segment — a prefix test alone would let
    // "/presets-archive" activate "/presets".
    if (p === tab.to || p.startsWith(tab.to + '/')) return tab.to
  }
  return null
}

/**
 * The secondary tab row for project-detail routes: [] anywhere else.
 * 3D Model and Console are task-scoped, so they only appear once the path
 * carries a task id.
 */
export function secondaryTabs(path) {
  const p = normalize(path)
  const detail = p.match(PROJECT_DETAIL)
  if (!detail) return []

  const projectId = detail[1]
  const tabs = [{ to: `/project/${projectId}`, label: 'Map' }]

  const task = p.match(PROJECT_TASK)
  if (task) {
    const taskId = task[2]
    tabs.push(
      { to: `/project/${projectId}/task/${taskId}/model`, label: '3D Model' },
      { to: `/project/${projectId}/task/${taskId}/console`, label: 'Console' },
    )
  }
  return tabs
}

/**
 * The `to` of the active secondary tab for a path, or null.
 */
export function activeSecondaryTab(path) {
  const p = normalize(path)
  const tabs = secondaryTabs(p)
  if (!tabs.length) return null
  const hit = tabs.find(t => t.to === p)
  return hit ? hit.to : null
}
