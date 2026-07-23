/**
 * memoryFlags.js — Route E-Reflection flag 面板 Store
 *
 * 读取 open 状态的记忆 flag，并支持前端一键 resolve
 * （resolve 仅改 memory_flags 状态列，不触碰原 memories，符合铁律）。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '../services/api'
import { showToast } from '../utils/toast'

export const useMemoryFlagsStore = defineStore('memoryFlags', () => {
  const flags = ref([])
  const loading = ref(false)

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
      // fail-open：拉取失败不阻塞主会话
      flags.value = []
    } finally {
      loading.value = false
    }
  }

  async function resolveFlag(flagId, resolution) {
    try {
      const data = await api.resolveFlag(flagId, resolution)
      if (data && data.ok) {
        flags.value = flags.value.filter((f) => f.id !== flagId)
        showToast('已标记解决 ✓')
        return true
      }
      showToast(data?.error || '解决失败', 'error')
      return false
    } catch (e) {
      showToast('解决失败：' + (e.message || '未知错误'), 'error')
      return false
    }
  }

  return { flags, loading, fetchFlags, resolveFlag }
})
