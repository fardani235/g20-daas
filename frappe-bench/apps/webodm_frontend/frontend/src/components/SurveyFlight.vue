<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { createSurveyFlight } from '@/composables/useSurveyFlight'

const emit = defineEmits(['phase'])

const wrapRef = ref(null)
const canvasRef = ref(null)

const hud = reactive({
  phase: 'STANDBY',
  alt: 0,
  spd: 0,
  gsd: 2.1,
  captured: 0,
  total: 0,
  progress: 0,
  area: 0,
  measuring: false,
})

const PHASE_LABEL = {
  STANDBY: 'Standby',
  SURVEY: 'Capture',
  STITCH: 'Reconstruct',
  MEASURE: 'Measure',
}

const PHASE_HEADER = {
  STANDBY: 'mission · survey grid',
  SURVEY: 'capturing imagery · WGS 84',
  STITCH: 'orthophoto · EPSG:32615',
  MEASURE: 'measurement · DSM backed',
}

let flight = null
let resizeObserver = null
let visibilityObserver = null
let motionQuery = null
let lastPhase = ''

function onHud(next) {
  hud.phase = next.phase
  hud.alt = next.alt
  hud.spd = next.spd
  hud.gsd = next.gsd
  hud.captured = next.captured
  hud.total = next.total
  hud.progress = next.progress
  hud.area = next.area
  hud.measuring = next.measuring

  if (next.phase !== lastPhase) {
    lastPhase = next.phase
    emit('phase', {
      phase: next.phase,
      header: PHASE_HEADER[next.phase] || PHASE_HEADER.STANDBY,
    })
  }
}

function teardown() {
  resizeObserver?.disconnect()
  visibilityObserver?.disconnect()
  resizeObserver = null
  visibilityObserver = null
  flight?.destroy()
  flight = null
}

function setup() {
  if (!canvasRef.value) return
  teardown()
  flight = createSurveyFlight(canvasRef.value, {
    onHud,
    reduced: motionQuery?.matches ?? false,
  })
  flight.init()

  resizeObserver = new ResizeObserver(() => flight?.resize())
  resizeObserver.observe(wrapRef.value)

  visibilityObserver = new IntersectionObserver(
    ([entry]) => (entry.isIntersecting ? flight?.start() : flight?.stop()),
    { threshold: 0.05 }
  )
  visibilityObserver.observe(wrapRef.value)
}

function onVisibilityChange() {
  if (document.hidden) flight?.stop()
  else flight?.start()
}

function onMotionChange() {
  setup()
}

onMounted(() => {
  motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
  motionQuery.addEventListener('change', onMotionChange)
  document.addEventListener('visibilitychange', onVisibilityChange)
  setup()
})

onBeforeUnmount(() => {
  motionQuery?.removeEventListener('change', onMotionChange)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  teardown()
})
</script>

<template>
  <div ref="wrapRef" class="relative size-full overflow-hidden bg-slate-950">
    <canvas
      ref="canvasRef"
      class="absolute inset-0 size-full"
      role="img"
      aria-label="Animated mission playback: a drone flies a survey grid over an elevation model, capturing
        georeferenced imagery that is stitched into an orthophoto, then a stockpile area is measured."
    />

    <!-- Telemetry HUD (decorative instrumentation) -->
    <div
      aria-hidden="true"
      class="pointer-events-none absolute inset-0 select-none font-mono text-[10px] uppercase tracking-[0.14em] text-slate-400"
    >
      <!-- Phase + mission meta -->
      <div class="absolute left-3 top-3 flex flex-col gap-1.5">
        <span
          class="flex w-fit items-center gap-1.5 rounded-full border border-slate-700/70 bg-slate-950/70 px-2.5 py-1 backdrop-blur transition-colors"
        >
          <span
            class="size-1.5 rounded-full transition-colors"
            :class="
              hud.phase === 'STANDBY'
                ? 'bg-slate-500'
                : hud.phase === 'MEASURE'
                  ? 'bg-emerald-400'
                  : 'animate-pulse bg-blue-400'
            "
          />
          <span class="text-slate-300">{{ PHASE_LABEL[hud.phase] }}</span>
        </span>
        <span class="hidden pl-1 text-slate-600 sm:block">
          grid 9 × 12 · {{ hud.total }} frames
        </span>
      </div>

      <!-- Measured area callout -->
      <Transition
        enter-active-class="transition duration-300 ease-out"
        enter-from-class="translate-y-2 opacity-0"
        leave-active-class="transition duration-200 ease-in"
        leave-to-class="translate-y-2 opacity-0"
      >
        <div
          v-if="hud.measuring"
          class="absolute bottom-16 left-1/2 -translate-x-1/2 rounded-lg border border-slate-700 bg-slate-950/85 px-4 py-2.5 backdrop-blur sm:bottom-14"
        >
          <p class="text-slate-500">measured area</p>
          <p class="mt-0.5 font-mono text-base tracking-normal text-blue-400">
            {{ hud.area.toFixed(2) }} ha
          </p>
          <p class="text-slate-500">dsm-backed volume ready</p>
        </div>
      </Transition>

      <!-- Bottom row: telemetry + stitching progress -->
      <div class="absolute inset-x-3 bottom-3 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div class="flex items-end gap-4">
          <div>
            <p class="text-slate-600">alt</p>
            <p class="mt-0.5 text-slate-300">{{ hud.alt.toFixed(0) }} m</p>
          </div>
          <div>
            <p class="text-slate-600">spd</p>
            <p class="mt-0.5 text-slate-300">{{ hud.spd.toFixed(1) }} m/s</p>
          </div>
          <div class="hidden sm:block">
            <p class="text-slate-600">gsd</p>
            <p class="mt-0.5 text-slate-300">{{ hud.gsd.toFixed(1) }} cm/px</p>
          </div>
          <div>
            <p class="text-slate-600">frames</p>
            <p class="mt-0.5 text-slate-300">{{ hud.captured }}</p>
          </div>
        </div>

        <div class="w-full sm:w-32">
          <div class="flex items-center justify-between text-slate-600">
            <span>coverage</span>
            <span>{{ (hud.progress * 100).toFixed(0) }}%</span>
          </div>
          <div class="mt-1 h-1 w-full overflow-hidden rounded-full bg-slate-800">
            <div
              class="h-full rounded-full bg-blue-500 transition-[width] duration-200 ease-out"
              :style="{ width: `${hud.progress * 100}%` }"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
