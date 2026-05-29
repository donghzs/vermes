import { ref } from 'vue'

export const toasts = ref([])
let toastId = 0

export function showToast(message, type = 'info', duration = 3000) {
  const id = ++toastId
  toasts.value.push({ id, message, type, duration })
  setTimeout(() => {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }, duration)
}

export const toast = {
  success: (msg, duration) => showToast(msg, 'success', duration),
  error:   (msg, duration) => showToast(msg, 'error',   duration),
  warning: (msg, duration) => showToast(msg, 'warning', duration),
  info:    (msg, duration) => showToast(msg, 'info',    duration),
}
