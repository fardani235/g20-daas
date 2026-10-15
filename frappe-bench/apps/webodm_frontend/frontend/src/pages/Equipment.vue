<template>
  <div class="space-y-6">
    <PageHeader
      title="Drones and cameras"
      description="The aircraft and cameras flight plans are computed for. The built-in list holds published nominal figures; add your own equipment if it is missing or flies differently."
    >
      <template #actions>
        <Button variant="outline" @click="router.push('/flight-plans')">
          <ArrowLeft />
          Flight plans
        </Button>
        <Button v-if="tab === 'drones'" :disabled="!canEdit" data-add-drone @click="openDrone(null)">
          <Plus />
          Add drone
        </Button>
        <Button v-else :disabled="!canEdit" data-add-camera @click="openCamera(null)">
          <Plus />
          Add camera
        </Button>
      </template>
    </PageHeader>

    <div class="flex flex-wrap items-center gap-3">
      <div class="grid grid-cols-2 gap-1 rounded-md bg-muted p-1 text-sm" role="tablist" aria-label="Equipment">
        <button
          v-for="t in ['drones', 'cameras']"
          :key="t"
          type="button"
          role="tab"
          :aria-selected="tab === t"
          class="rounded px-4 py-1.5 capitalize transition-colors"
          :class="tab === t ? 'bg-card text-card-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'"
          :data-tab="t"
          @click="tab = t"
        >{{ t }} ({{ t === 'drones' ? drones.length : cameras.length }})</button>
      </div>
      <Input v-model="filter" class="h-9 max-w-xs" placeholder="Filter by name or manufacturer…" aria-label="Filter equipment" />
      <span class="text-xs text-muted-foreground">Built-in catalog as of {{ catalog?.catalog_as_of || '—' }}</span>
    </div>

    <p v-if="loading" class="py-12 text-center text-muted-foreground">Loading equipment…</p>
    <p v-else-if="error" class="py-12 text-center text-destructive">{{ error }}</p>

    <div v-else-if="tab === 'drones'" class="overflow-x-auto rounded-lg border border-border bg-card">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-muted-foreground">
            <th class="px-4 py-3 font-medium">Aircraft</th>
            <th class="px-4 py-3 font-medium">Flight time</th>
            <th class="px-4 py-3 font-medium">Mission speed</th>
            <th class="px-4 py-3 font-medium">Cameras</th>
            <th class="px-4 py-3 font-medium">Mission files</th>
            <th class="px-4 py-3 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in shownDrones" :key="d.id" class="border-b border-border align-top last:border-0" data-drone-row :data-source="d.source">
            <td class="px-4 py-3">
              <span class="font-medium text-card-foreground">{{ d.label }}</span>
              <p class="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                <Badge v-if="d.source === 'custom'" variant="default">Yours</Badge>
                <span>{{ AIRFRAME_LABELS[d.airframe] }}</span>
                <span v-if="d.rtk !== 'none'">· RTK {{ d.rtk === 'standard' ? '' : 'optional' }}</span>
              </p>
              <p v-if="d.notes" class="mt-1 max-w-md text-xs text-muted-foreground">{{ d.notes }}</p>
            </td>
            <td class="whitespace-nowrap px-4 py-3 text-muted-foreground">
              {{ d.max_flight_time_min }} min rated
              <p class="text-xs" title="Rated time × endurance factor × (1 − 25 % reserve)">≈ {{ usableMinutes(d) }} min usable</p>
            </td>
            <td class="whitespace-nowrap px-4 py-3 text-muted-foreground">
              up to {{ d.max_mission_speed_ms ?? d.max_speed_ms }} m/s
              <p v-if="d.max_wind_ms" class="text-xs">wind limit {{ d.max_wind_ms }} m/s</p>
              <p v-else class="text-xs">wind limit not published</p>
            </td>
            <td class="px-4 py-3 text-muted-foreground">
              <template v-if="d.cameras.includes('*')">Any camera</template>
              <ul v-else class="space-y-0.5">
                <li v-for="id in d.cameras" :key="id">{{ cameraLabel(id) }}</li>
              </ul>
            </td>
            <td class="px-4 py-3">
              <ul v-if="d.mission.waypoints && d.mission.formats.length" class="space-y-1">
                <li v-for="f in d.mission.formats" :key="f.id" class="flex flex-wrap items-center gap-1.5">
                  <Badge :variant="supportLevel(f.level).variant" :title="supportLevel(f.level).hint">{{ supportLevel(f.level).label }}</Badge>
                  <span class="text-xs text-muted-foreground" :title="f.note || ''">{{ formatLabel(f.id) }}<template v-if="f.app"> · {{ f.app }}</template></span>
                </li>
              </ul>
              <span v-else-if="!d.mission.waypoints" class="text-xs text-muted-foreground">No autonomous flight</span>
              <span v-else class="text-xs text-muted-foreground">None known</span>
            </td>
            <td class="whitespace-nowrap px-4 py-3 text-right">
              <template v-if="d.editable">
                <Button variant="ghost" size="icon" class="size-8" title="Edit" @click="openDrone(d)">
                  <Pencil /><span class="sr-only">Edit {{ d.label }}</span>
                </Button>
                <Button variant="ghost" size="icon" class="size-8 text-muted-foreground hover:text-destructive" title="Delete" @click="removeDrone(d)">
                  <Trash2 /><span class="sr-only">Delete {{ d.label }}</span>
                </Button>
              </template>
              <Button v-else variant="ghost" size="sm" :disabled="!canEdit" title="Make an editable copy with your own figures" @click="openDrone(d, true)">
                <Copy />
                Copy
              </Button>
            </td>
          </tr>
          <tr v-if="!shownDrones.length">
            <td colspan="6" class="px-4 py-10 text-center text-muted-foreground">No aircraft matches “{{ filter }}”.</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else class="overflow-x-auto rounded-lg border border-border bg-card">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-muted-foreground">
            <th class="px-4 py-3 font-medium">Camera</th>
            <th class="px-4 py-3 font-medium">Sensor</th>
            <th class="px-4 py-3 font-medium">Image</th>
            <th class="px-4 py-3 font-medium">Lens</th>
            <th class="px-4 py-3 font-medium" title="Ground sample distance from 100 m">GSD at 100 m</th>
            <th class="px-4 py-3 font-medium">Photo interval</th>
            <th class="px-4 py-3 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in shownCameras" :key="c.id" class="border-b border-border align-top last:border-0" data-camera-row :data-source="c.source">
            <td class="px-4 py-3">
              <span class="font-medium text-card-foreground">{{ c.label }}</span>
              <p class="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                <Badge v-if="c.source === 'custom'" variant="default">Yours</Badge>
                <span>{{ { mechanical: 'Mechanical shutter', global: 'Global shutter', electronic: 'Electronic shutter' }[c.shutter] }}</span>
              </p>
              <p v-if="c.notes" class="mt-1 max-w-md text-xs text-muted-foreground">{{ c.notes }}</p>
            </td>
            <td class="px-4 py-3 text-muted-foreground">{{ c.sensor_width_mm }} × {{ c.sensor_height_mm }} mm</td>
            <td class="px-4 py-3 text-muted-foreground">{{ c.image_width_px }} × {{ c.image_height_px }}</td>
            <td class="px-4 py-3 text-muted-foreground">{{ c.focal_length_mm }} mm</td>
            <td class="px-4 py-3 text-muted-foreground">{{ formatGsd(gsdCm(c, 100)) }}</td>
            <td class="px-4 py-3 text-muted-foreground">{{ c.min_interval_s ? `${c.min_interval_s} s` : 'not published' }}</td>
            <td class="whitespace-nowrap px-4 py-3 text-right">
              <template v-if="c.editable">
                <Button variant="ghost" size="icon" class="size-8" title="Edit" @click="openCamera(c)">
                  <Pencil /><span class="sr-only">Edit {{ c.label }}</span>
                </Button>
                <Button variant="ghost" size="icon" class="size-8 text-muted-foreground hover:text-destructive" title="Delete" @click="removeCamera(c)">
                  <Trash2 /><span class="sr-only">Delete {{ c.label }}</span>
                </Button>
              </template>
              <Button v-else variant="ghost" size="sm" :disabled="!canEdit" title="Make an editable copy" @click="openCamera(c, true)">
                <Copy />
                Copy
              </Button>
            </td>
          </tr>
          <tr v-if="!shownCameras.length">
            <td colspan="7" class="px-4 py-10 text-center text-muted-foreground">No camera matches “{{ filter }}”.</td>
          </tr>
        </tbody>
      </table>
    </div>

    <p class="max-w-3xl text-xs leading-relaxed text-muted-foreground">
      Built-in figures are the manufacturers' published nominal values and can differ from your aircraft: firmware,
      payload, propellers, temperature and battery age all change them. “Not published” means the planner skips the
      check that needs the figure instead of guessing. Mission file support is stated per aircraft:
      <strong class="text-foreground">Supported</strong> = the flight app documents the import,
      <strong class="text-foreground">Unofficial</strong> = it works through a procedure the manufacturer does not document,
      <strong class="text-foreground">Boundary import</strong> = the app plans its own lines from the survey outline.
    </p>

    <DroneDialog
      v-model:open="showDrone"
      :drone="droneTarget"
      :copy="copying"
      :cameras="cameras"
      :ecosystems="catalog?.ecosystems || []"
      @saved="refresh"
    />
    <CameraDialog v-model:open="showCamera" :camera="cameraTarget" :copy="copying" @saved="refresh" />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, Copy, Pencil, Plus, Trash2 } from 'lucide-vue-next'
