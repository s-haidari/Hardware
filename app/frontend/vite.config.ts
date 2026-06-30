import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The FastAPI backend (hwkit.api.app) runs on 8799 in dev.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8799',
    },
  },
})
