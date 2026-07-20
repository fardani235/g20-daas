<template>
  <div class="p-6 space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100">Processing Presets</h2>
      <Button variant="solid" theme="blue" @click="showCreate = true">
        <template #prefix><FeatherIcon name="plus" class="h-4 w-4" /></template>
        New Preset
      </Button>
    </div>

    <div class="bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b dark:border-gray-700 text-left text-gray-500 dark:text-gray-400">
            <th class="px-4 py-3 font-medium">Name</th>
            <th class="px-4 py-3 font-medium">Description</th>
            <th class="px-4 py-3 font-medium">Scope</th>
            <th class="px-4 py-3 font-medium text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="preset in presets"
            :key="preset.name"
            class="border-b dark:border-gray-700 last:border-0 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
          >
            <td class="px-4 py-3 font-medium text-gray-900 dark:text-gray-100">{{ preset.name }}</td>
            <td class="px-4 py-3 text-gray-600 dark:text-gray-400">{{ preset.description }}</td>
            <td class="px-4 py-3">
              <span
                class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
                :class="preset.scope === 'System' ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300' : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'"
              >
                {{ preset.scope }}
              </span>
            </td>
            <td class="px-4 py-3 text-right">
              <button class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
                <FeatherIcon name="more-vertical" class="h-4 w-4" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Button, FeatherIcon } from 'frappe-ui'

const showCreate = ref(false)

const presets = ref([
  { name: 'High Quality', description: 'Max resolution, all outputs enabled', scope: 'System' },
  { name: 'Fast Processing', description: 'Reduced quality for quick results', scope: 'System' },
  { name: '3D Model Only', description: 'Generates point cloud and 3D model only', scope: 'User' },
])
</script>
