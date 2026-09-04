import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [
    vue(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    },
  },
  server: {
    host: '0.0.0.0',
    port: 4000,
    watch: {
      usePolling: true,
    },
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${process.env.MINER_BACKEND_PORT || 8000}`,
        changeOrigin: true,
      },
      '/change-matrix-outputs': {
        target: `http://127.0.0.1:${process.env.MINER_BACKEND_PORT || 8000}`,
        changeOrigin: true,
      },
      '/tiles': {
        target: `http://127.0.0.1:${process.env.MINER_BACKEND_PORT || 8000}`,
        changeOrigin: true,
      },
    },
  },
})
