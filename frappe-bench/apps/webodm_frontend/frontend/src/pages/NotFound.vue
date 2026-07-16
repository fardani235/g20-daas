<template>
  <div class="h-full flex flex-col items-center justify-center text-center p-6">
    <FeatherIcon name="map-off" class="h-16 w-16 text-gray-300 dark:text-gray-600 mb-4" />
    <h1 class="text-2xl font-semibold text-gray-900 dark:text-gray-100 mb-2">Page not found</h1>
    <p class="text-gray-500 dark:text-gray-400 mb-6">The page you're looking for doesn't exist.</p>
    <Button variant="solid" @click="$router.push('/')">Back to Dashboard</Button>
  </div>
</template>

<script setup>
import { Button, FeatherIcon } from 'frappe-ui'
</script>