import { Badge, Button, Input } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import DroneDialog from '@/components/flightplan/DroneDialog.vue'
import CameraDialog from '@/components/flightplan/CameraDialog.vue'
import { toast } from '@/lib/toast'
import {
  AIRFRAME_LABELS, deleteCamera, deleteDrone, formatGsd, getCatalog, gsdCm, supportLevel,
} from '@/lib/flightplan'

const router = useRouter()
const catalog = ref(null)
const loading = ref(true)
const error = ref('')
const tab = ref('drones')
const filter = ref('')
const showDrone = ref(false)
const showCamera = ref(false)
const droneTarget = ref(null)
const cameraTarget = ref(null)
const copying = ref(false)

const drones = computed(() => catalog.value?.drones || [])
const cameras = computed(() => catalog.value?.cameras || [])
const canEdit = computed(() => !!catalog.value?.can_edit_profiles)
const matches = label => !filter.value.trim() || label.toLowerCase().includes(filter.value.trim().toLowerCase())
// The organization's own profiles first, then the catalog by name.
const order = (a, b) => (a.source === b.source ? a.label.localeCompare(b.label) : a.source === 'custom' ? -1 : 1)
const shownDrones = computed(() => drones.value.filter(d => matches(d.label)).sort(order))
const shownCameras = computed(() => cameras.value.filter(c => matches(c.label)).sort(order))

