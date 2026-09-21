<script setup>
// Floating toolbar for the 3D viewer. Purely presentational: it reflects the
// viewer state passed in and emits intents; pages/ModelView.vue wires them to
// the useModelViewer composable. Icon-only with tooltips so it stays compact
// on phones; the Help dialog lists every control in full.
import { ChevronDown, Compass, Grid3x3, CircleHelp, Hand, Home, Maximize2, Minimize2, Rotate3d, ZoomIn, ZoomOut } from 'lucide-vue-next'
import { Button, DropdownMenu, DropdownMenuItem, DropdownMenuLabel } from '@/components/ui'
import { MODES, MODE_LABELS, VIEW_PRESETS, VIEW_LABELS } from '@/lib/modelViewer'

defineProps({
  mode: { type: String, default: 'rotate' },
  gridVisible: { type: Boolean, default: true },
  fullscreen: { type: Boolean, default: false },
})

const emit = defineEmits(['update:mode', 'zoom-in', 'zoom-out', 'reset', 'view', 'toggle-grid', 'toggle-fullscreen', 'help'])

const MODE_ICONS = { rotate: Rotate3d, pan: Hand, zoom: ZoomIn }
const MODE_KEYS = { rotate: 'left-drag', pan: 'left-drag (or right-drag in Rotate)', zoom: 'left-drag up/down (or scroll)' }
const VIEW_KEYS = { iso: '1', top: '2', north: '3', east: '4' }
</script>

<template>
  <div class="pointer-events-none absolute inset-x-3 top-3 z-10 flex flex-wrap items-start gap-2">
    <div
      class="pointer-events-auto flex items-center gap-0.5 rounded-lg border border-border bg-card/90 p-1 shadow-sm backdrop-blur"
      role="radiogroup"
      aria-label="Mouse mode"
    >
      <Button
        v-for="m in MODES"
        :key="m"
        size="icon"
        class="h-8 w-8"
        :variant="mode === m ? 'default' : 'ghost'"
        :title="`${MODE_LABELS[m]} — ${MODE_KEYS[m]}`"
        :aria-label="MODE_LABELS[m]"
        :aria-checked="mode === m"
        role="radio"
        @click="emit('update:mode', m)"
      >
        <component :is="MODE_ICONS[m]" />
      </Button>
    </div>

    <div class="pointer-events-auto flex items-center gap-0.5 rounded-lg border border-border bg-card/90 p-1 shadow-sm backdrop-blur">
      <Button size="icon" variant="ghost" class="h-8 w-8" title="Zoom in (+)" aria-label="Zoom in" @click="emit('zoom-in')">
        <ZoomIn />
      </Button>
      <Button size="icon" variant="ghost" class="h-8 w-8" title="Zoom out (−)" aria-label="Zoom out" @click="emit('zoom-out')">
        <ZoomOut />
      </Button>
      <Button size="icon" variant="ghost" class="h-8 w-8" title="Reset view (R)" aria-label="Reset view" @click="emit('reset')">
        <Home />
      </Button>
      <DropdownMenu align="start">
        <template #trigger>
          <Button size="sm" variant="ghost" class="h-8 gap-1 px-2" title="Preset views" aria-label="Preset views">
            <Compass />
            <ChevronDown class="!size-3" />
          </Button>
        </template>
        <DropdownMenuLabel>View from</DropdownMenuLabel>
        <DropdownMenuItem v-for="p in VIEW_PRESETS" :key="p" @select="emit('view', p)">
          <span class="flex-1">{{ VIEW_LABELS[p] }}</span>
          <kbd class="rounded border border-border px-1 font-mono text-[10px] text-muted-foreground">{{ VIEW_KEYS[p] }}</kbd>
        </DropdownMenuItem>
      </DropdownMenu>
    </div>

    <div class="pointer-events-auto flex items-center gap-0.5 rounded-lg border border-border bg-card/90 p-1 shadow-sm backdrop-blur">
      <Button
        size="icon"
        :variant="gridVisible ? 'secondary' : 'ghost'"
        class="h-8 w-8"
        :title="gridVisible ? 'Hide ground grid (G)' : 'Show ground grid (G)'"
        aria-label="Toggle ground grid"
        :aria-pressed="gridVisible"
        @click="emit('toggle-grid')"
      >
        <Grid3x3 />
      </Button>
      <Button
        size="icon"
        variant="ghost"
        class="h-8 w-8"
        :title="fullscreen ? 'Exit fullscreen (F)' : 'Fullscreen (F)'"
        aria-label="Toggle fullscreen"
        :aria-pressed="fullscreen"
        @click="emit('toggle-fullscreen')"
      >
        <Minimize2 v-if="fullscreen" />
        <Maximize2 v-else />
      </Button>
      <Button size="icon" variant="ghost" class="h-8 w-8" title="Controls help (?)" aria-label="Controls help" @click="emit('help')">
        <CircleHelp />
      </Button>
    </div>
  </div>
</template>
