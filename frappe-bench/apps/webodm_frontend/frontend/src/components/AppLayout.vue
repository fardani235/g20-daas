<template>
  <div class="flex h-screen bg-gray-50 dark:bg-gray-900">
    <aside class="w-56 bg-white dark:bg-gray-900 border-r dark:border-gray-700 flex flex-col flex-shrink-0">
      <div class="h-14 flex items-center px-4 border-b dark:border-gray-700">
        <FeatherIcon name="map" class="h-5 w-5 text-blue-600 dark:text-blue-400 mr-2" />
        <span class="font-semibold text-gray-900 dark:text-gray-100">G20 Tech</span>
      </div>
      <nav class="flex-1 p-3 space-y-1">
        <router-link
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors"
          :class="isActive(item.to) ? 'bg-blue-50 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300' : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'"
        >
          <FeatherIcon :name="item.icon" class="h-4 w-4" />
          {{ item.label }}
        </router-link>
      </nav>
      <div class="p-3 border-t dark:border-gray-700 space-y-1">
        <button
          class="flex items-center gap-2 px-3 py-2 w-full rounded-lg text-sm text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          @click="cycle"
        >
          <FeatherIcon :name="themeIcon" class="h-4 w-4" />
          {{ themeLabel }}
        </button>
        <button
          class="flex items-center gap-2 px-3 py-2 w-full rounded-lg text-sm text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          @click="logout"
        >
          <FeatherIcon name="log-out" class="h-4 w-4" />
          Sign out
        </button>
      </div>
    </aside>
    <div class="flex-1 flex flex-col min-w-0">
      <header class="h-14 bg-white dark:bg-gray-900 border-b dark:border-gray-700 flex items-center px-6 flex-shrink-0">
        <h1 class="text-lg font-semibold text-gray-900 dark:text-gray-100">{{ pageTitle }}</h1>
      </header>
      <main class="flex-1 overflow-auto bg-gray-50 dark:bg-gray-900">
        <router-view v-slot="{ Component }">
          <Transition name="page" mode="out-in">
            <component :is="Component" />
          </Transition>
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { FeatherIcon } from 'frappe-ui'
import { useTheme } from '../composables/useTheme'

const route = useRoute()
const router = useRouter()

const { current, cycle } = useTheme()

const navItems = [
  { to: '/dashboard', icon: 'grid', label: 'Dashboard' },
  { to: '/projects', icon: 'folder', label: 'Projects' },
  { to: '/presets', icon: 'sliders', label: 'Presets' },
  { to: '/invoices', icon: 'file-text', label: 'Invoices' },
  { to: '/settings', icon: 'settings', label: 'Settings' },
  { to: '/plugins', icon: 'puzzle', label: 'Plugins' },
]

const pageTitle = computed(() => route.meta?.title || '')

const isActive = (path) => route.path === path || route.path.startsWith(path + '/')

const themeIcon = computed(() => {
  if (current.value === 'light') return 'sun'
  if (current.value === 'dark') return 'moon'
  return 'monitor'
})

const themeLabel = computed(() => {
  return current.value.charAt(0).toUpperCase() + current.value.slice(1)
})

async function logout() {
  try {
    await fetch('/api/method/logout', { method: 'POST' })
  } catch {}
  router.push('/')
}
</script>
