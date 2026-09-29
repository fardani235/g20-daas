<template>
  <MarketplaceFrame>
    <PageHeader
      title="Marketplace"
      description="Analysis plugins and other products from the platform and partner publishers. Free to install into your organization."
    />

    <div class="flex flex-wrap items-center gap-3" data-marketplace-filters>
      <div class="relative min-w-[16rem] flex-1">
        <Search class="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input v-model="q" class="pl-9" type="search" placeholder="Search products, publishers, categories…" aria-label="Search the marketplace" />
      </div>
      <Select v-model="kind" class="w-auto" aria-label="Filter by kind">
        <option value="">All kinds</option>
        <option v-for="(label, key) in KIND_LABELS" :key="key" :value="key">{{ label }}</option>
      </Select>
    </div>

    <div v-if="categories.length" class="flex flex-wrap gap-2" data-marketplace-categories>
      <button
        type="button"
        class="rounded-full border px-3 py-1 text-xs font-medium transition-colors"
        :class="category === '' ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:text-foreground'"
        @click="category = ''"
      >
        All
      </button>
      <button
        v-for="c in categories"
        :key="c.category_id"
        type="button"
        class="rounded-full border px-3 py-1 text-xs font-medium transition-colors"
        :class="category === c.category_id ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:text-foreground'"
        :title="c.description || c.label"
        @click="category = category === c.category_id ? '' : c.category_id"
      >
        {{ c.label }}
      </button>
    </div>

    <p v-if="loading" class="text-sm text-muted-foreground">Loading catalog…</p>
    <Alert v-else-if="error" variant="destructive" title="Could not load the marketplace">{{ error }}</Alert>

    <div v-else-if="visible.length" class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" data-marketplace-grid>
      <router-link
        v-for="p in visible"
        :key="p.product_id"
        :to="productRoute(p.product_id)"
        class="group flex flex-col rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/60"
        data-product-card
      >
        <div class="flex items-start gap-3">
          <img v-if="p.icon" :src="p.icon" alt="" class="size-12 flex-shrink-0 rounded-md object-cover" />
          <div v-else class="flex size-12 flex-shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <Puzzle class="size-6" />
          </div>
          <div class="min-w-0 flex-1">
            <h3 class="truncate font-medium text-foreground group-hover:text-primary">{{ p.title }}</h3>
            <p class="truncate text-xs text-muted-foreground">
              {{ p.publisher?.display_name }}
              <span v-if="p.publisher?.kind === 'First-party'" class="ml-1 rounded bg-primary/10 px-1 text-[10px] font-medium text-primary">Official</span>
            </p>
          </div>
        </div>
        <p class="mt-3 line-clamp-2 flex-1 text-sm text-muted-foreground">{{ p.summary }}</p>
        <div class="mt-3 flex flex-wrap items-center gap-1.5">
          <Badge variant="outline">{{ kindLabel(p.artifact_kind) }}</Badge>
          <Badge v-for="c in p.categories" :key="c.category_id">{{ c.label }}</Badge>
        </div>
        <div class="mt-3 flex items-center justify-between text-xs text-muted-foreground">
          <span v-if="p.latest_release">
            v{{ p.latest_release.version }} · {{ p.latest_release.license?.license_id }}
          </span>
          <span v-else>No release yet</span>
          <span v-if="p.install_count" :title="`${p.install_count} organization(s) installed this`">
            {{ p.install_count }} install{{ p.install_count === 1 ? '' : 's' }}
          </span>
        </div>
      </router-link>
    </div>

    <div v-else class="rounded-lg border border-dashed border-border p-10 text-center text-sm text-muted-foreground" data-marketplace-empty>
      <template v-if="products.length">No products match your search.</template>
      <template v-else>Nothing has been published yet.</template>
    </div>
  </MarketplaceFrame>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Puzzle, Search } from 'lucide-vue-next'
import { Alert, Badge, Input, Select } from '@/components/ui'
import MarketplaceFrame from '@/components/MarketplaceFrame.vue'
import PageHeader from '@/components/PageHeader.vue'
import { KIND_LABELS, filterProducts, kindLabel, listProducts, productRoute } from '@/lib/marketplace'

const products = ref([])
const categories = ref([])
const loading = ref(true)
const error = ref('')
const q = ref('')
const category = ref('')
const kind = ref('')

const visible = computed(() =>
  filterProducts(products.value, { q: q.value, category: category.value, kind: kind.value }),
)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await listProducts()
    products.value = data.products || []
    categories.value = data.categories || []
  } catch (e) {
    error.value = e.message || 'Request failed'
  } finally {
    loading.value = false
  }
}

load()
</script>
