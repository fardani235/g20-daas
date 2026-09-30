<template>
  <div class="fixed top-0 left-0 right-0 z-[99999] h-0.5 transition-opacity duration-200" :class="navigating ? 'opacity-100' : 'opacity-0'">
    <div class="h-full bg-primary animate-pulse"></div>
  </div>
  <Toaster
    position="bottom-right"
    :toast-options="{
      class: 'bg-card text-card-foreground border border-border rounded-lg shadow-lg',
    }"
  />
  <AppLayout v-if="layoutFor(route.meta, loggedIn) === 'app'" />
  <router-view v-else v-slot="{ Component }">
    <Transition name="page" mode="out-in">
      <component :is="Component" />
    </Transition>
  </router-view>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Toaster } from 'vue-sonner'
import AppLayout from './components/AppLayout.vue'
import { useTheme } from './composables/useTheme'
import { layoutFor, loggedIn, ensureCsrfToken } from './lib/session.js'

const route = useRoute()
const router = useRouter()
const navigating = ref(false)
const { init, cleanup } = useTheme()

router.beforeEach((to, from) => {
  if (to.path !== from.path) {
    navigating.value = true
  }
})

router.afterEach(() => {
  navigating.value = false
})

onMounted(() => {
  init()
  if (loggedIn.value) ensureCsrfToken()
})

// The guard resolves the session asynchronously, so the token is fetched once
// it is known the visitor is signed in. Guests never call the authenticated-only
// token endpoint, so public pages no longer produce a 403.
watch(loggedIn, value => {
  if (value) ensureCsrfToken()
})

onUnmounted(() => {
  cleanup()
})
</script>
