<script setup>
import { ref } from 'vue'
import { MoreVertical, Plus } from 'lucide-vue-next'
import { Badge, Button } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'

const plugins = ref([
  { name: 'Change Detection', version: '1.0.0', enabled: true },
  { name: 'Object Detection', version: '1.0.0', enabled: true },
  { name: 'Plantation Health', version: '1.0.0', enabled: false },
  { name: 'Tree Counting', version: '1.0.0', enabled: false },
  { name: 'AI Analytics', version: '1.0.0', enabled: false },
])
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Plugins" description="Extend processing with optional analysis modules.">
      <template #actions>
        <Button>
          <Plus />
          Install plugin
        </Button>
      </template>
    </PageHeader>

    <div class="overflow-hidden rounded-lg border border-border bg-card">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-muted-foreground">
            <th class="px-4 py-3 font-medium">Name</th>
            <th class="px-4 py-3 font-medium">Version</th>
            <th class="px-4 py-3 font-medium">Status</th>
            <th class="px-4 py-3 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="plugin in plugins"
            :key="plugin.name"
            class="border-b border-border transition-colors last:border-0 hover:bg-accent"
          >
            <td class="px-4 py-3 font-medium text-card-foreground">{{ plugin.name }}</td>
            <td class="px-4 py-3 text-muted-foreground">{{ plugin.version }}</td>
            <td class="px-4 py-3">
              <Badge :variant="plugin.enabled ? 'success' : 'secondary'">
                {{ plugin.enabled ? 'Enabled' : 'Disabled' }}
              </Badge>
            </td>
            <td class="px-4 py-3 text-right">
              <Button variant="ghost" size="icon" :title="`Options for ${plugin.name}`">
                <MoreVertical />
                <span class="sr-only">Options for {{ plugin.name }}</span>
              </Button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
