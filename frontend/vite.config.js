import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import { execSync } from 'node:child_process'
import { writeFileSync, mkdirSync, readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'

// 读取项目根版本号（优先 package.json，回退 git tag / version.txt）
function readVersion() {
  try {
    const pkg = JSON.parse(readFileSync(join(fileURLToPath(new URL('.', import.meta.url)), 'package.json'), 'utf-8'))
    if (pkg.version) return pkg.version
  } catch {}
  try {
    return execSync('git describe --tags --abbrev=0 2>/dev/null || cat ../version.txt', {
      encoding: 'utf-8', cwd: fileURLToPath(new URL('.', import.meta.url))
    }).trim()
  } catch {
    return '0.0.0'
  }
}

function readGitHash() {
  try {
    return execSync('git rev-parse --short HEAD', {
      encoding: 'utf-8', cwd: fileURLToPath(new URL('.', import.meta.url))
    }).trim()
  } catch {
    return 'unknown'
  }
}

function readGitBranch() {
  try {
    return execSync('git rev-parse --abbrev-ref HEAD', {
      encoding: 'utf-8', cwd: fileURLToPath(new URL('.', import.meta.url))
    }).trim()
  } catch {
    return 'unknown'
  }
}

/** Vite 插件：生成 frontend-build.json */
function buildInfoPlugin() {
  let outDir = 'dist'
  return {
    name: 'build-info',
    configResolved(config) {
      outDir = config.build.outDir
    },
    writeBundle() {
      const buildInfo = {
        version: readVersion(),
        gitHash: readGitHash(),
        gitBranch: readGitBranch(),
        buildTime: new Date().toISOString(),
        nodeVersion: process.version,
      }
      const distDir = fileURLToPath(new URL(`./${outDir}`, import.meta.url))
      mkdirSync(distDir, { recursive: true })
      writeFileSync(join(distDir, 'frontend-build.json'), JSON.stringify(buildInfo, null, 2), 'utf-8')
      console.log(`[build-info] ✅ frontend-build.json → ${join(outDir, 'frontend-build.json')}`)
    }
  }
}

export default defineConfig({
  plugins: [vue(), buildInfoPlugin()],
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
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          'katex': ['katex', 'markdown-it-texmath'],
          'vendor': ['vue', 'vue-router'],
          'codemirror': [
            '@codemirror/state', '@codemirror/view', '@codemirror/commands',
            '@codemirror/language', '@codemirror/autocomplete', '@codemirror/search',
            '@codemirror/lang-markdown',
          ],
        }
      }
    }
  }
})
