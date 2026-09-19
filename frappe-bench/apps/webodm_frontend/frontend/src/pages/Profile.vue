<script setup>
import { ref, onMounted } from 'vue'
import { Building2, Loader2, Mail, Save, Smartphone, User } from 'lucide-vue-next'
import { Button, Input, Label } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import { toast } from '@/lib/toast'
import { getCurrentUser, updateUser } from '@/lib/user'
import { getMyOrganization } from '@/lib/organization'

const loading = ref(true)
const saving = ref(false)
const user = ref(null)
const org = ref(null)
const original = ref({})

const form = ref({
  full_name: '',
  mobile_no: '',
})

async function load() {
  loading.value = true
  try {
    const [u, o] = await Promise.all([getCurrentUser(), getMyOrganization()])
    user.value = u
    org.value = o
    form.value.full_name = u.full_name || ''
    form.value.mobile_no = u.mobile_no || ''
    original.value = { ...form.value }
  } catch (e) {
    toast.error(e.message || 'Failed to load profile')
  } finally {
    loading.value = false
  }
}

function reset() {
  form.value = { ...original.value }
}

async function onSave() {
  saving.value = true
  try {
    await updateUser({
      full_name: form.value.full_name,
      mobile_no: form.value.mobile_no,
    })
    original.value = { ...form.value }
    toast.success('Profile updated')
  } catch (e) {
    toast.error(e.message || 'Failed to update profile')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="max-w-2xl space-y-6">
    <PageHeader
      title="Profile"
      description="Your personal information and organization membership."
    />

    <div v-if="loading" class="flex items-center justify-center py-12">
      <Loader2 class="size-6 animate-spin text-muted-foreground" />
    </div>

    <template v-else>
      <!-- Avatar placeholder -->
      <div class="flex items-center gap-4">
        <div
          class="flex size-16 items-center justify-center rounded-full bg-primary/10 text-primary"
        >
          <User class="size-8" />
        </div>
        <div>
          <p class="text-lg font-medium text-foreground">{{ user?.full_name || user?.name }}</p>
          <p class="text-sm text-muted-foreground">{{ user?.name }}</p>
        </div>
      </div>

      <!-- Organization info (read-only) -->
      <section class="space-y-4 rounded-lg border border-border bg-card p-6">
        <h2 class="font-medium text-card-foreground">Organization</h2>
        <div class="grid gap-4 sm:grid-cols-2">
          <div class="space-y-1.5">
            <Label class="flex items-center gap-1.5">
              <Building2 class="size-3.5" />
              Organization
            </Label>
            <Input :value="org?.organization || '—'" disabled />
          </div>
          <div class="space-y-1.5">
            <Label class="flex items-center gap-1.5">
              <Mail class="size-3.5" />
              Role
            </Label>
            <Input :value="org?.role || '—'" disabled />
          </div>
        </div>
      </section>

      <!-- Editable profile fields -->
      <section class="space-y-4 rounded-lg border border-border bg-card p-6">
        <h2 class="font-medium text-card-foreground">Personal Information</h2>
        <div class="grid gap-4 sm:grid-cols-2">
          <div class="space-y-1.5">
            <Label for="full-name">Full Name</Label>
            <Input id="full-name" v-model="form.full_name" placeholder="Your full name" />
          </div>
          <div class="space-y-1.5">
            <Label for="mobile" class="flex items-center gap-1.5">
              <Smartphone class="size-3.5" />
              Mobile Number
            </Label>
            <Input id="mobile" v-model="form.mobile_no" placeholder="+1 234 567 8900" />
          </div>
        </div>
      </section>

      <div class="flex justify-end gap-3">
        <Button variant="outline" :disabled="saving" @click="reset">
          Cancel
        </Button>
        <Button :loading="saving" @click="onSave">
          <Save class="size-4" />
          Save Changes
        </Button>
      </div>
    </template>
  </div>
</template>
