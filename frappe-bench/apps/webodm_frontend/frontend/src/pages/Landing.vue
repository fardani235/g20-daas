<template>
  <div class="min-h-screen bg-white dark:bg-gray-900">
    <!-- Navbar -->
    <nav class="fixed top-0 w-full bg-white/80 dark:bg-gray-900/80 backdrop-blur-md border-b dark:border-gray-800 z-50">
      <div class="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
        <router-link to="/" class="flex items-center gap-2">
          <FeatherIcon name="map" class="h-5 w-5 text-blue-600 dark:text-blue-400" />
          <span class="font-semibold text-gray-900 dark:text-gray-100">G20 Tech</span>
        </router-link>
        <div class="flex items-center gap-6">
          <a href="#features" class="text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 transition-colors">Features</a>
          <a href="#pricing" class="text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 transition-colors">Pricing</a>
          <router-link
            to="/login"
            class="text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded-lg transition-colors"
          >
            Sign In
          </router-link>
        </div>
      </div>
    </nav>

    <!-- Hero -->
    <section class="relative min-h-screen flex items-center px-6 overflow-hidden">
      <div class="absolute inset-0 bg-cover bg-center bg-no-repeat" style="background-image: url('/assets/webodm_frontend/frontend/images/background.png')"></div>
      <div class="absolute inset-0 bg-black/50 dark:bg-black/70"></div>
      <div class="relative max-w-4xl mx-auto text-center">
        <h1 class="text-5xl sm:text-7xl font-bold text-white leading-tight">
          Drone Mapping<br />Made Simple
        </h1>
        <p class="mt-6 text-xl sm:text-2xl text-gray-200 max-w-3xl mx-auto">
          Upload drone images and generate orthophotos, 3D models, and point clouds.
          Powered by OpenDroneMap with a modern, easy-to-use interface.
        </p>
        <div class="mt-10 flex items-center justify-center gap-4">
          <router-link
            to="/login"
            class="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white font-medium px-6 py-3 rounded-lg transition-colors"
          >
            Get Started
            <FeatherIcon name="arrow-right" class="h-4 w-4" />
          </router-link>
        </div>
      </div>
    </section>

    <!-- Features -->
    <section id="features" class="py-20 px-6 bg-gray-50 dark:bg-gray-800/50">
      <div class="max-w-6xl mx-auto">
        <h2 class="text-2xl font-bold text-center text-gray-900 dark:text-gray-100 mb-12">
          Everything you need
        </h2>
        <div class="grid sm:grid-cols-3 gap-8">
          <div
            v-for="feature in features"
            :key="feature.title"
            class="bg-white dark:bg-gray-900 rounded-xl p-6 border dark:border-gray-700"
          >
            <FeatherIcon :name="feature.icon" class="h-8 w-8 text-blue-600 dark:text-blue-400 mb-4" />
            <h3 class="font-semibold text-gray-900 dark:text-gray-100 mb-2">{{ feature.title }}</h3>
            <p class="text-sm text-gray-600 dark:text-gray-400">{{ feature.description }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- Pricing -->
    <section id="pricing" class="py-20 px-6">
      <div class="max-w-6xl mx-auto">
        <h2 class="text-2xl font-bold text-center text-gray-900 dark:text-gray-100 mb-4">
          Simple, transparent pricing
        </h2>
        <p class="text-center text-gray-600 dark:text-gray-400 mb-12 max-w-2xl mx-auto">
          Choose the plan that fits your mapping needs. All plans include core processing features.
        </p>
        <div class="grid sm:grid-cols-3 gap-8 max-w-5xl mx-auto">
          <div
            v-for="plan in plans"
            :key="plan.name"
            class="relative bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 p-8 flex flex-col"
            :class="plan.featured ? 'border-blue-600 dark:border-blue-400 ring-1 ring-blue-600 dark:ring-blue-400' : ''"
          >
            <span
              v-if="plan.featured"
              class="absolute -top-3 left-1/2 -translate-x-1/2 bg-blue-600 text-white text-xs font-medium px-3 py-0.5 rounded-full"
            >
              Most Popular
            </span>
            <h3 class="text-lg font-semibold text-gray-900 dark:text-gray-100">{{ plan.name }}</h3>
            <p class="mt-2 text-sm text-gray-600 dark:text-gray-400">{{ plan.description }}</p>
            <div class="mt-6 mb-8">
              <span class="text-3xl font-bold text-gray-900 dark:text-gray-100">{{ plan.price }}</span>
              <span class="text-sm text-gray-500 dark:text-gray-400">/{{ plan.period }}</span>
            </div>
            <ul class="space-y-3 mb-8 flex-1">
              <li
                v-for="feature in plan.features"
                :key="feature"
                class="flex items-start gap-2 text-sm text-gray-600 dark:text-gray-400"
              >
                <FeatherIcon name="check" class="h-4 w-4 text-blue-600 dark:text-blue-400 mt-0.5 flex-shrink-0" />
                {{ feature }}
              </li>
            </ul>
            <router-link
              to="/login"
              class="block text-center py-2.5 px-4 rounded-lg font-medium text-sm transition-colors"
              :class="plan.featured
                ? 'bg-blue-600 hover:bg-blue-700 text-white'
                : 'bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-900 dark:text-gray-100'"
            >
              Get Started
            </router-link>
          </div>
        </div>
      </div>
    </section>

    <!-- Footer -->
    <footer class="py-8 px-6 border-t dark:border-gray-800">
      <div class="max-w-6xl mx-auto text-center text-sm text-gray-500 dark:text-gray-400">
        &copy; {{ new Date().getFullYear() }} G20 Tech. All rights reserved.
      </div>
    </footer>
  </div>
</template>

<script setup>
import { FeatherIcon } from 'frappe-ui'

const features = [
  {
    icon: 'image',
    title: 'Orthophoto Maps',
    description: 'Generate georectified orthomosaics from overlapping drone imagery.',
  },
  {
    icon: 'box',
    title: '3D Models',
    description: 'Create textured 3D reconstructions viewable directly in your browser.',
  },
  {
    icon: 'layers',
    title: 'Point Clouds & DSM',
    description: 'Export dense point clouds, digital surface models, and terrain models.',
  },
]

const plans = [
  {
    name: 'Starter',
    description: 'For individual pilots and small projects.',
    price: 'Rp 1.700.000',
    period: 'month',
    featured: false,
    features: [
      '10 GB storage',
      '100 processing credits/month',
      '~2 Gigapixels capacity',
      'Orthophoto & DSM export',
      'Email support (5 business days)',
    ],
  },
  {
    name: 'Standard',
    description: 'For teams and regular mapping operations.',
    price: 'Rp 8.500.000',
    period: 'month',
    featured: true,
    features: [
      '100 GB storage',
      '600 processing credits/month',
      '~12 Gigapixels capacity',
      'Change detection analytics',
      'Map sharing & collaboration',
      'Email support (2 business days)',
    ],
  },
  {
    name: 'Advanced',
    description: 'For enterprises with large-scale needs.',
    price: 'Rp 34.000.000',
    period: 'month',
    featured: false,
    features: [
      '600 GB storage',
      '3,000 processing credits/month',
      'API access & integrations',
      'User access control',
      'On-premise deployment',
      'Dedicated GIS expert support',
      'SLA guarantee',
    ],
  },
]
</script>
