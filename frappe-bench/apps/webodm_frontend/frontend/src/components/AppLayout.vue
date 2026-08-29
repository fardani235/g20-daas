<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  CreditCard,
  Lock,
  LogOut,
  Map as MapIcon,
  Menu,
  Monitor,
  Moon,
  Settings,
  Sun,
  User,
} from 'lucide-vue-next'
import {
  Button,
  DropdownMenu,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/components/ui'
import {
  PRIMARY_TABS,
  activePrimaryTab,
  secondaryTabs,
  activeSecondaryTab,
} from '@/lib/nav'
import { useTheme } from '@/composables/useTheme'

const route = useRoute()
const router = useRouter()
const { current, cycle } = useTheme()

// All nav state is derived from the path — never stored — so deep links and
// refreshes highlight the right tab.
const activeTab = computed(() => activePrimaryTab(route.path))
const subTabs = computed(() => secondaryTabs(route.path))
const activeSubTab = computed(() => activeSecondaryTab(route.path))
const activeTabLabel = computed(
  () => PRIMARY_TABS.find(t => t.to === activeTab.value)?.label ?? 'Menu',
)

// MapView fills the viewport and scrolls internally; every other page gets the
// standard padded, scrollable shell.
const fullBleed = computed(() => route.meta?.fullBleed === true)

const themeIcon = computed(() => {
  if (current.value === 'light') return Sun
  if (current.value === 'dark') return Moon
  return Monitor
})
const themeLabel = computed(
  () => current.value.charAt(0).toUpperCase() + current.value.slice(1),
)

async function logout() {
  try {
    await fetch('/api/method/logout', { method: 'POST' })
  } catch {}
  router.push('/')
}
</script>

<template>
  <div class="flex h-screen flex-col bg-background">
    <!-- Primary bar -->
    <header class="flex-shrink-0 border-b border-border bg-card">
      <div class="flex h-14 items-center gap-4 px-4 sm:px-6">
        <router-link to="/dashboard" class="flex flex-shrink-0 items-center gap-2">
          <MapIcon class="size-5 text-primary" />
          <span class="font-semibold tracking-tight text-foreground">G20 Tech</span>
        </router-link>

        <!-- Tabs (md and up) -->
        <nav class="hidden min-w-0 flex-1 items-center gap-1 md:flex" aria-label="Main">
          <router-link
            v-for="tab in PRIMARY_TABS"
            :key="tab.to"
            :to="tab.to"
            :aria-current="activeTab === tab.to ? 'page' : undefined"
            class="relative flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            :class="
              activeTab === tab.to
                ? 'text-primary after:absolute after:inset-x-2 after:-bottom-[13px] after:h-0.5 after:rounded-full after:bg-primary'
                : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
            "
          >
            <component :is="tab.icon" class="size-4" />
            {{ tab.label }}
          </router-link>
        </nav>

        <!-- Collapsed tabs (below md) -->
        <div class="flex min-w-0 flex-1 md:hidden">
          <DropdownMenu align="start">
            <template #trigger>
              <Button variant="ghost" size="sm">
                <Menu />
                {{ activeTabLabel }}
              </Button>
            </template>
            <DropdownMenuItem
              v-for="tab in PRIMARY_TABS"
              :key="tab.to"
              @select="router.push(tab.to)"
            >
              <component :is="tab.icon" />
              {{ tab.label }}
            </DropdownMenuItem>
          </DropdownMenu>
        </div>

        <!-- Right side -->
        <div class="flex flex-shrink-0 items-center gap-1">
          <Button variant="ghost" size="icon" :title="`Theme: ${themeLabel}`" @click="cycle">
            <component :is="themeIcon" />
            <span class="sr-only">Toggle theme (currently {{ themeLabel }})</span>
          </Button>
          <DropdownMenu>
            <template #trigger>
              <Button variant="ghost" size="sm">Account</Button>
            </template>
            <DropdownMenuItem @select="router.push('/account/profile')">
              <User class="size-4 mr-2" />
              Profile
            </DropdownMenuItem>
            <DropdownMenuItem @select="router.push('/invoices')">
              <CreditCard class="size-4 mr-2" />
              Billing
            </DropdownMenuItem>
            <DropdownMenuItem @select="router.push('/settings')">
              <Settings class="size-4 mr-2" />
              Settings
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem @select="router.push('/account/password')">
              <Lock class="size-4 mr-2" />
              Change Password
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem @select="logout">
              <LogOut class="size-4 mr-2" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenu>
        </div>
      </div>

      <!-- Secondary bar: project-detail routes only -->
      <nav
        v-if="subTabs.length"
        class="flex items-center gap-1 border-t border-border px-4 sm:px-6"
        aria-label="Project sections"
      >
        <router-link
          v-for="tab in subTabs"
          :key="tab.to"
          :to="tab.to"
          :aria-current="activeSubTab === tab.to ? 'page' : undefined"
          class="-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :class="
            activeSubTab === tab.to
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          "
        >
          {{ tab.label }}
        </router-link>
      </nav>
    </header>

    <main
      class="flex-1"
      :class="fullBleed ? 'overflow-hidden' : 'overflow-auto p-4 sm:p-6'"
    >
      <router-view v-slot="{ Component }">
        <Transition name="page" mode="out-in">
          <component :is="Component" />
        </Transition>
      </router-view>
    </main>
  </div>
</template>
