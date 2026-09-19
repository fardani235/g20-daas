<script setup>
import { ref } from 'vue'
import { KeyRound, Loader2 } from 'lucide-vue-next'
import { Button, Input, Label } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import { toast } from '@/lib/toast'
import { changePassword } from '@/lib/user'

const loading = ref(false)
const form = ref({
  old_password: '',
  new_password: '',
  confirm_password: '',
})

async function onSubmit() {
  if (!form.value.old_password || !form.value.new_password || !form.value.confirm_password) {
    toast.error('All fields are required')
    return
  }
  if (form.value.new_password !== form.value.confirm_password) {
    toast.error('New password and confirmation do not match')
    return
  }
  if (form.value.new_password.length < 8) {
    toast.error('New password must be at least 8 characters')
    return
  }

  loading.value = true
  try {
    await changePassword(form.value.old_password, form.value.new_password)
    toast.success('Password updated successfully')
    form.value = { old_password: '', new_password: '', confirm_password: '' }
  } catch (e) {
    toast.error(e.message || 'Failed to update password')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="max-w-md space-y-6">
    <PageHeader
      title="Change Password"
      description="Update your account password."
    />

    <section class="space-y-4 rounded-lg border border-border bg-card p-6">
      <div class="space-y-1.5">
        <Label for="old-password">Current Password</Label>
        <Input
          id="old-password"
          v-model="form.old_password"
          type="password"
          placeholder="Enter current password"
        />
      </div>

      <div class="space-y-1.5">
        <Label for="new-password">New Password</Label>
        <Input
          id="new-password"
          v-model="form.new_password"
          type="password"
          placeholder="Enter new password"
        />
        <p class="text-xs text-muted-foreground">Must be at least 8 characters.</p>
      </div>

      <div class="space-y-1.5">
        <Label for="confirm-password">Confirm New Password</Label>
        <Input
          id="confirm-password"
          v-model="form.confirm_password"
          type="password"
          placeholder="Re-enter new password"
        />
      </div>

      <Button class="w-full" :loading="loading" @click="onSubmit">
        <KeyRound class="size-4" />
        Update Password
      </Button>
    </section>
  </div>
</template>
