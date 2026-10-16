<template>
  <div class="flex h-full flex-col md:flex-row" data-flight-planner>
    <!-- Sidebar: the plan, top to bottom in the order it is made -->
    <aside
      class="max-h-[48vh] w-full flex-shrink-0 flex-col border-b border-border bg-card md:max-h-none md:w-[400px] md:border-b-0 md:border-r"
      :class="simulating ? 'hidden md:flex' : 'flex'"
      aria-label="Flight plan"
    >
      <header class="space-y-2 border-b border-border p-3">
        <div class="flex items-center gap-2">
          <router-link
            :to="`/project/${encodeURIComponent(projectId)}/plans`"
            class="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
            title="Back to this project's flight plans"
          >
            <ArrowLeft class="size-4" />
            <span class="sr-only">Back to flight plans</span>
          </router-link>
          <Input
            v-model="state.title"
            class="h-8 flex-1 font-medium"
            placeholder="Flight plan title"
            aria-label="Flight plan title"
            data-plan-title
          />
          <Button size="sm" :loading="saving" :disabled="!canSave" :title="saveHint" data-plan-save @click="save">
            <Save />
            Save
          </Button>
        </div>
        <div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span class="truncate">{{ project?.title || projectId }}</span>
          <Badge v-if="savedPlan && !dirty" variant="success">Saved</Badge>
          <Badge v-else-if="savedPlan" variant="warning">Unsaved changes</Badge>
          <Badge v-else variant="outline">Not saved yet</Badge>
          <Badge :variant="status.variant" data-plan-status>{{ status.label }}</Badge>
        </div>
      </header>

      <div class="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        <!-- Estimates -->
        <section aria-labelledby="fp-estimates" data-plan-estimates>
          <div class="mb-1.5 flex items-center justify-between">
            <h2 id="fp-estimates" class="text-xs font-medium uppercase tracking-wide text-muted-foreground">Estimates</h2>
            <span class="flex items-center gap-1 text-[11px] text-muted-foreground">
              <LoaderCircle v-if="computing || terrainLoading" class="size-3 animate-spin" />
              <span v-if="terrainLoading">Loading terrain…</span>
              <span v-else-if="computing">Computing…</span>
              <span v-else title="Computed from nominal equipment data; not verified flight-safety information">Planning estimates</span>
            </span>
          </div>
          <dl v-if="mission" class="grid grid-cols-3 gap-1.5">
            <div v-for="row in estimates" :key="row.key" class="rounded-md border border-border bg-background px-2 py-1.5" :data-estimate="row.key">
              <dt class="text-[10px] uppercase tracking-wide text-muted-foreground">{{ row.label }}</dt>
              <dd class="text-sm font-semibold text-foreground">{{ row.value }}</dd>
            </div>
          </dl>
          <Alert v-else-if="previewError" variant="destructive" data-plan-error>{{ previewError.message }}</Alert>
          <p v-else class="rounded-md border border-dashed border-border px-3 py-3 text-xs text-muted-foreground" data-plan-hint>
            {{ startHint }}
          </p>
          <details v-if="mission" class="mt-1.5 text-xs">
            <summary class="cursor-pointer text-muted-foreground hover:text-foreground">Details</summary>
            <dl class="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
              <template v-for="row in details" :key="row.label">
                <dt class="text-muted-foreground">{{ row.label }}</dt>
                <dd class="text-foreground">{{ row.value }}</dd>
              </template>
            </dl>
            <table v-if="mission.flights.length > 1" class="mt-2 w-full text-left" data-plan-flights>
              <caption class="mb-0.5 text-left text-muted-foreground">Flights (one battery each, split at line ends)</caption>
              <thead>
                <tr class="text-muted-foreground"><th class="font-normal">Flight</th><th class="font-normal">Time</th><th class="font-normal">Distance</th><th class="font-normal">Photos</th></tr>
              </thead>
              <tbody>
                <tr v-for="f in mission.flights" :key="f.index" class="text-foreground">
                  <td>{{ f.index + 1 }}</td>
                  <td>{{ formatDuration(f.duration_s) }}</td>
                  <td>{{ formatLength(f.distance_m) }}</td>
                  <td>{{ formatCount(f.photos) }}</td>
                </tr>
              </tbody>
            </table>
          </details>
        </section>

        <!-- 1. What to survey -->
        <section aria-labelledby="fp-shape" class="space-y-2">
          <h2 id="fp-shape" class="text-xs font-medium uppercase tracking-wide text-muted-foreground">1 · Survey</h2>
          <div class="grid grid-cols-2 gap-1 rounded-md bg-muted p-1 text-sm" role="tablist" aria-label="Mission type" data-plan-type>
            <button
              v-for="t in TYPES"
              :key="t.id"
              type="button"
              role="tab"
              :aria-selected="state.type === t.id"
              class="flex items-center justify-center gap-1.5 rounded px-3 py-1.5 transition-colors"
              :class="state.type === t.id ? 'bg-card text-card-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'"
              :data-type="t.id"
              @click="setType(t.id)"
            >
              <component :is="t.icon" class="size-3.5" />
              {{ t.label }}
            </button>
          </div>
          <p class="text-xs text-muted-foreground">{{ TYPES.find(t => t.id === state.type).help }}</p>
          <NumberField
            v-if="state.type === 'corridor'"
            id="fp-width"
            v-model="state.corridorWidth"
            label="Corridor width"
            unit="m"
            :min="1"
            :max="5000"
            :invalid="previewError?.field === 'corridor_width_m'"
            help="Total width to cover, centred on the line."
          />
          <div class="flex flex-wrap gap-1.5">
            <Button v-if="!editor.state.drawing" size="sm" :variant="state.vertices.length ? 'outline' : 'default'" data-plan-draw @click="startDrawing">
              <PenLine />
              {{ state.vertices.length ? 'Redraw' : (state.type === 'area' ? 'Draw area' : 'Draw line') }}
            </Button>
            <template v-else>
              <Button size="sm" variant="success" :disabled="state.vertices.length < MIN_VERTICES[state.type]" data-plan-finish @click="editor.finish()">
                <Check />
                Finish
              </Button>
              <Button size="sm" variant="ghost" @click="editor.cancel()">Cancel</Button>
            </template>
            <Button
              size="sm"
              :variant="placingTakeoff ? 'default' : 'outline'"
              :title="state.takeoff ? 'Click the map to move the takeoff point' : 'Click the map where you will launch'"
              data-plan-takeoff
              @click="placingTakeoff = !placingTakeoff"
            >
              <Navigation />
              {{ state.takeoff ? 'Move takeoff' : 'Set takeoff point' }}
            </Button>
            <Button v-if="state.takeoff" size="sm" variant="ghost" title="Remove the takeoff point" @click="state.takeoff = null">
              <X />
              <span class="sr-only">Remove the takeoff point</span>
            </Button>
          </div>
          <p class="text-xs text-muted-foreground" role="status" data-plan-draw-hint>{{ drawHint }}</p>
        </section>

        <!-- 2. Equipment -->
        <section aria-labelledby="fp-equipment" class="space-y-2">
          <div class="flex items-center justify-between">
            <h2 id="fp-equipment" class="text-xs font-medium uppercase tracking-wide text-muted-foreground">2 · Aircraft and camera</h2>
            <router-link to="/flight-plans/equipment" class="text-xs text-primary hover:underline">Manage</router-link>
          </div>
          <div class="space-y-1">
            <Label for="fp-drone" class="text-xs text-muted-foreground">Aircraft</Label>
            <Select id="fp-drone" :model-value="state.drone" data-plan-drone @update:model-value="setDrone">
              <optgroup v-for="g in droneGroups" :key="g.label" :label="g.label">
                <option v-for="d in g.items" :key="d.id" :value="d.id">{{ d.label }}</option>
              </optgroup>
            </Select>
          </div>
          <div class="space-y-1">
            <Label for="fp-camera" class="text-xs text-muted-foreground">Camera</Label>
            <Select id="fp-camera" :model-value="state.camera" :disabled="cameraChoices.length < 2" data-plan-camera @update:model-value="setCamera">
              <option v-for="c in cameraChoices" :key="c.id" :value="c.id">{{ c.label }}</option>
            </Select>
          </div>
          <p v-if="drone" class="text-xs text-muted-foreground" data-plan-support>{{ missionSupportSummary(drone) }}</p>
          <details v-if="drone" class="text-xs">
            <summary class="cursor-pointer text-muted-foreground hover:text-foreground">Specifications used</summary>
            <dl class="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
              <template v-for="row in droneSpecRows(drone)" :key="row.label">
                <dt class="text-muted-foreground">{{ row.label }}</dt>
                <dd class="text-foreground">{{ row.value }}</dd>
              </template>
              <template v-for="row in cameraSpecRows(camera)" :key="row.label">
                <dt class="text-muted-foreground">{{ row.label }}</dt>
                <dd class="text-foreground">{{ row.value }}</dd>
              </template>
            </dl>
            <p v-if="drone.notes" class="mt-1 text-muted-foreground">{{ drone.notes }}</p>
            <p class="mt-1 text-muted-foreground">
              {{ drone.source === 'custom' ? 'From your organization\'s profile.' : `Published nominal figures (catalog ${catalog?.catalog_as_of || ''}); check them against the aircraft manual.` }}
            </p>
          </details>
        </section>

        <!-- 3. Flight -->
        <section aria-labelledby="fp-flight" class="space-y-2">
          <h2 id="fp-flight" class="text-xs font-medium uppercase tracking-wide text-muted-foreground">3 · Flight</h2>
          <div class="grid grid-cols-2 gap-2">
            <NumberField
              id="fp-altitude"
              :model-value="state.params.altitude_m"
              :label="state.params.altitude_mode === 'terrain' ? 'Height above ground' : 'Height above takeoff'"
              unit="m"
              :min="5"
              :max="2000"
              :invalid="previewError?.field === 'altitude_m'"
              :help="spec('altitude_m').help"
              data-plan-altitude
              @update:model-value="setAltitude"
            />
            <NumberField
              id="fp-gsd"
              :model-value="state.params.gsd_cm"
              label="Ground resolution"
              unit="cm/px"
              :min="0.1"
              :max="200"
              :invalid="previewError?.field === 'gsd_cm'"
              help="Size of one image pixel on the ground. Linked to the height: change either."
              data-plan-gsd
              @update:model-value="setGsd"
            />
            <NumberField id="fp-front" v-model="state.params.front_overlap" label="Front overlap" unit="%" :min="10" :max="95" :invalid="previewError?.field === 'front_overlap'" :help="spec('front_overlap').help" />
            <NumberField id="fp-side" v-model="state.params.side_overlap" label="Side overlap" unit="%" :min="10" :max="95" :invalid="previewError?.field === 'side_overlap'" :help="spec('side_overlap').help" />
            <NumberField
              id="fp-speed"
              v-model="state.params.speed_ms"
              label="Speed"
              unit="m/s"
              :min="0.5"
              :max="60"
              :placeholder="drone?.cruise_speed_ms ? String(drone.cruise_speed_ms) : '8'"
              :invalid="previewError?.field === 'speed_ms'"
              help="Speed along the flight lines. Empty uses the aircraft's suggested mapping speed."
              data-plan-speed
            />
            <div v-if="state.type === 'area'" class="space-y-1">
              <Label for="fp-direction" class="text-xs text-muted-foreground" :title="spec('direction').help">Line direction</Label>
              <div class="flex items-center gap-1.5">
                <Input
                  id="fp-direction"
                  type="number"
                  class="h-9"
                  min="0"
                  max="359"
                  step="1"
                  :disabled="directionAuto"
                  :model-value="directionAuto ? (mission?.layout.direction_deg ?? '') : state.params.direction"
                  @update:model-value="v => (state.params.direction = v === '' ? 0 : Number(v))"
                />
                <label class="flex cursor-pointer items-center gap-1 whitespace-nowrap text-xs text-foreground" title="Pick the direction that needs the fewest lines">
                  <input type="checkbox" class="rounded" :checked="directionAuto" @change="toggleDirectionAuto" />
                  Auto
                </label>
              </div>
            </div>
            <NumberField v-else id="fp-passes" v-model="state.params.corridor_min_passes" label="Minimum passes" :min="1" :max="9" step="1" :help="spec('corridor_min_passes').help" />
          </div>
          <label v-if="state.type === 'area'" class="flex cursor-pointer items-center gap-2 text-xs text-foreground" :title="spec('crosshatch').help">
            <input v-model="state.params.crosshatch" type="checkbox" class="rounded" />
            Double grid (second pass at right angles)
          </label>

          <details class="text-xs" data-plan-advanced>
            <summary class="cursor-pointer text-muted-foreground hover:text-foreground">Camera and advanced settings</summary>
            <div class="mt-2 grid grid-cols-2 gap-2">
              <div class="space-y-1">
                <Label for="fp-orientation" class="text-xs text-muted-foreground" :title="spec('camera_orientation').help">Camera orientation</Label>
                <Select id="fp-orientation" v-model="state.params.camera_orientation">
                  <option value="landscape">Landscape (long side across)</option>
                  <option value="portrait">Portrait (short side across)</option>
                </Select>
              </div>
              <NumberField id="fp-pitch" v-model="state.params.gimbal_pitch_deg" label="Gimbal pitch" unit="°" :min="-90" :max="0" :help="spec('gimbal_pitch_deg').help" />
              <NumberField id="fp-shutter" v-model="state.params.shutter_speed" label="Shutter speed 1/" unit="s" :min="30" :max="16000" :help="spec('shutter_speed').help" />
              <NumberField id="fp-overshoot" v-model="state.params.overshoot_m" label="Line overshoot" unit="m" :min="0" :max="1000" placeholder="auto" :help="spec('overshoot_m').help" />
              <NumberField id="fp-transit" v-model="state.params.transit_speed_ms" label="Transit speed" unit="m/s" :min="0.5" :max="60" placeholder="same" :help="spec('transit_speed_ms').help" />
              <NumberField id="fp-rth" v-model="state.params.rth_altitude_m" label="Return height" unit="m" :min="5" :max="2000" placeholder="auto" :help="spec('rth_altitude_m').help" />
              <div class="space-y-1">
                <Label for="fp-start" class="text-xs text-muted-foreground" :title="spec('start').help">Start corner</Label>
                <Select id="fp-start" v-model="state.params.start">
                  <option value="auto">Nearest to takeoff</option>
                  <option v-for="n in 4" :key="n" :value="String(n - 1)">Corner {{ n }}</option>
                </Select>
              </div>
              <div class="space-y-1">
                <Label for="fp-finish" class="text-xs text-muted-foreground" :title="spec('finish_action').help">When finished</Label>
                <Select id="fp-finish" v-model="state.params.finish_action">
                  <option value="rth">Return to takeoff</option>
                  <option value="hover">Hover in place</option>
                  <option value="land">Land in place</option>
                </Select>
              </div>
            </div>
          </details>
        </section>

        <!-- 4. Terrain -->
        <section aria-labelledby="fp-terrain" class="space-y-2">
          <h2 id="fp-terrain" class="text-xs font-medium uppercase tracking-wide text-muted-foreground">4 · Terrain</h2>
          <div class="grid grid-cols-2 gap-1 rounded-md bg-muted p-1 text-sm" role="tablist" aria-label="Altitude mode" data-plan-altitude-mode>
            <button
              v-for="m in ALTITUDE_MODES"
              :key="m.id"
              type="button"
              role="tab"
              :aria-selected="state.params.altitude_mode === m.id"
              class="rounded px-3 py-1.5 transition-colors"
              :class="state.params.altitude_mode === m.id ? 'bg-card text-card-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'"
              :data-mode="m.id"
              @click="state.params.altitude_mode = m.id"
            >{{ m.label }}</button>
          </div>
          <div class="space-y-1">
            <Label for="fp-elevation" class="text-xs text-muted-foreground">Elevation data</Label>
            <Select id="fp-elevation" v-model="state.elevationSource" data-plan-elevation>
              <option v-for="s in sources" :key="s.id" :value="s.id">{{ s.label }}</option>
            </Select>
            <p v-if="currentSource?.note" class="text-[11px] leading-relaxed text-muted-foreground">{{ currentSource.note }}</p>
          </div>
          <NumberField
            v-if="state.params.altitude_mode === 'terrain'"
            id="fp-tolerance"
            v-model="state.params.terrain_tolerance_m"
            label="Following tolerance"
            unit="m"
            :min="0.5"
            :max="50"
            placeholder="auto"
            :help="spec('terrain_tolerance_m').help"
          />
          <template v-if="mission?.terrain">
            <TerrainProfile :terrain="mission.terrain" />
            <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs" data-plan-terrain>
              <dt class="text-muted-foreground">Height above ground</dt>
              <dd class="text-foreground">{{ mission.terrain.agl_min_m }}–{{ mission.terrain.agl_max_m }} m <span class="text-muted-foreground">(estimate)</span></dd>
              <dt class="text-muted-foreground">Lowest clearance</dt>
              <dd class="text-foreground">{{ mission.terrain.clearance_min_m }} m <span class="text-muted-foreground">(estimate)</span></dd>
              <dt class="text-muted-foreground">Ground under the route</dt>
              <dd class="text-foreground">{{ mission.terrain.ground_min_m }}–{{ mission.terrain.ground_max_m }} m above sea level</dd>
            </dl>
          </template>
        </section>

        <!-- 5. Limits -->
        <section aria-labelledby="fp-limits" class="space-y-2">
          <h2 id="fp-limits" class="text-xs font-medium uppercase tracking-wide text-muted-foreground">5 · Limits</h2>
          <div class="space-y-1">
            <Label for="fp-rules" class="text-xs text-muted-foreground" :title="spec('regulation').help">Rule preset (not legal advice)</Label>
            <Select id="fp-rules" v-model="state.params.regulation" data-plan-rules>
              <option v-for="r in catalog?.rule_sets || []" :key="r.id" :value="r.id">{{ r.label }}</option>
            </Select>
            <p v-if="currentRule?.notes" class="text-[11px] leading-relaxed text-muted-foreground">{{ currentRule.notes }}</p>
          </div>
          <div class="grid grid-cols-2 gap-2">
            <NumberField id="fp-maxagl" v-model="state.params.max_agl_m" label="Height limit above ground" unit="m" :min="1" :placeholder="currentRule?.max_agl_m ? String(currentRule.max_agl_m) : 'none'" :help="spec('max_agl_m').help" />
            <NumberField id="fp-vlos" v-model="state.params.vlos_m" label="Line-of-sight distance" unit="m" :min="10" :placeholder="currentRule?.vlos_advisory_m ? String(currentRule.vlos_advisory_m) : 'none'" :help="spec('vlos_m').help" />
            <NumberField id="fp-clearance" v-model="state.params.min_clearance_m" label="Minimum terrain clearance" unit="m" :min="0" :max="500" :help="spec('min_clearance_m').help" />
            <NumberField id="fp-obstacle" v-model="state.params.obstacle_height_m" label="Tallest known obstacle" unit="m" :min="0" :max="1000" :help="spec('obstacle_height_m').help" />
            <NumberField id="fp-reserve" v-model="state.params.reserve_percent" label="Battery reserve" unit="%" :min="0" :max="60" :help="spec('reserve_percent').help" />
          </div>
        </section>

        <!-- Findings -->
        <section v-if="mission" aria-labelledby="fp-findings" class="space-y-2">
          <h2 id="fp-findings" class="text-xs font-medium uppercase tracking-wide text-muted-foreground">Checks</h2>
          <PlanFindings :issues="mission.issues" :assurance="mission.assurance" :notice="mission.notice" @locate="locate" />
        </section>
      </div>

      <footer class="flex items-center gap-2 border-t border-border p-3">
        <Button class="flex-1" variant="outline" :disabled="!mission" :title="exportHint" data-plan-export @click="openExport">
          <FileDown />
          Export…
        </Button>
        <Button
          class="flex-1"
          :variant="simulating ? 'default' : 'outline'"
          :disabled="!mission"
          :title="simulateHint"
          :aria-pressed="simulating"
          data-plan-simulate
          @click="simulating = !simulating"
        >
          <Play />
          {{ simulating ? 'Simulating' : 'Simulate flight' }}
        </Button>
        <Button v-if="savedPlan" variant="ghost" size="icon" title="Duplicate this plan" :loading="duplicating" @click="duplicate">
          <Copy />
          <span class="sr-only">Duplicate this plan</span>
        </Button>
      </footer>
    </aside>

    <!-- Map; the simulator adds its 3D view and its dock around it when opened -->
    <FlightSimulator
      v-model:active="simulating"
      :mission="mission"
      :definition="definition"
      :project="projectId"
      :get-map="() => map"
      :basemap="basemap"
      :ortho="show.ortho ? ortho : null"
    >
      <div ref="mapEl" class="h-full w-full" data-plan-map></div>
      <div class="pointer-events-none absolute inset-0 z-[1000] flex items-start justify-between gap-2 p-3 pl-14">
        <div class="pointer-events-auto min-w-0 flex-1">
          <PlaceSearch @select="goTo" />
        </div>
        <div class="pointer-events-auto flex max-h-full min-h-0 flex-col items-end gap-2">
          <div class="flex items-center gap-2">
            <Button variant="outline" size="sm" class="bg-card" title="Zoom to the plan" :disabled="!hasExtent" @click="fit">Zoom to plan</Button>
            <Button
              variant="outline"
              size="icon"
              class="h-8 w-8 bg-card"
              :title="panelOpen ? 'Hide map options' : 'Show map options'"
              :aria-expanded="panelOpen"
              @click="panelOpen = !panelOpen"
            >
              <X v-if="panelOpen" />
              <Layers v-else />
              <span class="sr-only">{{ panelOpen ? 'Hide map options' : 'Show map options' }}</span>
            </Button>
          </div>
          <div v-show="panelOpen" class="min-h-0 w-[210px] max-w-full overflow-y-auto rounded-lg border border-border bg-card text-sm shadow-md">
            <div class="border-b border-border p-3">
              <p class="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Basemap</p>
              <label v-for="b in BASEMAPS" :key="b.id" class="flex cursor-pointer items-center gap-2 py-0.5 text-foreground">
                <input type="radio" name="fp-basemap" :value="b.id" :checked="basemap === b.id" @change="setBasemap(b.id)" />
                {{ b.label }}
              </label>
            </div>
            <div class="p-3">
              <p class="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Show</p>
              <label class="flex cursor-pointer items-center gap-2 py-0.5 text-foreground">
                <input v-model="show.waypoints" type="checkbox" class="rounded" /> Waypoints
              </label>
              <label class="flex cursor-pointer items-center gap-2 py-0.5 text-foreground">
                <input v-model="show.footprint" type="checkbox" class="rounded" /> One photo's footprint
              </label>
              <label v-if="ortho" class="flex cursor-pointer items-center gap-2 py-0.5 text-foreground" :title="`Orthophoto of ${ortho.title}`">
                <input v-model="show.ortho" type="checkbox" class="rounded" /> Latest orthophoto
              </label>
            </div>
            <div class="space-y-1 border-t border-border p-3 text-[11px] text-muted-foreground">
              <p class="flex items-center gap-1.5"><span class="inline-block h-0.5 w-4 bg-[#f59e0b]"></span>Flight line (camera on)</p>
              <p class="flex items-center gap-1.5"><span class="inline-block h-0 w-4 border-t-2 border-dashed border-slate-500"></span>Turn</p>
              <p class="flex items-center gap-1.5"><span class="inline-block size-2.5 rounded-sm bg-emerald-500/40 ring-1 ring-emerald-500"></span>One photo</p>
              <p class="flex items-center gap-1.5"><span class="inline-flex size-3.5 items-center justify-center rounded-full bg-violet-600 text-[8px] font-bold text-white">H</span>Takeoff point</p>
            </div>
          </div>
        </div>
      </div>
      <p
        v-if="placingTakeoff"
        class="pointer-events-none absolute bottom-4 left-1/2 z-[1000] -translate-x-1/2 rounded-full bg-card px-4 py-1.5 text-sm text-foreground shadow-md"
        role="status"
      >Click the map where you will launch</p>
    </FlightSimulator>

    <ExportDialog v-model:open="showExport" :plan="savedPlan" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import {
  ArrowLeft, Check, Copy, FileDown, Layers, LoaderCircle, Navigation, PenLine, Play, Route, Save, Square, X,
} from 'lucide-vue-next'
import L from 'leaflet'
import { Alert, Badge, Button, Input, Label, Select } from '@/components/ui'
import NumberField from '@/components/flightplan/NumberField.vue'
import PlaceSearch from '@/components/flightplan/PlaceSearch.vue'
import PlanFindings from '@/components/flightplan/PlanFindings.vue'
import TerrainProfile from '@/components/flightplan/TerrainProfile.vue'
import ExportDialog from '@/components/flightplan/ExportDialog.vue'
import FlightSimulator from '@/components/flightsim/FlightSimulator.vue'
import { BASEMAPS, createBasemap } from '@/lib/mapLayers'
import { buildPlanLayers, takeoffIcon } from '@/lib/planLayers'
import { useShapeEditor } from '@/composables/useShapeEditor'
import { listTasks } from '@/lib/datasets'
import { toast } from '@/lib/toast'
import { ensureCsrfToken } from '@/lib/session'
import {
  MIN_VERTICES,
  buildDefinition,
  cameraAfterDroneChange,
  cameraSpecRows,
  camerasFor,
  canPreview,
  detailRows,
  droneSpecRows,
  duplicatePlan,
  emptyPlanState,
  estimateRows,
  formatCount,
  formatDuration,
  formatLength,
  getCatalog,
  getElevationSources,
  getPlan,
  groupDrones,
  gsdCm,
  heightForGsd,
  missionSupportSummary,
  planFingerprint,
  planStatus,
  previewPlan,
  savePlan,
  stateFromPlan,
} from '@/lib/flightplan'

