<template>
  <div class="p-6 space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100">Plugins</h2>
      <Button variant="solid" theme="blue">
        <template #prefix><FeatherIcon name="plus" class="h-4 w-4" /></template>
        Install Plugin
      </Button>
    </div>

    <div class="bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b dark:border-gray-700 text-left text-gray-500 dark:text-gray-400">
            <th class="px-4 py-3 font-medium">Name</th>
            <th class="px-4 py-3 font-medium">Version</th>
            <th class="px-4 py-3 font-medium">Status</th>
            <th class="px-4 py-3 font-medium text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="plugin in plugins"
            :key="plugin.name"
            class="border-b dark:border-gray-700 last:border-0 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
          >
            <td class="px-4 py-3 font-medium text-gray-900 dark:text-gray-100">{{ plugin.name }}</td>
            <td class="px-4 py-3 text-gray-600 dark:text-gray-400">{{ plugin.version }}</td>
            <td class="px-4 py-3">
              <span
                class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
                :class="plugin.enabled ? 'bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-300' : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'"
              >
                {{ plugin.enabled ? 'Enabled' : 'Disabled' }}
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

const plugins = ref([
  { name: 'Change Detection', version: '1.0.0', enabled: true },
  { name: 'Object Detection', version: '1.0.0', enabled: true },
  { name: 'Plantation Health', version: '1.0.0', enabled: false },
  { name: 'Tree Counting', version: '1.0.0', enabled: false },
  { name: 'AI Analytics', version: '1.0.0', enabled: false },
])
</script>
