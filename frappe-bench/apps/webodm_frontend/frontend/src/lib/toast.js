// Single import site for toasts. Re-exported (rather than imported directly in
// pages) so the toast library stays swappable without touching call sites, and
// so the `toast.success(...)` / `toast.error(...)` surface the pages already
// use keeps working unchanged.
import { toast } from 'vue-sonner'

export { toast }
