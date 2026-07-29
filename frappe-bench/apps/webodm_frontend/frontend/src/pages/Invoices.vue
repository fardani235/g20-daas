<script setup>
import { ref } from 'vue'
import { Download } from 'lucide-vue-next'
import { Badge, Button } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'

const invoices = ref([
  { id: 'INV-001', date: '2026-07-01', amount: '$49.00', status: 'Paid' },
  { id: 'INV-002', date: '2026-06-01', amount: '$49.00', status: 'Paid' },
  { id: 'INV-003', date: '2026-05-01', amount: '$49.00', status: 'Paid' },
  { id: 'INV-004', date: '2026-08-01', amount: '$49.00', status: 'Overdue' },
])

// Invoice statuses are their own vocabulary, separate from task/project status.
const INVOICE_VARIANTS = {
  Paid: 'success',
  Overdue: 'destructive',
  Draft: 'secondary',
}
const invoiceVariant = (status) => INVOICE_VARIANTS[status] || 'warning'
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Billing history" description="Invoices issued to your organization." />

    <div class="overflow-hidden rounded-lg border border-border bg-card">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-muted-foreground">
            <th class="px-4 py-3 font-medium">Invoice</th>
            <th class="px-4 py-3 font-medium">Date</th>
            <th class="px-4 py-3 font-medium">Amount</th>
            <th class="px-4 py-3 font-medium">Status</th>
            <th class="px-4 py-3 text-right font-medium">Download</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="invoice in invoices"
            :key="invoice.id"
            class="border-b border-border transition-colors last:border-0 hover:bg-accent"
          >
            <td class="px-4 py-3 font-medium text-card-foreground">{{ invoice.id }}</td>
            <td class="px-4 py-3 text-muted-foreground">{{ invoice.date }}</td>
            <td class="px-4 py-3 text-card-foreground">{{ invoice.amount }}</td>
            <td class="px-4 py-3">
              <Badge :variant="invoiceVariant(invoice.status)">{{ invoice.status }}</Badge>
            </td>
            <td class="px-4 py-3 text-right">
              <Button variant="ghost" size="icon" :title="`Download ${invoice.id}`">
                <Download />
                <span class="sr-only">Download {{ invoice.id }}</span>
              </Button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
