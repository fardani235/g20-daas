<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Layers, LoaderCircle, X } from 'lucide-vue-next'
import { Button } from '@/components/ui'
import { CAMERA_MODES } from '@/lib/flightsim/cameraRig'
import { terrainLabel } from '@/lib/flightsim/terrain'
import { WebGLUnavailable, createSimScene } from '@/lib/flightsim/scene'

// The 3D pane. It draws whatever state the controller hands it (`onFrame`),
// the same one the 2D map draws; the buttons here only move the camera and
// switch layers. Loaded on demand, with three.js, when a simulation opens.
const props = defineProps({
  simulation: { type: Object, required: true },
  onFrame: { type: Function, required: true },          // subscribe(fn) -> unsubscribe
  onCoverage: { type: Function, required: true },
  coverageCanvas: { type: Object, default: null },
  coverageMode: { type: String, default: 'covered' },
  basemap: { type: Object, default: null },             // { id, url, maxZoom, attribution }
  ortho: { type: Object, default: null },               // { task, title } when the orthophoto is shown
})

const host = ref(null)
const status = ref('loading')                           // 'loading' | 'ready' | 'error'
const failure = ref('')
const mode = ref('follow')
const panel = ref(false)
const layers = reactive({ path: true, waypoints: true, photos: true, footprint: true })
const imagery = ref({ imagery: 'loading', tiles: 0, loaded: 0, failed: 0 })
const frame = ref(null)                                 // the photo's frame in the camera view
let scene = null
let stopFrames = null
let stopCoverage = null

const ground = computed(() => terrainLabel(props.simulation.terrain))
const flat = computed(() => !props.simulation.terrain)
const imageryNote = computed(() => {
  const s = imagery.value
  if (s.imagery === 'loading') return `Loading imagery… ${s.loaded}/${s.tiles}`
  if (s.imagery === 'unavailable') return 'Map imagery could not be loaded; the ground is shown plain.'
  return ''
})

function setMode(id) {
  mode.value = id
  scene?.setMode(id)
  frame.value = scene?.gimbalFrame() || null
}

onMounted(() => {
  try {
    scene = createSimScene(host.value, props.simulation, {
      coverageCanvas: props.coverageCanvas,
      basemap: props.basemap,
      ortho: props.ortho,
      mode: mode.value,
      layers: { ...layers, coverage: props.coverageMode },
      onStatus: s => { imagery.value = s },
    })
  } catch (e) {
    status.value = 'error'
    failure.value = e instanceof WebGLUnavailable
      ? 'This browser cannot show 3D graphics (WebGL is not available). The simulation continues on the map.'
      : 'The 3D view could not be started. The simulation continues on the map.'
    if (!(e instanceof WebGLUnavailable)) console.error(e)
    return
  }
  // For the browser checks: what the scene is showing right now.
  host.value.simSnapshot = () => scene.snapshot()
  stopFrames = props.onFrame(state => {
    scene.update(state)
    // The photo's frame depends on the pane's shape; keep it right when the pane is resized.
    const f = scene.gimbalFrame()
    if (f?.width !== frame.value?.width || f?.height !== frame.value?.height) frame.value = f
  })
  stopCoverage = props.onCoverage(() => scene.coverageChanged())
  scene.coverageChanged()
  status.value = 'ready'
})

onBeforeUnmount(() => {
  stopFrames?.()
  stopCoverage?.()
  scene?.dispose()
  scene = null
})

watch(layers, () => scene?.setLayers({ ...layers }))
watch(() => props.coverageMode, m => scene?.setLayers({ coverage: m }))
watch(() => [props.basemap?.id, props.ortho?.task], () => scene?.setImagery({ basemap: props.basemap, ortho: props.ortho }))
</script>

