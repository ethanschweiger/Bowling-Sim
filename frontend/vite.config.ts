/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Local-dev-only proxy: forwards relative /api/... requests (see
    // src/api/client.ts's default, empty VITE_API_BASE_URL) to the FastAPI
    // backend running on its documented default port (see root README's
    // "Run"). This is the "minimal local CORS/proxy configuration" the
    // frontend milestone allows — it needs no change to the backend at
    // all, since the browser only ever talks to the Vite dev server, which
    // makes the cross-origin request itself. Override the target with
    // VITE_BACKEND_ORIGIN if the backend runs somewhere else.
    proxy: {
      '/api': {
        target: process.env.VITE_BACKEND_ORIGIN ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'node',
  },
})
