/**
 * memoryFlags.js — Route E-Reflection flag 面板 Store
 *
 * 读取 open/resolved 状态的记忆 flag，并支持前端一键 resolve/restore。
 * resolve：demote 联动改 lifecycle_tag=ephemeral（P3-⑩）；merge/false_positive 只改 flag 状态。
 * restore：demote → flag→open + lifecycle_tag→reference；merge/false_positive → 只重开 flag。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '../services/api'
import { showToast } from '../utils/toast'

export const useMemoryFlagsStore = defineStore('memoryFlags', () => {
  const flags = ref([])
  const resolvedFlags = ref([])
  const loading = ref(false)
  const resolvedLoading = ref(false)

  async function fetchFlags() {
    loading.value = true
    try {
      const data = await api.getFlags()
      if (data && data.ok) {
        flags.value = data.flags || []
      } else {
        flags.value = []
      }
    } catch (e) {
      flags.value = []
    } finally {
      loading.value = false
    }
  }

  async function fetchResolved() {
    resolvedLoading.value = true
    try {
      const data = await api.getResolvedFlags()
      if (data && data.ok) {
        resolvedFlags.value = data.flags || []
      } else {
        resolvedFlags.value = []
      }
    } catch (e) {
      resolvedFlags.value = []
    } finally {
      resolvedLoading.value = false
    }
  }

  async function resolveFlag(flagId, resolution) {
    try {
      const data = await api.resolveFlag(flagId, resolution)
      if (data && data.ok) {
        // 从 open 列表移除 → 加入 resolved 列表
        const removed = flags.value.find(f => f.id === flagId)
        flags.value = flags.value.filter((f) => f.id !== flagId)
        if (removed) {
          removed.status = 'resolved'
          removed.resolution = resolution
          removed.resolved_at = new Date().toISOString()
          resolvedFlags.value.unshift(removed)
        }
        const labels = { demote: '已降级', merge: '已合并标记', false_positive: '已标记误报' }
        showToast(labels[resolution] || '已标记解决 ✓')
        return true
      }
      showToast(data?.error || '解决失败', 'error')
      return false
    } catch (e) {
      showToast('解决失败：' + (e.message || '未知错误'), 'error')
      return false
    }
  }

  async function restoreFlag(flagId) {
    try {
      const data = await api.restoreFlag(flagId)
      if (data && data.ok) {
        // 从 resolved 列表移除 → 加入 open 列表
        const restored = resolvedFlags.value.find(f => f.id === flagId)
        resolvedFlags.value = resolvedFlags.value.filter((f) => f.id !== flagId)
        if (restored) {
          restored.status = 'open'
          restored.resolution = null
          restored.resolved_at = null
          flags.value.unshift(restored)
        }
        showToast('已恢复 ✓')
        return true
      }
      showToast(data?.error || '恢复失败', 'error')
      return false
    } catch (e) {
      showToast('恢复失败：' + (e.message || '未知错误'), 'error')
      return false
    }
  }

  return { flags, resolvedFlags, loading, resolvedLoading, fetchFlags, fetchResolved, resolveFlag, restoreFlag }
})