const cameraLabel = id => cameras.value.find(c => c.id === id)?.label || id
const formatLabel = id => catalog.value?.formats.find(f => f.id === id)?.label || id
const usableMinutes = d => Math.round(d.max_flight_time_min * (d.endurance_factor || 0.8) * 0.75)

async function refresh() {
  try {
    catalog.value = await getCatalog()
    error.value = ''
  } catch (e) {
    error.value = e.message || 'Failed to load the equipment catalog'
  } finally {
    loading.value = false
  }
}
refresh()

function openDrone(d, copy = false) {
  droneTarget.value = d
  copying.value = copy
  showDrone.value = true
}

function openCamera(c, copy = false) {
  cameraTarget.value = c
  copying.value = copy
  showCamera.value = true
}

async function removeDrone(d) {
  if (!confirm(`Delete the drone profile "${d.label}"? Saved plans keep the figures they were planned with.`)) return
  try {
    await deleteDrone(d.docname)
    toast.success('Drone profile deleted')
    await refresh()
  } catch (e) {
    toast.error(e.message || 'Could not delete the drone profile')
  }
}

async function removeCamera(c) {
  if (!confirm(`Delete the camera profile "${c.label}"?`)) return
  try {
    await deleteCamera(c.docname)
    toast.success('Camera profile deleted')
    await refresh()
  } catch (e) {
    toast.error(e.message || 'Could not delete the camera profile')
  }
}
</script>
