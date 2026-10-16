<script setup>
import { ref } from 'vue'
import { LoaderCircle, MapPin, Search, X } from 'lucide-vue-next'
import { searchPlace } from '@/lib/flightplan'

// Find the survey site: an address, a place name or coordinates. Coordinates
// are recognised on the server without any external lookup, so a single
// coordinate result is taken at once.
const emit = defineEmits(['select'])

const query = ref('')
const results = ref([])
const message = ref('')
const attribution = ref('')
const loading = ref(false)
const open = ref(false)
let token = 0

async function run() {
  const q = query.value.trim()
  if (!q) return
  loading.value = true
  message.value = ''
  const mine = ++token
  try {
    const out = await searchPlace(q)
    if (mine !== token) return
    results.value = out.results || []
    attribution.value = out.attribution || ''
    message.value = out.message || (results.value.length ? '' : 'Nothing found. Try a nearby town, or enter coordinates as latitude, longitude.')
    if (results.value.length === 1 && results.value[0].source === 'coordinates') {
      choose(results.value[0])
      return
    }
    open.value = true
  } catch (e) {
    if (mine !== token) return
    results.value = []
    message.value = e.message || 'Search failed'
    open.value = true
  } finally {
    if (mine === token) loading.value = false
  }
}

function choose(result) {
  emit('select', result)
  results.value = []
  // A coordinate pair read in an unexpected order says so; show that.
  message.value = result.note || ''
  open.value = !!result.note
}

function reset() {
  query.value = ''
  results.value = []
  message.value = ''
  open.value = false
}
</script>

<template>
  <div class="relative w-full max-w-md" data-place-search>
    <form
      class="flex items-center gap-1 rounded-lg border border-border bg-card p-1 shadow-md"
      role="search"
      @submit.prevent="run"
    >
      <Search class="ml-2 size-4 flex-shrink-0 text-muted-foreground" aria-hidden="true" />
      <input
        v-model="query"
        type="search"
        class="h-8 min-w-0 flex-1 border-0 bg-transparent px-1 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-0"
        placeholder="Address, place or coordinates (lat, lng)"
        aria-label="Find the survey site by address, place or coordinates"
        autocomplete="off"
        @focus="open = results.length > 0 || !!message"
        @keydown.esc="open = false"
      />
      <button
        v-if="query"
        type="button"
        class="rounded p-1 text-muted-foreground hover:text-foreground"
        title="Clear search"
        @click="reset"
      >
        <X class="size-4" />
        <span class="sr-only">Clear search</span>
      </button>
      <button
        type="submit"
        class="inline-flex h-8 items-center rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        :disabled="loading || !query.trim()"
      >
        <LoaderCircle v-if="loading" class="size-3.5 animate-spin" />
        <span v-else>Find</span>
      </button>
    </form>

    <div
      v-if="open && (results.length || message)"
      class="absolute left-0 right-0 top-full z-10 mt-1 overflow-hidden rounded-lg border border-border bg-card text-sm shadow-lg"
      data-place-results
    >
      <ul v-if="results.length" class="max-h-64 overflow-y-auto">
        <li v-for="(r, i) in results" :key="i">
          <button
            type="button"
            class="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-accent"
            @click="choose(r)"
          >
            <MapPin class="mt-0.5 size-4 flex-shrink-0 text-primary" aria-hidden="true" />
            <span class="min-w-0">
              <span class="block break-words text-foreground">{{ r.label }}</span>
              <span v-if="r.kind" class="text-xs text-muted-foreground">{{ r.kind }}</span>
            </span>
          </button>
        </li>
      </ul>
      <p v-if="message" class="px-3 py-2 text-xs text-muted-foreground" role="status">{{ message }}</p>
      <p v-if="attribution && results.length" class="border-t border-border px-3 py-1 text-[11px] text-muted-foreground">
        {{ attribution }}
      </p>
    </div>
  </div>
</template>
