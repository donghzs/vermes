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
    // Build a dirty-aware hash: "b9facaf64a" (clean) or "b9facaf64a-dirty" (uncommitted).
    // This prevents the build from silently recording a stale parent hash
    // when `npm run build` runs before `git commit` (common workflow).
    const hash = execSync('git rev-parse --short=12 HEAD', {
      encoding: 'utf-8', cwd: fileURLToPath(new URL('.', import.meta.url))
    }).trim()
    const dirty = execSync('git status --porcelain', {
      encoding: 'utf-8', cwd: fileURLToPath(new URL('.', import.meta.url))
    }).trim()
    return dirty ? `${hash}-dirty` : hash
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
    // 部署产物直接输出到后端静态目录，一次 build = 一次部署同步。
    // 历史坑：此处若缺 outDir，产物会落到默认的 frontend/dist，只能靠手工改名拷进
    // web_dist（旧包还越堆越多），造成「源码与包不一致」——审计无法自动化。
    // main.py 的 _web_ui_build_needed 也是按 vermes_cli/web_dist 判定的，必须一致。
    // publicDir(frontend/public) 下的文件（guide.md 等）每次 build 自动复制到 outDir，
    // 因此 emptyOutDir 清理旧 hash 包不会误删非构建产物。
    outDir: '../vermes_cli/web_dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          'katex': ['katex', 'markdown-it-texmath'],
          'vendor': ['vue', 'vue-router', 'pinia'],
          'codemirror': [
            '@codemirror/state', '@codemirror/view', '@codemirror/commands',
            '@codemirror/language', '@codemirror/autocomplete', '@codemirror/search',
            '@codemirror/lang-markdown',
          ],
          'markdown': ['markdown-it', 'dompurify'],
          'highlight': [
            'highlight.js/lib/core',
            'highlight.js/lib/languages/javascript',
            'highlight.js/lib/languages/python',
            'highlight.js/lib/languages/bash',
            'highlight.js/lib/languages/json',
            'highlight.js/lib/languages/xml',
            'highlight.js/lib/languages/css',
            'highlight.js/lib/languages/markdown',
            'highlight.js/lib/languages/typescript',
            'highlight.js/lib/languages/go',
            'highlight.js/lib/languages/rust',
            'highlight.js/lib/languages/sql',
            'highlight.js/lib/languages/yaml',
          ],
          'three': ['three'],
          // 'office' chunk 不在 manualChunks 里，改为运行时动态 import
          // 'office': ['xlsx', 'mammoth/mammoth.browser.js'],
        }
      }
    },
    chunkSizeWarningLimit: 600,
  }
})