const TYPES = [
  { id: 'area', label: 'Area', icon: Square, help: 'Draw the outline of the area to map. The planner lays parallel flight lines across it.' },
  { id: 'corridor', label: 'Corridor', icon: Route, help: 'Draw a line along a road, river, railway or pipeline and give the width to cover on it.' },
]
const ALTITUDE_MODES = [
  { id: 'constant', label: 'Constant altitude' },
  { id: 'terrain', label: 'Follow terrain' },
]
const PREVIEW_DELAY_MS = 300

const route = useRoute()
const router = useRouter()
const projectId = computed(() => String(route.params.id))
const planId = computed(() => (route.params.planId && route.params.planId !== 'new' ? String(route.params.planId) : null))

const catalog = ref(null)
const specs = computed(() => catalog.value?.params || [])
const project = ref(null)
const sources = ref([{ id: 'auto', label: 'Global elevation model' }, { id: 'none', label: 'None (assume flat ground)' }])
const state = reactive(emptyPlanState([]))
const mission = ref(null)
const previewError = ref(null)
const computing = ref(false)
const terrainLoading = ref(false)
const savedPlan = ref(null)
const savedPrint = ref('')
const saving = ref(false)
const duplicating = ref(false)
const showExport = ref(false)
const placingTakeoff = ref(false)
const simulating = ref(false)        // the flight simulator is open over the map
const panelOpen = ref(false)
const basemap = ref('esri-satellite')
const show = reactive({ waypoints: true, footprint: true, ortho: false })
const ortho = ref(null)             // { task, title, bounds } of the project's latest orthophoto
const mapEl = ref(null)
const loaded = ref(false)

