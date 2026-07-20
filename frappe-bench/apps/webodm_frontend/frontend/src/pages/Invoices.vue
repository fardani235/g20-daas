<template>
  <div class="p-6 space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100">Billing History</h2>
    </div>

    <div class="bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b dark:border-gray-700 text-left text-gray-500 dark:text-gray-400">
            <th class="px-4 py-3 font-medium">Invoice</th>
            <th class="px-4 py-3 font-medium">Date</th>
            <th class="px-4 py-3 font-medium">Amount</th>
            <th class="px-4 py-3 font-medium">Status</th>
            <th class="px-4 py-3 font-medium text-right">Download</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="invoice in invoices"
            :key="invoice.id"
            class="border-b dark:border-gray-700 last:border-0 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
          >
            <td class="px-4 py-3 font-medium text-gray-900 dark:text-gray-100">{{ invoice.id }}</td>
            <td class="px-4 py-3 text-gray-600 dark:text-gray-400">{{ invoice.date }}</td>
            <td class="px-4 py-3 text-gray-900 dark:text-gray-100">{{ invoice.amount }}</td>
            <td class="px-4 py-3">
              <span
                class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
                :class="statusClass(invoice.status)"
              >
                {{ invoice.status }}
              </span>
            </td>
            <td class="px-4 py-3 text-right">
              <button class="text-blue-600 dark:text-blue-400 hover:underline text-sm">
                <FeatherIcon name="download" class="h-4 w-4 inline" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { FeatherIcon } from 'frappe-ui'

const invoices = ref([
  { id: 'INV-001', date: '2026-07-01', amount: '$49.00', status: 'Paid' },
  { id: 'INV-002', date: '2026-06-01', amount: '$49.00', status: 'Paid' },
  { id: 'INV-003', date: '2026-05-01', amount: '$49.00', status: 'Paid' },
  { id: 'INV-004', date: '2026-08-01', amount: '$49.00', status: 'Overdue' },
])

function statusClass(status) {
  if (status === 'Paid') return 'bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-300'
  if (status === 'Overdue') return 'bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-300'
  if (status === 'Draft') return 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'
  return 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/50 dark:text-yellow-300'
}
</script>
