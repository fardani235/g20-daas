import { ref } from 'vue'
import { fetchOptions } from '@/lib/presets'

// Map a NodeODM /options catalog entry to a form field kind. NodeODM types seen
// in practice: bool, int, float, enum (with a `domain` list), string.
export function fieldType(option) {
  const t = (option?.type || '').toLowerCase()
  if (t === 'bool') return 'checkbox'
  if (t === 'enum' || Array.isArray(option?.domain)) return 'select'
  if (t === 'int' || t === 'float') return 'number'
  return 'text'
}

export function useOdmOptions() {
  const catalog = ref([])
  const error = ref('')
  const loading = ref(false)

  async function load() {
    loading.value = true
    error.value = ''
    try {
      catalog.value = await fetchOptions()
    } catch (e) {
      error.value = e.message || 'Could not load options'
      catalog.value = []
    } finally {
      loading.value = false
    }
  }

  // Fill in enum/select defaults for any option the caller hasn't already set.
  // A native <select> renders its first <option> as selected but never syncs
  // that back to the model, so an untouched enum would be dropped on submit and
  // the shown value would silently differ from what ODM receives. Seeding the
  // option's own default (or first domain entry) keeps shown == submitted.
  // Only fills keys that are still undefined, so preset/user values win.
  function seedEnumDefaults(values) {
    for (const opt of catalog.value) {
      if (fieldType(opt) !== 'select') continue
      if (values[opt.name] !== undefined) continue
      const fallback = opt.value ?? (Array.isArray(opt.domain) ? opt.domain[0] : undefined)
      if (fallback !== undefined) values[opt.name] = fallback
    }
  }

  return { catalog, error, loading, load, fieldType, seedEnumDefaults }
}