let map = null
let baseLayer = null
let planGroup = null
let planBounds = null
let takeoffMarker = null
let orthoLayer = null
let previewTimer = null
let previewSeq = 0
// The definition (as JSON) the mission on screen was computed from. A saved
// plan is shown as stored, so loading one must not trigger a recompute.
let computedFor = null

const editor = useShapeEditor(() => map, {
  onChange(vertices, { final }) {
    // While a corner is being dragged the shape follows the pointer; the
    // mission is recomputed once, when the drag ends.
    if (final) state.vertices = vertices
  },
})

// -- derived ---------------------------------------------------------------------

const spec = name => specs.value.find(s => s.name === name) || { help: '' }
const drone = computed(() => catalog.value?.drones.find(d => d.id === state.drone) || null)
const camera = computed(() => catalog.value?.cameras.find(c => c.id === state.camera) || null)
const droneGroups = computed(() => groupDrones(catalog.value?.drones))
const cameraChoices = computed(() => camerasFor(drone.value, catalog.value?.cameras))
const currentSource = computed(() => sources.value.find(s => s.id === state.elevationSource) || null)
const currentRule = computed(() => catalog.value?.rule_sets.find(r => r.id === state.params.regulation) || null)
const directionAuto = computed(() => state.params.direction === 'auto' || state.params.direction === null)
const estimates = computed(() => estimateRows(mission.value))
const details = computed(() => detailRows(mission.value))
const status = computed(() => planStatus(mission.value?.summary))
const definition = computed(() => buildDefinition(state, specs.value))
const print = computed(() => planFingerprint(state, specs.value))
const dirty = computed(() => loaded.value && print.value !== savedPrint.value)
const hasExtent = computed(() => state.vertices.length > 0)
const canSave = computed(() => !!mission.value && !!state.title.trim() && !saving.value && (dirty.value || !savedPlan.value))
const saveHint = computed(() => {
  if (!state.title.trim()) return 'Give the plan a title to save it'
  if (!mission.value) return 'Draw the survey first'
  return savedPlan.value && !dirty.value ? 'Nothing to save' : 'Save this plan in the project'
})
const exportHint = computed(() => {
  if (!mission.value) return 'Draw the survey first'
  return dirty.value || !savedPlan.value ? 'Saves the plan, then opens the export options' : 'Export this plan'
})
const simulateHint = computed(() => {
  if (!mission.value) return 'Draw the survey first'
  return simulating.value ? 'Close the simulation' : 'Replay this plan in 2D and 3D: an estimate, not a flight-safety check'
})
const startHint = computed(() => {
  if (!state.vertices.length) {
    return 'Find your site with the search box on the map, then draw the survey. Estimates appear here as soon as the shape is complete.'
  }
  return state.type === 'area' ? 'Add at least three corners to see the estimates.' : 'Add at least two points to see the estimates.'
})
const drawHint = computed(() => {
  if (editor.state.drawing) {
    return state.type === 'area'
      ? 'Click each corner on the map. Click the first corner again, double-click or press Enter to finish; Backspace removes the last corner.'
      : 'Click along the centre line. Double-click or press Enter to finish; Backspace removes the last point.'
  }
  if (state.vertices.length) return 'Drag a corner to move it, drag a faint handle to add one, right-click a corner to remove it.'
  return ''
})

