<template>
  <Teleport to="body">
    <div v-if="activeModule" class="module-host-overlay">
      <div class="module-host-header">
        <button class="module-host-back" @click="closeModule">
          <span>← 返回</span>
        </button>
        <span class="module-host-title">{{ activeModule.display_name }}</span>
        <span class="module-host-version">v{{ activeModule.version }}</span>
      </div>
      <div ref="containerRef" class="module-host-container"></div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, onUnmounted, nextTick } from 'vue'

const activeModule = ref(null)
const containerRef = ref(null)
let currentUnmount = null

const moduleCache = {}  // name → { export, loaded }

async function open(mod) {
  // 如果切换到新模块，先卸载旧的
  if (currentUnmount) {
    currentUnmount()
    currentUnmount = null
  }

  activeModule.value = mod

  // 等待 DOM 更新
  await nextTick()
  if (!containerRef.value) return

  // 动态加载模块 entry.js（带缓存）
  let modExport
  if (moduleCache[mod.name]) {
    modExport = moduleCache[mod.name].export
  } else {
    try {
      const jsUrl = `/api/modules/${mod.name}/frontend/${mod.frontend_entry || 'entry.js'}`
      modExport = await import(/* @vite-ignore */ jsUrl)
      moduleCache[mod.name] = { export: modExport, loaded: Date.now() }
    } catch (e) {
      console.error(`[ModuleHost] Failed to load module ${mod.name}:`, e)
      activeModule.value = null
      // 通知父组件加载失败
      emit('error', { module: mod.name, error: e.message })
      return
    }
  }

  // 调用 mount 函数挂载到容器
  const container = containerRef.value
  container.innerHTML = ''  // 清空
  try {
    const unmountFn = modExport.mount?.(container) || modExport.default?.mount?.(container)
    currentUnmount = typeof unmountFn === 'function' ? unmountFn : null
  } catch (e) {
    console.error(`[ModuleHost] Failed to mount module ${mod.name}:`, e)
    activeModule.value = null
    emit('error', { module: mod.name, error: e.message })
  }
}

function closeModule() {
  if (currentUnmount) {
    try {
      currentUnmount()
    } catch (e) {
      console.warn('[ModuleHost] unmount error:', e)
    }
    currentUnmount = null
  }
  if (containerRef.value) {
    containerRef.value.innerHTML = ''
  }
  activeModule.value = null
}

const emit = defineEmits(['error'])

onUnmounted(() => {
  if (currentUnmount) {
    try { currentUnmount() } catch {}
    currentUnmount = null
  }
})

defineExpose({ open, close: closeModule })
</script>

<style scoped>
.module-host-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 9999;
  background: #ffffff;
  display: flex;
  flex-direction: column;
}
.module-host-overlay.dark {
  background: #0a0a0a;
}

.module-host-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  height: 44px;
  flex-shrink: 0;
  border-bottom: 1px solid #e5e7eb;
  background: #f9fafb;
}
:deep(.dark) .module-host-header {
  border-bottom-color: #374151;
  background: #111827;
}

.module-host-back {
  display: flex;
  align-items: center;
  padding: 4px 12px;
  border-radius: 8px;
  font-size: 14px;
  color: #374151;
  background: transparent;
  transition: background 0.15s;
}
.module-host-back:hover {
  background: #e5e7eb;
}
:deep(.dark) .module-host-back {
  color: #d1d5db;
}
:deep(.dark) .module-host-back:hover {
  background: #374151;
}

.module-host-title {
  font-size: 14px;
  font-weight: 600;
  color: #111827;
}
:deep(.dark) .module-host-title {
  color: #f3f4f6;
}

.module-host-version {
  font-size: 11px;
  color: #9ca3af;
}

.module-host-container {
  flex: 1;
  overflow: auto;
  position: relative;
}
</style>
