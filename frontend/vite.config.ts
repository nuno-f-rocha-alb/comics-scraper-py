import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  // Prod build is served under /app by FastAPI (coexists with the legacy Jinja
  // pages at /); dev stays at / so the proxy + launch.json are unchanged.
  // Router reads this via import.meta.env.BASE_URL (see main.tsx).
  base: command === 'build' ? '/app/' : '/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    // SPA owns the page routes (/, /series, …); only data lives under /api.
    // 127.0.0.1 (not localhost) — Windows resolves localhost to IPv6 ::1 first,
    // which uvicorn isn't bound to, causing the proxy to 502.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
}))
