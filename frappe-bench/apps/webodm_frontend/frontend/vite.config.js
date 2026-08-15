import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  base: '/assets/webodm_frontend/frontend/',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  build: {
    outDir: '../webodm_frontend/public/frontend',
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      input: path.resolve(__dirname, 'index.html'),
    },
  },
  server: {
    port: 8081,
    allowedHosts: ['webodm.local','daas.raspigeek.me'],
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
        headers: { Host: 'webodm.local'},
      },
      '/private': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
        headers: { Host: 'webodm.local'},
      },
    },
  },
})