// -- form actions ------------------------------------------------------------------

function setType(type) {
  if (state.type === type) return
  state.type = type
  // The corners carry over: an outline becomes a centre line and back.
  if (editor.state.drawing) editor.start(type)
  else editor.setShape(type, state.vertices)
}

function startDrawing() {
  if (state.vertices.length && !confirm('Discard the current shape and draw a new one?')) return
  placingTakeoff.value = false
  editor.start(state.type)
}

function setDrone(id) {
  state.drone = id
  const next = catalog.value?.drones.find(d => d.id === id) || null
  setCamera(cameraAfterDroneChange(next, catalog.value?.cameras, state.camera))
}

function setCamera(id) {
  state.camera = id
  syncLinked()
}

// Height and resolution are one quantity seen two ways: whichever the user
// typed last is the input, the other is shown derived from it.
function syncLinked() {
  const cam = camera.value
  if (!cam) return
  if (state.params.altitude_from === 'gsd' && state.params.gsd_cm > 0) {
    state.params.altitude_m = Math.round(heightForGsd(cam, state.params.gsd_cm) * 10) / 10
  } else if (state.params.altitude_m > 0) {
    state.params.gsd_cm = Math.round(gsdCm(cam, state.params.altitude_m) * 100) / 100
  }
}

function setAltitude(value) {
  state.params.altitude_m = value
  state.params.altitude_from = 'altitude'
  syncLinked()
}

