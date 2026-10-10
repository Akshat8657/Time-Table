import { createHash } from 'node:crypto'
import { defineConfig, loadEnv, type Plugin } from 'vite'
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

/**
 * Production builds get a Content-Security-Policy: only our own scripts may run (the one small inline theme script is
 * allowed by its exact hash), the page may only talk to our own API, and nothing may be embedded from elsewhere.
 * (frame-ancestors cannot be set from a <meta> tag - the web server adds it, see PRODUCTION_CHECKLIST.md.)
 */
function contentSecurityPolicy(apiBase: string): Plugin {
  return {
    name: 'timetablepro-csp',
    apply: 'build',
    transformIndexHtml(html) {
      const hashes = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(
        (m) => `'sha256-${createHash('sha256').update(m[1]).digest('base64')}'`,
      )
      let api = ''
      try {
        api = apiBase ? ` ${new URL(apiBase).origin}` : ''
      } catch {
        api = ''
      }
      const policy = [
        "default-src 'self'",
        `script-src 'self' ${hashes.join(' ')}`.trim(),
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        `connect-src 'self'${api}`,
        "worker-src 'self'",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
      ].join('; ')
      return html.replace('<meta charset="UTF-8" />', `<meta charset="UTF-8" />\n    <meta http-equiv="Content-Security-Policy" content="${policy}" />`)
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  return {
    plugins: [react(), tailwindcss(), contentSecurityPolicy(env.VITE_API_BASE_URL ?? '')],
    build: {
      rollupOptions: {
        output: {
          // React + the router change rarely: a separate file stays cached in the browser across our updates
          manualChunks(id: string) {
            return /node_modules[\/](react|react-dom|react-router|react-router-dom|scheduler|@remix-run)[\/]/.test(id) ? 'react' : undefined
          },
        },
      },
    },
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
  }
})
