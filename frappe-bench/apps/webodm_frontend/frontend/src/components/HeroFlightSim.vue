<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Camera,
  CheckCircle2,
  Crosshair,
  Flame,
  Layers,
  Pause,
  Play,
  RotateCcw,
  ShieldAlert,
} from 'lucide-vue-next'
import { createFlightSim, LAYERS } from '@/composables/useFlightSim'

const wrapRef = ref(null)
const canvasRef = ref(null)

const telemetry = reactive({
  altitude: 118,
  speed: 8.4,
  battery: 92,
  satellites: 19,
  latitude: -6.2,
  longitude: 106.8,
  heading: 0,
  gsd: 2.1,
  status: 'SURVEYING',
})

const layer = ref('optical')
const playing = ref(true)
const notice = ref('Click the map to inspect an area or reroute the drone')
const selected = ref(null)
const reduced = ref(false)

const LAYER_ICONS = {
  optical: Camera,
  thermal: Flame,
  pointcloud: Layers,
}

let sim = null
let resizeObserver = null
let intersectionObserver = null
let motionQuery = null
let intersecting = true
let documentVisible = true

function onTelemetry(next) {
  Object.assign(telemetry, next)
}

function syncState() {
  if (!sim) return
  const state = sim.getState()
  layer.value = state.layer
  playing.value = state.playing
  notice.value = state.notice
  selected.value = state.selected
  Object.assign(telemetry, state.telemetry)
}

function updateRunState() {
  if (!sim) return
  if (intersecting && documentVisible) sim.start()
  else sim.stop()
}

function teardown() {
  resizeObserver?.disconnect()
  intersectionObserver?.disconnect()
  resizeObserver = null
  intersectionObserver = null
  sim?.destroy()
  sim = null
}

function setup() {
  if (!canvasRef.value) return
  teardown()
  sim = createFlightSim(canvasRef.value, { reduced: reduced.value, onTelemetry })
  sim.init()
  syncState()

  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => {
      sim?.resize()
      syncState()
    })
    resizeObserver.observe(wrapRef.value)
  }

  if (typeof IntersectionObserver !== 'undefined') {
    intersectionObserver = new IntersectionObserver(
      ([entry]) => {
        intersecting = entry.isIntersecting
        updateRunState()
      },
      { threshold: 0.05 }
    )
    intersectionObserver.observe(wrapRef.value)
  }

  updateRunState()
}

function onVisibilityChange() {
  documentVisible = !document.hidden
  updateRunState()
}

function onMotionChange() {
  reduced.value = motionQuery?.matches ?? false
  setup()
}

function setLayer(id) {
  sim?.setLayer(id)
  syncState()
}

function togglePlay() {
  sim?.togglePlay()
  syncState()
}

function resetFlight() {
  sim?.reset()
  syncState()
}

function onCanvasClick(event) {
  if (!sim || !canvasRef.value) return
  const rect = canvasRef.value.getBoundingClientRect()
  sim.selectAt(event.clientX - rect.left, event.clientY - rect.top)
  syncState()
}

onMounted(() => {
  motionQuery =
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)')
      : null
  reduced.value = motionQuery?.matches ?? false
  motionQuery?.addEventListener?.('change', onMotionChange)
  document.addEventListener('visibilitychange', onVisibilityChange)
  setup()
})

onBeforeUnmount(() => {
  motionQuery?.removeEventListener?.('change', onMotionChange)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  teardown()
})

defineExpose({ layer, playing, telemetry, notice, selected })
</script>