function setGsd(value) {
  state.params.gsd_cm = value
  state.params.altitude_from = 'gsd'
  syncLinked()
}

function toggleDirectionAuto(e) {
  state.params.direction = e.target.checked ? 'auto' : Math.round(mission.value?.layout.direction_deg ?? 0)
}

// -- preview -----------------------------------------------------------------------

function schedulePreview() {
  if (!loaded.value) return
  clearTimeout(previewTimer)
  previewTimer = setTimeout(runPreview, PREVIEW_DELAY_MS)
}

async function runPreview() {
  const seq = ++previewSeq
  computedFor = JSON.stringify(definition.value)
  if (!canPreview(state)) {
    mission.value = null
    previewError.value = null
    computing.value = terrainLoading.value = false
    drawPlan()
    return
  }
  computing.value = true
  const request = { definition: definition.value, project: projectId.value, plan: savedPlan.value?.name }
  try {
    // First an immediate answer from whatever elevation data is cached, then,
    // only if that had to skip the terrain, the full one.
    let out = await previewPlan({ ...request, terrain: 'cached' })
    if (seq !== previewSeq) return
    apply(out)
    if (out.ok && out.mission.terrain_pending) {
      terrainLoading.value = true
      out = await previewPlan({ ...request, terrain: 'fetch' })
      if (seq !== previewSeq) return
      apply(out)
    }
  } catch (e) {
    if (seq !== previewSeq) return
    mission.value = null
    previewError.value = { message: e.message || 'The plan could not be computed', field: null }
    drawPlan()
  } finally {
    if (seq === previewSeq) computing.value = terrainLoading.value = false
  }
}

