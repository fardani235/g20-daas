<template>
  <div class="p-6 space-y-6 max-w-2xl">
    <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100">Settings</h2>

    <!-- General -->
    <div class="bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 p-6 space-y-4">
      <h3 class="font-medium text-gray-900 dark:text-gray-100">General</h3>
      <div class="space-y-3">
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Default Basemap</label>
          <select class="w-full rounded-lg border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-3 py-2 text-sm">
            <option>OSM Streets</option>
            <option>Esri Satellite</option>
            <option>Esri Topographic</option>
          </select>
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Max Upload Size (MB)</label>
          <input type="number" value="200" class="w-full rounded-lg border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-3 py-2 text-sm" />
        </div>
      </div>
    </div>

    <!-- Processing -->
    <div class="bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 p-6 space-y-4">
      <h3 class="font-medium text-gray-900 dark:text-gray-100">Processing</h3>
      <div class="space-y-3">
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Default Resolution (cm/px)</label>
          <input type="number" value="5" class="w-full rounded-lg border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-3 py-2 text-sm" />
        </div>
        <div class="flex items-center gap-2">
          <input type="checkbox" id="auto-process" class="rounded border-gray-300 dark:border-gray-600" />
          <label for="auto-process" class="text-sm text-gray-700 dark:text-gray-300">Auto-start processing after upload</label>
        </div>
      </div>
    </div>

    <!-- Notifications -->
    <div class="bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 p-6 space-y-4">
      <h3 class="font-medium text-gray-900 dark:text-gray-100">Notifications</h3>
      <div class="space-y-3">
        <div class="flex items-center gap-2">
          <input type="checkbox" id="email-done" checked class="rounded border-gray-300 dark:border-gray-600" />
          <label for="email-done" class="text-sm text-gray-700 dark:text-gray-300">Email on task completion</label>
        </div>
        <div class="flex items-center gap-2">
          <input type="checkbox" id="email-fail" checked class="rounded border-gray-300 dark:border-gray-600" />
          <label for="email-fail" class="text-sm text-gray-700 dark:text-gray-300">Email on task failure</label>
        </div>
      </div>
    </div>

    <div class="flex justify-end">
      <Button variant="solid" theme="blue">
        <template #prefix><FeatherIcon name="check" class="h-4 w-4" /></template>
        Save Settings
      </Button>
    </div>
  </div>
</template>

<script setup>
import { Button } from 'frappe-ui'
</script>
