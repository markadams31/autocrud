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
    // Same-origin backend paths are forwarded to the dev auth proxy (:8001),
    // which injects the EasyAuth headers and forwards to the API (:8000).
    // /me powers the signed-in user badge; /.auth is the session-refresh path.
    proxy: {
      '/api':   'http://localhost:8001',
      '/meta':  'http://localhost:8001',
      '/admin': 'http://localhost:8001',
      '/me':    'http://localhost:8001',
      '/.auth': 'http://localhost:8001',
    },
  },
  build: {
    outDir: '../backend/app/frontend/dist',
    // The out-dir lives outside the frontend project root, so Vite won't clear
    // it by default. Enable it explicitly so stale assets from a previous build
    // never linger in the directory FastAPI serves.
    emptyOutDir: true,
  },
})