function apply(out) {
  if (out.ok) {
    mission.value = out.mission
    previewError.value = null
  } else {
    mission.value = null
    previewError.value = out.error
  }
  drawPlan()
}

// -- map ---------------------------------------------------------------------------

function drawPlan() {
  if (!map) return
  if (planGroup) map.removeLayer(planGroup)
  const built = buildPlanLayers(mission.value, { waypoints: show.waypoints, footprint: show.footprint })
  planGroup = built.group
  planBounds = built.bounds
  planGroup.addTo(map)
  drawTakeoff()
}

function drawTakeoff() {
  if (!map) return
  if (takeoffMarker) { map.removeLayer(takeoffMarker); takeoffMarker = null }
  const assumed = !state.takeoff
  const at = state.takeoff || (mission.value ? mission.value.takeoff : null)
  if (!at) return
  takeoffMarker = L.marker([at.lat, at.lng], {
    icon: takeoffIcon(assumed), draggable: !assumed, keyboard: false, zIndexOffset: 1200,
    title: assumed ? 'Assumed takeoff point (first waypoint). Set the real one.' : 'Takeoff point: drag to move',
  })
  if (!assumed) {
    takeoffMarker.on('dragend', e => {
      const p = e.target.getLatLng()
      state.takeoff = { lat: p.lat, lng: p.lng }
    })
  }
  takeoffMarker.addTo(map)
}

