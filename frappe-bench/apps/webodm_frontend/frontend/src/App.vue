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
  <AppLayout v-if="route.meta.layout !== false" />
  <router-view v-else v-slot="{ Component }">
    <Transition name="page" mode="out-in">
      <component :is="Component" />
    </Transition>
  </router-view>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Toaster } from 'vue-sonner'
import AppLayout from './components/AppLayout.vue'
import { useTheme } from './composables/useTheme'

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

onMounted(async () => {
  init()
  if (!window.csrf_token) {
    try {
      const res = await fetch('/api/method/webodm_core.api.csrf.get_token')
      if (res.ok) {
        const { message: token } = await res.json()
        window.csrf_token = token
      }
    } catch {}
  }
})

onUnmounted(() => {
  cleanup()
})
</script>
