<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Pause, Play, RotateCcw, Square } from 'lucide-vue-next'
import { Button, Select } from '@/components/ui'
import { SPEEDS } from '@/lib/flightsim'
import { PHASE_COLORS, TIMELINE_LEGEND, flightMarks, formatClock, timelineColumns } from '@/lib/flightsim/describe'
import { PHASE_INFO } from '@/lib/flightsim/trajectory'

// Transport controls and the timeline. The component holds no time of its
// own: it shows `playback` and emits what the user asked for.
const props = defineProps({
  playback: { type: Object, required: true },       // { time, duration, playing, speed, ended }
  trajectory: { type: Object, default: null },
})
const emit = defineEmits(['play', 'pause', 'stop', 'restart', 'seek', 'speed', 'flight'])

const bar = ref(null)
const marks = computed(() => (props.trajectory && props.trajectory.flights.length > 1 ? flightMarks(props.trajectory) : []))
const percent = computed(() => (props.playback.duration > 0 ? (props.playback.time / props.playback.duration) * 100 : 0))
let observer = null

// The phases of a long mission are far more than the bar has pixels: paint one colour per column.
function paint() {
  const el = bar.value
  if (!el || !props.trajectory) return
  const width = Math.max(1, Math.round(el.clientWidth || 600))
  el.width = width
  el.height = 1
  const ctx = el.getContext && el.getContext('2d')
  if (!ctx) return
  const columns = timelineColumns(props.trajectory, width)
  for (let i = 0; i < width; i++) {
    ctx.fillStyle = PHASE_COLORS[PHASE_INFO[columns[i]].key]
    ctx.fillRect(i, 0, 1, 1)
  }
}

onMounted(() => {
  paint()
  if (typeof ResizeObserver !== 'undefined' && bar.value) {
    observer = new ResizeObserver(paint)
    observer.observe(bar.value)
  }
})
onBeforeUnmount(() => observer?.disconnect())
watch(() => props.trajectory, paint, { flush: 'post' })

function onKey(e) {
  if (e.key === ' ' || e.key === 'k') {
    e.preventDefault()
    emit(props.playback.playing ? 'pause' : 'play')
  } else if (e.key === 'Home') {
    e.preventDefault()
    emit('seek', 0)
  } else if (e.key === 'End') {
    e.preventDefault()
    emit('seek', props.playback.duration)
  }
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-x-3 gap-y-2" data-sim-controls @keydown="onKey">
    <div class="flex items-center gap-1" role="group" aria-label="Playback">
      <Button size="icon" variant="outline" class="h-8 w-8" title="Restart from the beginning" data-sim-restart @click="emit('restart')">
        <RotateCcw />
        <span class="sr-only">Restart</span>
      </Button>
      <Button
        size="icon"
        class="h-8 w-8"
        :variant="playback.playing ? 'outline' : 'default'"
        :title="playback.playing ? 'Pause (space)' : 'Play (space)'"
        :aria-pressed="playback.playing"
        data-sim-play
        @click="emit(playback.playing ? 'pause' : 'play')"
      >
        <Pause v-if="playback.playing" />
        <Play v-else />
        <span class="sr-only">{{ playback.playing ? 'Pause' : 'Play' }}</span>
      </Button>
      <Button size="icon" variant="outline" class="h-8 w-8" title="Stop and return to the start" data-sim-stop @click="emit('stop')">
        <Square />
        <span class="sr-only">Stop</span>
      </Button>
    </div>

    <span class="whitespace-nowrap text-xs tabular-nums text-foreground" data-sim-clock>
      {{ formatClock(playback.time) }} <span class="text-muted-foreground">/ {{ formatClock(playback.duration) }}</span>
    </span>

    <div class="relative h-8 min-w-[180px] flex-1" data-sim-timeline>
      <canvas
        ref="bar"
        class="absolute inset-x-0 top-1/2 h-2 w-full -translate-y-1/2 rounded-sm"
        style="image-rendering: pixelated"
        aria-hidden="true"
      ></canvas>
      <!-- What has been played, dimmed over the phase colours. -->
      <div class="pointer-events-none absolute inset-x-0 top-1/2 h-2 -translate-y-1/2 overflow-hidden rounded-sm">
        <div class="h-full w-full bg-background/60" :style="{ transform: `translateX(${percent}%)` }"></div>
      </div>
      <button
        v-for="m in marks"
        :key="m.index"
        type="button"
        class="absolute top-0 z-10 -translate-x-1/2 rounded px-1 text-[10px] leading-3 text-muted-foreground hover:bg-accent hover:text-foreground"
        :style="{ left: `${m.start * 100}%` }"
        :title="`Jump to the start of flight ${m.index + 1}`"
        :data-sim-flight="m.index"
        @click="emit('flight', m.index)"
      >{{ m.index + 1 }}</button>
      <input
        type="range"
        class="flightsim-scrub absolute inset-0 h-full w-full cursor-pointer"
        min="0"
        :max="playback.duration"
        step="0.1"
        :value="playback.time"
        aria-label="Simulation time"
        :aria-valuetext="`${formatClock(playback.time)} of ${formatClock(playback.duration)}`"
        data-sim-scrub
        @input="emit('seek', Number($event.target.value))"
      />
    </div>

    <label class="flex items-center gap-1.5 text-xs text-muted-foreground">
      <span>Speed</span>
      <Select
        class="h-8 w-[76px] px-2 text-xs"
        :model-value="playback.speed"
        aria-label="Playback speed"
        data-sim-speed
        @update:model-value="emit('speed', Number($event))"
      >
        <option v-for="s in SPEEDS" :key="s" :value="s">{{ s }}×</option>
      </Select>
    </label>

    <ul class="hidden w-full flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground xl:flex" aria-label="Timeline colours">
      <li v-for="l in TIMELINE_LEGEND" :key="l.key" class="flex items-center gap-1">
        <span class="inline-block h-1.5 w-3 rounded-sm" :style="{ background: PHASE_COLORS[l.key] }"></span>{{ l.label }}
      </li>
    </ul>
  </div>
</template>

<style scoped>
/* The range input is the whole scrubber: a transparent track over the phase bar, a visible thumb. */
.flightsim-scrub {
  -webkit-appearance: none;
  appearance: none;
  background: transparent;
  margin: 0;
}
.flightsim-scrub::-webkit-slider-runnable-track { background: transparent; height: 100%; }
.flightsim-scrub::-moz-range-track { background: transparent; height: 100%; }
.flightsim-scrub::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 4px;
  height: 22px;
  margin-top: 5px;
  border-radius: 2px;
  background: hsl(var(--foreground));
  box-shadow: 0 0 0 2px hsl(var(--background));
}
.flightsim-scrub::-moz-range-thumb {
  width: 4px;
  height: 22px;
  border: 0;
  border-radius: 2px;
  background: hsl(var(--foreground));
  box-shadow: 0 0 0 2px hsl(var(--background));
}
.flightsim-scrub:focus-visible { outline: 2px solid hsl(var(--ring)); outline-offset: 2px; }
</style>