function onMapClick(e) {
  if (!placingTakeoff.value || editor.state.drawing) return
  state.takeoff = { lat: e.latlng.lat, lng: e.latlng.lng }
  placingTakeoff.value = false
}

function setBasemap(id) {
  basemap.value = id
  if (!map) return
  if (baseLayer) map.removeLayer(baseLayer)
  baseLayer = createBasemap(id)
  baseLayer.addTo(map)
  baseLayer.bringToBack()
}

function fit() {
  if (!map) return
  const bounds = planBounds || editor.bounds()
  // Extra room at the top: the search box and map buttons sit there, and a
  // corner under them could not be grabbed.
  if (bounds && bounds.isValid()) {
    map.fitBounds(bounds, { paddingTopLeft: [48, 84], paddingBottomRight: [48, 48], maxZoom: 19 })
  }
}

function goTo(result) {
  if (!map) return
  if (result.bbox) {
    const [west, south, east, north] = result.bbox
    map.fitBounds([[south, west], [north, east]], { maxZoom: 17 })
  } else {
    map.setView([result.lat, result.lng], 17)
  }
}

function locate(lnglat) {
  if (map && Array.isArray(lnglat)) map.setView([lnglat[1], lnglat[0]], Math.max(map.getZoom(), 17))
}

function setOrtho(visible) {
  if (!map || !ortho.value) return
  if (orthoLayer) { map.removeLayer(orthoLayer); orthoLayer = null }
  if (!visible) return
  const url = `/api/method/webodm_core.api.tiles.serve?task_name=${encodeURIComponent(ortho.value.task)}`
    + '&dataset=orthophoto&z={z}&x={x}&y={y}'
  orthoLayer = L.tileLayer(url, { bounds: ortho.value.bounds || undefined, maxNativeZoom: 22, maxZoom: 24, tileSize: 256 })
  orthoLayer.addTo(map)
}

function extentBounds(extent) {
  try {
    const geo = typeof extent === 'string' ? JSON.parse(extent) : extent
    const ring = geo?.coordinates?.[0]
    if (!ring?.length) return null
    const b = L.latLngBounds()
    for (const [lng, lat] of ring) b.extend([lat, lng])
    return b
  } catch {
    return null
  }
}

// -- save / export -------------------------------------------------------------------

