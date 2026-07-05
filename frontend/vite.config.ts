import path from 'path'
import { defineConfig } from 'vite'
import react, { reactCompilerPreset } from '@vitejs/plugin-react'
import babel from '@rolldown/plugin-babel'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    babel({ presets: [reactCompilerPreset()] })
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    // Same-origin backend paths are forwarded to the backend. The default
    // target is the dev auth proxy (:8001), which injects the EasyAuth headers
    // and forwards to the API (:8000) — the hosted-database workflow. For the
    // zero-Azure local mode there is no auth proxy; point straight at the
    // backend instead:
    //   AUTOCRUD_PROXY_TARGET=http://localhost:8000 npm run dev
    // /me powers the signed-in user badge; /.auth is the session-refresh path.
    proxy: Object.fromEntries(
      ['/api', '/meta', '/admin', '/me', '/.auth'].map((route) => [
        route,
        process.env.AUTOCRUD_PROXY_TARGET ?? 'http://localhost:8001',
      ]),
    ),
  },
  build: {
    outDir: '../backend/app/frontend/dist',
    // The out-dir lives outside the frontend project root, so Vite won't clear
    // it by default. Enable it explicitly so stale assets from a previous build
    // never linger in the directory FastAPI serves.
    emptyOutDir: true,
  },
})
