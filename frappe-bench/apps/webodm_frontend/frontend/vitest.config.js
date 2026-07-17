import { defineConfig } from 'vitest/config'
import path from 'path'

// Standalone from vite.config.js so tests don't load the frappe-ui/icons
// build plugins. jsdom is required because src/lib/mapLayers.js imports
// Leaflet, which touches window/document at construction time.
export default defineConfig({
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
