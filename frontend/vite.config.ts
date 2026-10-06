import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The browser only ever talks to http://localhost:5173.
// Every call that starts with /api is forwarded to the Python backend (port 8000),
// so the browser sees ONE origin: no CORS problems, and login cookies stay first-party.
const BACKEND = 'http://127.0.0.1:8000'

const securityHeaders = {
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'same-origin',
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    headers: securityHeaders,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: false, xfwd: true },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    headers: securityHeaders,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: false, xfwd: true },
    },
  },
})
