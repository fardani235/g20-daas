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

  return { catalog, error, loading, load, fieldType }
}
