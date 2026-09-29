<script setup>
/**
 * Marketplace pages render inside AppLayout when signed in (App.vue decides
 * from `layout: 'auto'`) and need their own chrome otherwise. This wrapper
 * keeps that decision in one place so the pages only render content.
 */
import PublicShell from '@/components/PublicShell.vue'
import { loggedIn } from '@/lib/session.js'
</script>

<template>
  <div v-if="loggedIn" class="space-y-6">
    <slot />
  </div>
  <PublicShell v-else>
    <div class="space-y-6">
      <slot />
    </div>
  </PublicShell>
</template>