<template>
  <div
    ref="wrapRef"
    class="relative w-full overflow-hidden rounded-2xl border border-slate-800/80 bg-slate-950/90 shadow-2xl backdrop-blur-xl"
  >
    <!-- Top telemetry bar -->
    <div
      class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800/80 bg-slate-900/60 px-4 py-3 text-xs"
    >
      <div class="flex items-center gap-2.5">
        <span class="relative flex h-2.5 w-2.5">
          <span
            class="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75"
          />
          <span class="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" />
        </span>
        <span class="font-mono font-semibold uppercase tracking-wider text-slate-200">
          Flight sim // Survey patrol
        </span>
        <span
          class="hidden rounded border border-cyan-800/60 bg-cyan-950/80 px-2 py-0.5 font-mono text-cyan-300 sm:inline-block"
        >
          G20 Mapping v4.2
        </span>
      </div>

      <div class="flex items-center gap-4 font-mono text-slate-300">
        <span class="flex items-center gap-1.5">
          <span class="text-slate-500">ALT:</span>
          <span class="font-semibold text-cyan-400">{{ telemetry.altitude.toFixed(0) }}m</span>
        </span>
        <span class="flex items-center gap-1.5">
          <span class="text-slate-500">SPD:</span>
          <span class="font-semibold text-cyan-400">{{ telemetry.speed.toFixed(1) }} m/s</span>
        </span>
        <span class="hidden items-center gap-1.5 md:flex">
          <span class="text-slate-500">HDG:</span>
          <span class="text-slate-200">{{ telemetry.heading }}°</span>
        </span>
        <span class="hidden items-center gap-1.5 lg:flex">
          <span class="text-slate-500">SAT:</span>
          <span class="text-emerald-400">{{ telemetry.satellites }} lock</span>
        </span>
        <span class="flex items-center gap-1.5">
          <span class="text-slate-500">BAT:</span>
          <span class="font-semibold text-emerald-400">{{ telemetry.battery.toFixed(0) }}%</span>
        </span>
      </div>
    </div>

    <!-- Interactive canvas -->
    <div
      class="relative min-h-[320px] max-h-[560px] w-full select-none bg-slate-950 aspect-[16/10] cursor-crosshair sm:aspect-[16/9]"
    >
      <canvas
        ref="canvasRef"
        class="block h-full w-full"
        role="img"
        aria-label="Animated drone flight simulation over a survey block: the drone follows waypoints
          while the operator inspects mapped areas of interest such as stockpile volume, building
          footprints, vegetation encroachment and water bodies."
        @click="onCanvasClick"
      />

      <!-- Decorative sensor scan sweep -->
      <div class="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
        <div class="absolute inset-0 animate-scan-line">
          <div class="h-16 w-full bg-gradient-to-b from-cyan-400/10 to-transparent" />
        </div>
      </div>

      <!-- Flight controls -->
      <div
        class="absolute bottom-3 left-3 flex flex-wrap items-center gap-2 rounded-xl border border-slate-700/60 bg-slate-900/85 p-2 backdrop-blur-md"
      >
        <button
          type="button"
          class="flex items-center gap-1.5 rounded-lg p-2 font-mono text-xs font-semibold transition-all"
          :class="
            playing
              ? 'border border-amber-500/30 bg-amber-500/20 text-amber-300 hover:bg-amber-500/30'
              : 'border border-emerald-500/30 bg-emerald-500/20 text-emerald-300 hover:bg-emerald-500/30'
          "
          :aria-pressed="!playing"
          :aria-label="playing ? 'Pause flight simulation' : 'Resume flight simulation'"
          @click="togglePlay"
        >
          <Pause v-if="playing" class="h-3.5 w-3.5" />
          <Play v-else class="h-3.5 w-3.5" />
          <span>{{ playing ? 'PAUSE' : 'RESUME' }}</span>
        </button>

        <button
          type="button"
          class="flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-800/80 p-2 font-mono text-xs text-slate-300 hover:bg-slate-700"
          aria-label="Reset flight plan"
          @click="resetFlight"
        >
          <RotateCcw class="h-3.5 w-3.5" />
          <span class="hidden sm:inline">RESET</span>
        </button>

        <div class="mx-1 hidden h-4 w-px bg-slate-700 sm:block" />

        <div
          class="flex items-center gap-1 rounded-lg border border-slate-800 bg-slate-950/80 p-0.5"
          role="group"
          aria-label="Map layer"
        >
          <button
            v-for="option in LAYERS"
            :key="option.id"
            type="button"
            class="flex items-center gap-1 rounded px-2.5 py-1 text-xs font-medium transition-colors"
            :class="
              layer === option.id
                ? 'bg-cyan-500 font-bold text-slate-950 shadow-sm'
                : 'text-slate-400 hover:text-slate-200'
            "
            :aria-pressed="layer === option.id"
            @click="setLayer(option.id)"
          >
            <component :is="LAYER_ICONS[option.id]" class="h-3 w-3" />
            <span>{{ option.label }}</span>
          </button>
        </div>
      </div>

      <!-- Interaction notice -->
      <div
        class="pointer-events-none absolute left-1/2 top-3 flex max-w-[90%] -translate-x-1/2 items-center gap-2 rounded-full border border-slate-800/90 bg-slate-950/80 px-3 py-1.5 font-mono text-[11px] text-cyan-300 shadow-lg backdrop-blur-md"
      >
        <Crosshair class="h-3 w-3 shrink-0 text-cyan-400 animate-pulse" />
        <span class="truncate">{{ notice }}</span>
      </div>

      <!-- Selected area detail panel -->
      <div
        v-if="selected"
        class="absolute right-3 top-3 max-w-[260px] rounded-xl border border-cyan-500/40 bg-slate-900/95 p-3.5 shadow-2xl backdrop-blur-md sm:max-w-xs"
      >
        <div class="mb-2.5 flex items-start justify-between gap-2 border-b border-slate-800 pb-2">
          <div class="flex items-center gap-2">
            <span class="rounded bg-cyan-900/40 p-1 text-cyan-400">
              <ShieldAlert class="h-3.5 w-3.5" />
            </span>
            <div>
              <h4 class="text-xs font-semibold leading-tight text-slate-100">
                {{ selected.label }}
              </h4>
              <span class="font-mono text-[10px] text-cyan-400">
                Confidence: {{ selected.confidence }}%
              </span>
            </div>
          </div>
          <span
            class="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase text-slate-300"
          >
            {{ selected.metric }}
          </span>
        </div>

        <p class="mb-3 text-xs leading-relaxed text-slate-300">{{ selected.details }}</p>

        <div
          class="flex items-center justify-between border-t border-slate-800/60 pt-2 font-mono text-[10px] text-slate-400"
        >
          <span class="flex items-center gap-1 text-emerald-400">
            <CheckCircle2 class="h-3 w-3" /> Geo-tagged output
          </span>
          <span>DSM backed</span>
        </div>
      </div>
    </div>

    <!-- Bottom status ticker -->
    <div
      class="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800/80 bg-slate-950/90 px-4 py-2 font-mono text-xs text-slate-400"
    >
      <div class="flex items-center gap-3">
        <span class="text-slate-500">POSITION:</span>
        <span class="text-slate-300">
          {{ telemetry.latitude.toFixed(4) }}°, {{ telemetry.longitude.toFixed(4) }}°
        </span>
        <span class="hidden text-slate-600 sm:inline">|</span>
        <span class="hidden text-slate-500 sm:inline">GSD:</span>
        <span class="hidden text-cyan-300 sm:inline">{{ telemetry.gsd.toFixed(1) }} cm/px</span>
      </div>
      <div class="flex items-center gap-2 text-cyan-400">
        <span class="h-2 w-2 rounded-full" :class="playing ? 'bg-emerald-400' : 'bg-amber-400'" />
        <span>{{ telemetry.status }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
@media (prefers-reduced-motion: reduce) {
  .animate-ping,
  .animate-pulse {
    animation: none !important;
  }
}
</style>
