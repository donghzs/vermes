/**
 * useConfirm — 轻量确认弹窗 composable
 * 替代 native confirm()，统一风格
 */
import { ref } from 'vue'

const _state = ref({
  visible: false,
  title: '',
  message: '',
  confirmText: '确定',
  cancelText: '取消',
  danger: false,
  resolve: null,
})

export function useConfirm() {
  function confirm(options) {
    if (typeof options === 'string') {
      options = { message: options }
    }
    return new Promise((resolve) => {
      _state.value = {
        visible: true,
        title: options.title || '请确认',
        message: options.message,
        confirmText: options.confirmText || '确定',
        cancelText: options.cancelText || '取消',
        danger: options.danger !== false,
        resolve,
      }
    })
  }

  function _resolve(ok) {
    const r = _state.value.resolve
    _state.value = { ..._state.value, visible: false, resolve: null }
    if (r) r(ok)
  }

  return { confirmState: _state, confirm, _resolve }
}

// 单例 state 供 ConfirmDialog 组件引用
export const confirmState = _state
