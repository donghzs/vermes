import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import { readFileSync } from 'node:fs'

// Read version from Python __init__.py — single source of truth
const initPy = readFileSync(new URL('../hermes_cli/__init__.py', import.meta.url), 'utf-8')
const versionMatch = initPy.match(/__version__\s*=\s*['"]([^'"]+)['"]/)
const appVersion = versionMatch ? versionMatch[1] : '0.0.0'

export default defineConfig({
  plugins: [vue()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:9120',
        changeOrigin: true,
      }
    }
  }
})
