<template>
  <div class="space-y-2">
    <details
      v-for="group in groups"
      :key="group.name"
      :open="group.name === 'General'"
      class="rounded-lg border dark:border-gray-700"
    >
      <summary class="cursor-pointer select-none px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-300">
        {{ group.name }}
        <span class="text-gray-400 dark:text-gray-500">({{ group.options.length }})</span>
      </summary>
      <div class="space-y-1.5 px-3 pb-3 pt-1">
        <div v-for="opt in group.options" :key="opt.name" class="flex items-center gap-2">
          <label class="text-sm text-gray-600 dark:text-gray-400 w-44 truncate" :title="opt.help">{{ opt.name }}</label>
          <input
            v-if="fieldType(opt) === 'checkbox'"
            type="checkbox"
            v-model="modelValue[opt.name]"
            class="rounded"
          />
          <select
            v-else-if="fieldType(opt) === 'select'"
            v-model="modelValue[opt.name]"
            class="flex-1 rounded border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-2 py-1 text-sm"
          >
            <option v-for="d in opt.domain" :key="d" :value="d">{{ d }}</option>
          </select>
          <input
            v-else-if="fieldType(opt) === 'number'"
            type="number"
            v-model.number="modelValue[opt.name]"
            class="flex-1 rounded border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-2 py-1 text-sm"
          />
          <input
            v-else
            type="text"
            v-model="modelValue[opt.name]"
            class="flex-1 rounded border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-2 py-1 text-sm"
          />
        </div>
      </div>
    </details>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { groupOptions } from '@/lib/odmCategories'

// modelValue is a shared reactive values object ({ [optName]: value }); we mutate
// its keys in place (matching the previous inline behavior), so the parent stays
// the source of truth and no update:modelValue re-emit is needed.
const props = defineProps({
  catalog: { type: Array, default: () => [] },
  modelValue: { type: Object, required: true },
  fieldType: { type: Function, required: true },
})

const groups = computed(() => groupOptions(props.catalog))
</script>
