<template>
  <MarketplaceFrame>
    <router-link to="/marketplace" class="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
      <ArrowLeft class="size-4" /> Marketplace
    </router-link>

    <p v-if="loading" class="text-sm text-muted-foreground">Loading product…</p>
    <Alert v-else-if="error" variant="destructive" title="Product not available">{{ error }}</Alert>

    <template v-else-if="product">
      <!-- Header -->
      <div class="flex flex-wrap items-start gap-4" data-product-header>
        <img v-if="product.icon" :src="product.icon" alt="" class="size-16 rounded-lg object-cover" />
        <div v-else class="flex size-16 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <Puzzle class="size-8" />
        </div>
        <div class="min-w-0 flex-1">
          <h1 class="text-2xl font-semibold tracking-tight text-foreground">{{ product.title }}</h1>
          <p class="mt-1 text-sm text-muted-foreground">
            by
            <a v-if="product.publisher?.website" :href="product.publisher.website" target="_blank" rel="noopener noreferrer" class="text-foreground hover:underline">{{ product.publisher.display_name }}</a>
            <span v-else class="text-foreground">{{ product.publisher?.display_name }}</span>
            <Badge v-if="product.publisher?.kind === 'First-party'" variant="default" class="ml-1">Official</Badge>
            <Badge v-else variant="outline" class="ml-1">Partner</Badge>
          </p>
          <p v-if="product.summary" class="mt-2 text-sm text-muted-foreground">{{ product.summary }}</p>
          <div class="mt-3 flex flex-wrap gap-1.5">
            <Badge variant="outline">{{ kindLabel(product.artifact_kind) }}</Badge>
            <Badge v-for="c in product.categories" :key="c.category_id">{{ c.label }}</Badge>
          </div>
        </div>
      </div>

      <div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <!-- Main column -->
        <div class="space-y-8">
          <section v-if="product.description" data-section="overview">
            <h2 class="mb-2 text-lg font-semibold text-foreground">Overview</h2>
            <div class="prose prose-sm max-w-none text-foreground dark:prose-invert" v-html="renderMarkdown(product.description)" />
          </section>

          <section data-section="versions">
            <h2 class="mb-2 text-lg font-semibold text-foreground">Versions</h2>
            <div v-if="product.releases?.length" class="overflow-hidden rounded-lg border border-border bg-card">
              <table class="w-full text-sm">
                <thead>
                  <tr class="border-b border-border text-left text-muted-foreground">
                    <th class="px-4 py-2 font-medium">Version</th>
                    <th class="px-4 py-2 font-medium">Published</th>
                    <th class="px-4 py-2 font-medium">License</th>
                    <th class="hidden px-4 py-2 font-medium md:table-cell">Size</th>
                    <th class="px-4 py-2 text-right font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="r in product.releases" :key="r.name" class="border-b border-border last:border-0 align-top" data-release-row>
                    <td class="px-4 py-3">
                      <div class="flex items-center gap-2 font-medium text-foreground">
                        v{{ r.version }}
                        <Badge v-if="r.status === 'Yanked'" variant="warning">Yanked</Badge>
                        <Badge v-else-if="installedRelease === r.name" variant="success">Installed</Badge>
                      </div>
                      <p v-if="r.release_notes" class="mt-1 whitespace-pre-line text-xs text-muted-foreground">{{ r.release_notes }}</p>
                      <p class="mt-1 font-mono text-[11px] text-muted-foreground" :title="r.artifact_hash">sha256 {{ shortHash(r.artifact_hash) }}</p>
                    </td>
                    <td class="px-4 py-3 text-muted-foreground">{{ formatVersionDate(r.published_on) }}</td>
                    <td class="px-4 py-3">
                      <a v-if="r.license?.url" :href="r.license.url" target="_blank" rel="noopener noreferrer" class="text-foreground hover:underline">{{ r.license.license_id }}</a>
                      <span v-else>{{ r.license?.license_id }}</span>
                      <p v-if="r.license_notes" class="mt-1 text-xs text-muted-foreground">{{ r.license_notes }}</p>
                    </td>
                    <td class="hidden px-4 py-3 text-muted-foreground md:table-cell">{{ formatBytes(r.artifact_size) }}</td>
                    <td class="px-4 py-3 text-right">
                      <div class="flex flex-wrap justify-end gap-1">
                        <Button
                          v-if="canInstall && r.installable && installedRelease !== r.name"
                          size="sm"
                          variant="outline"
                          :loading="busy === r.name"
                          :disabled="!!busy"
                          @click="install(r.name)"
                        >
                          Install
                        </Button>
                        <a v-if="r.download_url" :href="r.download_url" class="inline-flex" :title="`Download v${r.version}`">
                          <Button size="sm" variant="ghost"><Download class="size-4" /></Button>
                        </a>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else class="text-sm text-muted-foreground">No versions have been published yet.</p>
          </section>

          <section v-if="latestLicense" data-section="license">
            <h2 class="mb-2 text-lg font-semibold text-foreground">License</h2>
            <div class="rounded-lg border border-border bg-card p-4 text-sm">
              <p class="font-medium text-foreground">
                {{ latestLicense.name }}
                <span class="text-muted-foreground">({{ latestLicense.license_id }})</span>
              </p>
              <p v-if="latestLicense.summary" class="mt-1 text-muted-foreground">{{ latestLicense.summary }}</p>
              <p v-if="product.latest_release?.license_notes" class="mt-2 text-muted-foreground">
                <span class="font-medium text-foreground">Notes:</span> {{ product.latest_release.license_notes }}
              </p>
              <p class="mt-2 text-xs text-muted-foreground">
                <template v-if="product.allow_anonymous_download">
                  The publisher allows anyone to download this artifact; the license permits redistribution.
                </template>
                <template v-else>
                  Available to signed-in organizations under the terms above. Not offered for anonymous download.
                </template>
              </p>
              <a v-if="latestLicense.url" :href="latestLicense.url" target="_blank" rel="noopener noreferrer" class="mt-2 inline-flex items-center gap-1 text-xs text-primary hover:underline">
                Full license text <ExternalLink class="size-3" />
              </a>
            </div>
          </section>

          <section v-if="product.docs || product.docs_url || product.homepage_url || product.support_url" data-section="docs">
            <h2 class="mb-2 text-lg font-semibold text-foreground">Documentation</h2>
            <div v-if="product.docs" class="prose prose-sm max-w-none text-foreground dark:prose-invert" v-html="renderMarkdown(product.docs)" />
            <ul class="mt-3 flex flex-wrap gap-4 text-sm">
              <li v-if="product.docs_url"><a :href="product.docs_url" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-primary hover:underline">Documentation <ExternalLink class="size-3" /></a></li>
              <li v-if="product.homepage_url"><a :href="product.homepage_url" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-primary hover:underline">Homepage <ExternalLink class="size-3" /></a></li>
              <li v-if="product.support_url"><a :href="product.support_url" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-primary hover:underline">Support <ExternalLink class="size-3" /></a></li>
            </ul>
          </section>

          <section v-if="product.media?.length" data-section="media">
            <h2 class="mb-2 text-lg font-semibold text-foreground">Media</h2>
            <div class="grid gap-3 sm:grid-cols-2">
              <figure v-for="(m, i) in product.media" :key="i" class="overflow-hidden rounded-lg border border-border bg-card">
                <img :src="m.url" :alt="m.caption || product.title" class="aspect-video w-full object-cover" loading="lazy" />
                <figcaption v-if="m.caption" class="px-3 py-2 text-xs text-muted-foreground">{{ m.caption }}</figcaption>
              </figure>
            </div>
          </section>

          <section v-if="manifest" data-section="manifest">
            <h2 class="mb-2 text-lg font-semibold text-foreground">Plugin details</h2>
            <dl class="grid gap-x-6 gap-y-2 rounded-lg border border-border bg-card p-4 text-sm sm:grid-cols-2">
              <dt class="text-muted-foreground">Plugin id</dt>
              <dd class="font-mono text-foreground">{{ manifest.id }}</dd>
              <dt class="text-muted-foreground">Output</dt>
              <dd class="text-foreground">{{ manifest.output_kind }}<span v-if="manifest.render_kind"> · {{ manifest.render_kind }}</span></dd>
              <dt class="text-muted-foreground">Inputs</dt>
              <dd class="text-foreground">
                <span v-for="(inp, i) in manifest.inputs" :key="inp.name">
                  <span v-if="i">, </span>{{ inp.label || inp.name }}
                  <span class="text-muted-foreground">({{ (inp.datasets || []).join('/') }}<span v-if="inp.optional">, optional</span>)</span>
                </span>
              </dd>
              <dt class="text-muted-foreground">Parameters</dt>
              <dd class="text-foreground">{{ manifest.parameters?.length ? manifest.parameters.join(', ') : 'none' }}</dd>
            </dl>
          </section>
        </div>

        <!-- Install panel -->
        <aside class="lg:sticky lg:top-20 lg:self-start">
          <div class="space-y-3 rounded-lg border border-border bg-card p-4" data-install-panel :data-state="state">
            <div v-if="product.latest_release" class="text-sm">
              <p class="font-medium text-foreground">Latest: v{{ product.latest_release.version }}</p>
              <p class="text-xs text-muted-foreground">
                {{ formatVersionDate(product.latest_release.published_on) }} · {{ formatBytes(product.latest_release.artifact_size) }}
                · {{ product.latest_release.license?.license_id }}
              </p>
            </div>
            <p v-else class="text-sm text-muted-foreground">No installable version yet.</p>

            <!-- Signed out -->
            <template v-if="state === 'guest'">
              <router-link :to="{ path: '/login', query: { redirect: $route.fullPath } }" class="block">
                <Button class="w-full">Sign in to install</Button>
              </router-link>
              <a v-if="download.canDownload" :href="download.url" class="block">
                <Button variant="outline" class="w-full"><Download class="mr-2 size-4" /> Download v{{ product.latest_release.version }}</Button>
              </a>
              <p class="text-xs text-muted-foreground">
                <template v-if="download.canDownload">
                  Free and open: install it into your organization, or download the package directly.
                </template>
                <template v-else-if="product.latest_release">
                  Sign in and install this into your organization. The publisher does not offer this package for download without an account.
                </template>
              </p>
            </template>

            <!-- Signed in, no organization -->
            <template v-else-if="state === 'no-org'">
              <router-link to="/onboarding" class="block"><Button class="w-full" variant="outline">Set up an organization</Button></router-link>
              <p class="text-xs text-muted-foreground">Products are installed into an organization. Create or join one first.</p>
            </template>

            <!-- Member -->
            <template v-else-if="state === 'member'">
              <p class="text-sm text-muted-foreground">Only organization admins can install products. Ask your admin to install this one.</p>
              <a v-if="download.canDownload" :href="download.url" class="block">
                <Button variant="outline" class="w-full"><Download class="mr-2 size-4" /> Download</Button>
              </a>
            </template>

            <template v-else-if="state === 'unavailable'">
              <Button class="w-full" disabled>Install</Button>
            </template>

            <!-- Admin -->
            <template v-else>
              <Button v-if="state === 'install'" class="w-full" :loading="busy === 'latest'" :disabled="!!busy" @click="install()">
                Install into {{ orgLabel }}
              </Button>
              <Button v-else-if="state === 'update'" class="w-full" :loading="busy === 'latest'" :disabled="!!busy" @click="install()">
                Update to v{{ product.latest_release.version }}
              </Button>
              <div v-if="entitlement" class="rounded-md bg-success/10 px-3 py-2 text-xs text-success" data-installed-note>
                Installed v{{ entitlement.version }} in {{ orgLabel }}
                <span v-if="state === 'update'" class="text-warning"> · update available</span>
              </div>
              <router-link v-if="entitlement" to="/plugins" class="block">
                <Button variant="outline" class="w-full">Open in Plugins</Button>
              </router-link>
              <Button v-if="entitlement" variant="ghost" class="w-full text-destructive" :disabled="!!busy" @click="confirmUninstall = true">
                Uninstall
              </Button>
              <a v-if="download.canDownload" :href="download.url" class="block text-center text-xs text-muted-foreground hover:text-foreground">
                Download the package
              </a>
            </template>

            <p v-if="product.install_count" class="border-t border-border pt-3 text-xs text-muted-foreground">
              Installed by {{ product.install_count }} organization{{ product.install_count === 1 ? '' : 's' }}
            </p>
          </div>
        </aside>
      </div>

      <Dialog
        :open="confirmUninstall"
        title="Uninstall this product?"
        :description="`This removes ${product.title} from ${orgLabel}, including its settings and all of its run results.`"
        @update:open="confirmUninstall = $event"
      >
        <template #footer>
          <Button variant="outline" @click="confirmUninstall = false">Cancel</Button>
          <Button variant="destructive" :loading="busy === 'uninstall'" @click="uninstall">Uninstall</Button>
        </template>
      </Dialog>
    </template>
  </MarketplaceFrame>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft, Download, ExternalLink, Puzzle } from 'lucide-vue-next'
