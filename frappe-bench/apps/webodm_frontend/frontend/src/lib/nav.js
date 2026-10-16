import {
  Folder,
  Images,
  LayoutGrid,
  Puzzle,
  Route,
  SlidersHorizontal,
  Store,
} from 'lucide-vue-next'

/**
 * The primary tab bar. Order is the order shown.
 */
export const PRIMARY_TABS = Object.freeze([
  { to: '/dashboard', label: 'Dashboard', icon: LayoutGrid },
  { to: '/projects', label: 'Projects', icon: Folder },
  // The reusable input-image library; a task picks one of these.
  { to: '/datasets', label: 'Datasets', icon: Images },
  // Mission planning before the flight: every plan of the organization.
  { to: '/flight-plans', label: 'Flight Plans', icon: Route },
  { to: '/presets', label: 'Presets', icon: SlidersHorizontal },
  { to: '/plugins', label: 'Plugins', icon: Puzzle },
  // Public catalog (layout: 'auto'); signed-in users get it as a tab so
  // install-only plugins are discoverable from inside the app.
  { to: '/marketplace', label: 'Marketplace', icon: Store },
])

// A project detail page is /project/:id — singular, so it is not a path prefix
// of the /projects tab. Matching it explicitly keeps Projects highlighted while
// the user is inside a project.
const PROJECT_DETAIL = /^\/project\/([^/]+)/
const PROJECT_TASK = /^\/project\/([^/]+)\/task\/([^/]+)\/(model|console)$/
// A project's flight plans, and one plan open in the planner.
const PROJECT_PLANS = /^\/project\/([^/]+)\/plans(\/[^/]+)?$/

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
  const tabs = [
    { to: `/project/${projectId}`, label: 'Map' },
    { to: `/project/${projectId}/plans`, label: 'Flight plans' },
  ]

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
  // A plan open in the planner keeps the Flight plans tab active.
  const plans = p.match(PROJECT_PLANS)
  if (plans) return `/project/${plans[1]}/plans`
  const hit = tabs.find(t => t.to === p)
  return hit ? hit.to : null
}