<template>
  <div class="relative h-full w-full overflow-hidden bg-[#cfe3f5]" :data-sim-3d="status" :data-sim-3d-mode="mode">
    <div ref="host" class="absolute inset-0" data-sim-3d-host></div>

    <div v-if="status === 'error'" class="absolute inset-0 flex items-center justify-center bg-card p-6 text-center text-sm text-muted-foreground" role="alert">
      {{ failure }}
    </div>

    <template v-else>
      <!-- The frame of one photo, when looking through the survey camera. -->
      <div v-if="frame" class="pointer-events-none absolute inset-0 flex items-center justify-center" aria-hidden="true">
        <div
          class="rounded-sm border-2 border-emerald-400/90 shadow-[0_0_0_9999px_rgba(0,0,0,0.28)]"
          :style="{ width: `${frame.width * 100}%`, height: `${frame.height * 100}%` }"
        ></div>
      </div>

      <div class="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-2 p-2">
        <div class="pointer-events-auto flex flex-wrap gap-1 rounded-md bg-card/90 p-1 shadow-sm" role="group" aria-label="Camera">
          <button
            v-for="m in CAMERA_MODES"
            :key="m.id"
            type="button"
            class="rounded px-2 py-1 text-xs transition-colors"
            :class="mode === m.id ? 'bg-primary text-primary-foreground' : 'text-foreground hover:bg-accent'"
            :title="m.hint"
            :aria-pressed="mode === m.id"
            :data-sim-camera="m.id"
            @click="setMode(m.id)"
          >{{ m.label }}</button>
        </div>
        <div class="pointer-events-auto flex flex-col items-end gap-1">
          <Button
            variant="outline" size="icon" class="h-8 w-8 bg-card"
            :title="panel ? 'Hide 3D layers' : 'Show 3D layers'" :aria-expanded="panel"
            data-sim-3d-layers
            @click="panel = !panel"
          >
            <X v-if="panel" />
            <Layers v-else />
            <span class="sr-only">3D layers</span>
          </Button>
          <div v-show="panel" class="w-[190px] rounded-lg border border-border bg-card p-3 text-sm shadow-md">
            <p class="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Show in 3D</p>
            <label class="flex cursor-pointer items-center gap-2 py-0.5 text-foreground"><input v-model="layers.path" type="checkbox" class="rounded" /> Flight path</label>
            <label class="flex cursor-pointer items-center gap-2 py-0.5 text-foreground"><input v-model="layers.waypoints" type="checkbox" class="rounded" /> Line ends</label>
            <label class="flex cursor-pointer items-center gap-2 py-0.5 text-foreground"><input v-model="layers.photos" type="checkbox" class="rounded" /> Photos taken</label>
            <label class="flex cursor-pointer items-center gap-2 py-0.5 text-foreground"><input v-model="layers.footprint" type="checkbox" class="rounded" /> Camera view</label>
            <p class="mt-2 text-[11px] leading-snug text-muted-foreground">{{ ground }}. Heights are drawn to scale.</p>
          </div>
        </div>
      </div>

      <div class="pointer-events-none absolute inset-x-0 bottom-0 flex flex-wrap items-end justify-between gap-2 p-2 text-[11px]">
        <div class="space-y-1">
          <p v-if="status === 'loading'" class="inline-flex items-center gap-1 rounded bg-card/90 px-2 py-1 text-muted-foreground">
            <LoaderCircle class="size-3 animate-spin" /> Starting 3D…
          </p>
          <p v-if="imageryNote" class="rounded bg-card/90 px-2 py-1 text-muted-foreground" data-sim-3d-imagery>{{ imageryNote }}</p>
          <p v-if="flat" class="rounded bg-card/90 px-2 py-1 text-muted-foreground" data-sim-3d-flat>
            No elevation data: the ground is drawn flat at the takeoff height.
          </p>
        </div>
        <p v-if="basemap?.attribution" class="rounded bg-card/80 px-1.5 py-0.5 text-[10px] text-muted-foreground" v-html="basemap.attribution"></p>
      </div>
    </template>
  </div>
</template>