import { Alert, Badge, Button, Dialog } from '@/components/ui'
import MarketplaceFrame from '@/components/MarketplaceFrame.vue'
import { toast } from '@/lib/toast'
import {
  downloadPolicy, formatVersionDate, getProduct, installProduct, installState, kindLabel,
  shortHash, uninstallProduct,
} from '@/lib/marketplace'
import { renderMarkdown } from '@/lib/markdownLite'
import { formatBytes } from '@/lib/rasterMetadata'

const route = useRoute()
const product = ref(null)
const loading = ref(true)
const error = ref('')
const busy = ref('')
const confirmUninstall = ref(false)

const state = computed(() => installState(product.value))
const download = computed(() => downloadPolicy(product.value))
const entitlement = computed(() => product.value?.viewer?.entitlement || null)
const installedRelease = computed(() => entitlement.value?.release || null)
const canInstall = computed(() => !!product.value?.viewer?.can_install)
const orgLabel = computed(() => product.value?.viewer?.organization || 'your organization')
const latestLicense = computed(() => product.value?.latest_release?.license || null)
const manifest = computed(() => {
  const m = product.value?.latest_release?.manifest
  return m && m.id ? m : null
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    product.value = await getProduct(route.params.product)
  } catch (e) {
    product.value = null
    error.value = e.message || 'Request failed'
  } finally {
    loading.value = false
  }
}

async function install(release) {
  busy.value = release || 'latest'
  try {
    const result = await installProduct(product.value.product_id, release)
    toast.success(`Installed ${product.value.title} v${result.version}`)
    await load()
  } catch (e) {
    toast.error(e.message || 'Install failed')
  } finally {
    busy.value = ''
  }
}

async function uninstall() {
  busy.value = 'uninstall'
  try {
    await uninstallProduct(product.value.product_id)
    toast.success(`Uninstalled ${product.value.title}`)
    confirmUninstall.value = false
    await load()
  } catch (e) {
    toast.error(e.message || 'Uninstall failed')
  } finally {
    busy.value = ''
  }
}

watch(() => route.params.product, load)
load()
</script>
