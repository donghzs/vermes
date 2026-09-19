import { defineConfig, configDefaults } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'happy-dom',
    globals: true,
    setupFiles: ['./tests/setup.js'],
    // playwright/*.spec.ts 是浏览器级 e2e，归属 `npm run test:e2e`（playwright test）。
    // vitest 默认收集 **/*.{test,spec}.?(c|m)[jt]s?(x) 会把它一并扫入：实测移除本行后
    // `vitest list` 直接 exit=1 报 "Playwright Test did not expect test() to be called here"
    // （两个 test runner 撞车），污染单测「全绿」观感。故显式排除。
    exclude: [...configDefaults.exclude, '**/playwright/**'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.{js,vue}'],
      exclude: ['src/main.js', 'src/router/**'],
    },
  },
})
