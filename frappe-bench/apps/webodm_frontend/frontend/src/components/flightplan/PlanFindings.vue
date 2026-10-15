<script setup>
import { computed } from 'vue'
import { CircleAlert, CircleHelp, Info, TriangleAlert } from 'lucide-vue-next'
import { Badge } from '@/components/ui'
import { basisInfo, groupIssues, severityInfo } from '@/lib/flightplan'

// What the planner found, and how far each statement can be trusted.
//
// Two lists, kept visibly apart:
//   - findings: what is wrong or worth knowing about this plan;
//   - "what was checked": per topic, whether it was checked against the
//     profile, estimated, compared with advisory data, or not looked at.
// The basis badge is on every line so an estimate never reads as a fact.
const props = defineProps({
  issues: { type: Array, default: () => [] },
  assurance: { type: Array, default: () => [] },
  notice: { type: String, default: '' },
  compact: { type: Boolean, default: false },
})
defineEmits(['locate'])

const groups = computed(() => groupIssues(props.issues))
const ordered = computed(() => [...groups.value.error, ...groups.value.warning, ...groups.value.info])
const ICONS = { error: CircleAlert, warning: TriangleAlert, info: Info }
const TONE = {
  error: 'border-destructive/30 bg-destructive/10',
  warning: 'border-warning/30 bg-warning/10',
  info: 'border-border bg-muted/40',
}
const ICON_TONE = { error: 'text-destructive', warning: 'text-warning', info: 'text-muted-foreground' }
</script>

<template>
  <div class="space-y-3" data-plan-findings>
    <ul v-if="ordered.length" class="space-y-1.5" aria-label="Findings">
      <li
        v-for="issue in ordered"
        :key="issue.code + issue.message"
        class="rounded-md border px-2.5 py-2 text-xs"
        :class="TONE[issue.severity]"
        :data-finding="issue.code"
        :data-severity="issue.severity"
      >
        <div class="flex items-start gap-2">
          <component :is="ICONS[issue.severity]" class="mt-0.5 size-3.5 flex-shrink-0" :class="ICON_TONE[issue.severity]" aria-hidden="true" />
          <div class="min-w-0 flex-1">
            <p class="break-words text-foreground">
              <span class="sr-only">{{ severityInfo(issue.severity).label }}: </span>{{ issue.message }}
            </p>
            <div v-if="basisInfo(issue.basis).label || issue.detail?.at" class="mt-1 flex flex-wrap items-center gap-1.5">
              <Badge
                v-if="basisInfo(issue.basis).label"
                :variant="basisInfo(issue.basis).variant"
                class="text-[10px]"
                :title="basisInfo(issue.basis).hint"
              >
                {{ basisInfo(issue.basis).label }}
              </Badge>
              <button
                v-if="issue.detail?.at"
                type="button"
                class="text-[11px] text-primary hover:underline"
                @click="$emit('locate', issue.detail.at)"
              >Show on map</button>
            </div>
          </div>
        </div>
      </li>
    </ul>
    <p v-else class="text-xs text-muted-foreground">No findings for this plan.</p>

    <div v-if="assurance.length && !compact" class="rounded-md border border-border" data-assurance>
      <p class="flex items-center gap-1.5 border-b border-border px-2.5 py-1.5 text-xs font-medium text-foreground">
        <CircleHelp class="size-3.5 text-muted-foreground" aria-hidden="true" />
        What was and was not checked
      </p>
      <dl class="divide-y divide-border">
        <div v-for="a in assurance" :key="a.topic" class="px-2.5 py-2" :data-assurance-topic="a.topic" :data-status="a.status">
          <dt class="flex items-center justify-between gap-2 text-xs font-medium text-foreground">
            {{ a.title }}
            <Badge :variant="basisInfo(a.status).variant" class="text-[10px]" :title="basisInfo(a.status).hint">
              {{ basisInfo(a.status).label }}
            </Badge>
          </dt>
          <dd class="mt-1 text-[11px] leading-relaxed text-muted-foreground">{{ a.summary }}</dd>
        </div>
      </dl>
    </div>

    <p v-if="notice && !compact" class="rounded-md border border-warning/30 bg-warning/10 px-2.5 py-2 text-[11px] leading-relaxed text-foreground" role="note">
      {{ notice }}
    </p>
  </div>
</template>