async function save() {
  if (!mission.value || !state.title.trim()) return null
  saving.value = true
  try {
    const saved = await savePlan({
      name: savedPlan.value?.name,
      project: projectId.value,
      title: state.title.trim(),
      description: state.description,
      definition: definition.value,
    })
    const created = !savedPlan.value
    savedPlan.value = saved
    mission.value = saved.mission
    savedPrint.value = print.value
    drawPlan()
    toast.success(created ? 'Flight plan saved' : 'Changes saved')
    if (created) {
      router.replace(`/project/${encodeURIComponent(projectId.value)}/plans/${encodeURIComponent(saved.name)}`)
    }
    return saved
  } catch (e) {
    toast.error(e.message || 'Could not save the flight plan')
    return null
  } finally {
    saving.value = false
  }
}

async function openExport() {
  if (!mission.value) return
  if (!savedPlan.value || dirty.value) {
    if (!state.title.trim()) { toast.error('Give the plan a title first: exports are made from the saved plan'); return }
    if (!(await save())) return
  }
  showExport.value = true
}

async function duplicate() {
  if (!savedPlan.value) return
  duplicating.value = true
  try {
    const copy = await duplicatePlan(savedPlan.value.name)
    toast.success(`Duplicated as "${copy.title}"`)
    router.push(`/project/${encodeURIComponent(projectId.value)}/plans/${encodeURIComponent(copy.name)}`)
  } catch (e) {
    toast.error(e.message || 'Could not duplicate the plan')
  } finally {
    duplicating.value = false
  }
}

// -- loading ------------------------------------------------------------------------

async function loadProject() {
  try {
    const res = await fetch(`/api/resource/WebODM%20Project/${encodeURIComponent(projectId.value)}`)
    project.value = res.ok ? (await res.json()).data : null
  } catch {
    project.value = null
  }
}

async function loadOrtho() {
  try {
    const tasks = await listTasks(projectId.value)
    const done = (tasks || []).find(t => t.status === 'Completed' && t.orthophoto)
    if (done) ortho.value = { task: done.name, title: done.title || done.name, bounds: extentBounds(done.orthophoto_extent) }
  } catch {
    ortho.value = null
  }
}

async function load() {
  loaded.value = false
  simulating.value = false
  previewSeq++
  mission.value = null
  previewError.value = null
  savedPlan.value = null
  try {
    if (!catalog.value) catalog.value = await getCatalog()
  } catch (e) {
    toast.error(e.message || 'Could not load the flight planning catalog')
    return
  }
  Object.assign(state, emptyPlanState(specs.value))
  getElevationSources(projectId.value).then(list => { sources.value = list }).catch(() => {})

  if (planId.value) {
    try {
      const plan = await getPlan(planId.value)
      Object.assign(state, stateFromPlan(plan, specs.value))
      savedPlan.value = plan
      mission.value = plan.mission?.waypoints ? plan.mission : null
    } catch (e) {
      toast.error(e.message || 'Could not open the flight plan')
      router.replace(`/project/${encodeURIComponent(projectId.value)}/plans`)
      return
    }
  } else {
    const first = catalog.value.drones.find(d => d.id === 'dji-mavic-3e') || catalog.value.drones[0]
    state.drone = first?.id || null
    state.camera = first?.default_camera || null
    state.elevationSource = catalog.value.elevation.enabled ? 'auto' : 'none'
    syncLinked()
  }
  editor.setShape(state.type, state.vertices)
  savedPrint.value = savedPlan.value ? print.value : ''
  computedFor = mission.value ? JSON.stringify(definition.value) : null
  loaded.value = true
  drawPlan()
  if (state.vertices.length) fit()
  else if (ortho.value?.bounds) map.fitBounds(ortho.value.bounds, { padding: [30, 30] })
  // A saved plan is shown as saved; a new one computes as soon as it can.
  if (!savedPlan.value) schedulePreview()
  // "Simulate" on the plan list opens the plan with its simulation.
  if (route.query?.simulate && mission.value) simulating.value = true
}

// Only the definition changes the mission; the title and description do not.
watch(() => JSON.stringify(definition.value), now => {
  if (loaded.value && now !== computedFor) schedulePreview()
})
// Nothing to simulate once the route is gone (the shape was cleared, or the plan no longer computes).
watch(mission, now => { if (!now) simulating.value = false })
watch(() => [show.waypoints, show.footprint], drawPlan)
watch(() => show.ortho, setOrtho)
watch(() => state.takeoff, drawTakeoff)
watch(() => route.params.planId, (now, before) => {
  // Saving a new plan replaces /new with its id: same plan, nothing to reload.
  if (now === before || (savedPlan.value && now === savedPlan.value.name)) return
  load()
})

onBeforeRouteLeave(() => {
  if (dirty.value && state.vertices.length && !confirm('This flight plan has unsaved changes. Leave without saving?')) return false
  return true
})

function onBeforeUnload(e) {
  if (dirty.value && state.vertices.length) { e.preventDefault(); e.returnValue = '' }
}

onMounted(async () => {
  map = L.map(mapEl.value, { zoomControl: true }).setView([0, 0], 2)
  baseLayer = createBasemap(basemap.value)
  baseLayer.addTo(map)
  map.on('click', onMapClick)
  window.addEventListener('beforeunload', onBeforeUnload)
  // On a full page load the session token is still being fetched; every
  // request below is a POST and would be refused without it.
  await ensureCsrfToken()
  loadProject()
  await loadOrtho()
  await load()
})

onUnmounted(() => {
  clearTimeout(previewTimer)
  previewSeq++
  window.removeEventListener('beforeunload', onBeforeUnload)
  editor.destroy()
  if (map) {
    map.off('click', onMapClick)
    map.remove()
    map = null
  }
})
</script>
