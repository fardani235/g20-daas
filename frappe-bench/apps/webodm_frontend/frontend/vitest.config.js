import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import path from 'path'

// Standalone from vite.config.js so tests skip the app's build-time config
// (base path, output dir, dev proxy). jsdom is required because
// src/lib/mapLayers.js imports Leaflet, which touches window/document at
// construction time. The Vue plugin compiles .vue single-file components
// imported by component tests.
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.js'],
  },
})
