<script setup>
import { toasts } from '../utils/toast'
</script>

<template>
  <Teleport to="body">
    <div class="fixed top-4 right-4 z-[9999] space-y-2 pointer-events-none">
      <TransitionGroup name="toast">
        <div v-for="toast in toasts" :key="toast.id"
          class="px-4 py-3 rounded-lg shadow-lg text-sm flex items-center gap-2 pointer-events-auto max-w-sm"
          :class="{
            'bg-green-500 text-white': toast.type === 'success',
            'bg-red-500 text-white': toast.type === 'error',
            'bg-amber-500 text-white': toast.type === 'warning',
            'bg-blue-500 text-white': toast.type === 'info'
          }">
          <span>{{ toast.message }}</span>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<style scoped>
.toast-enter-active {
  animation: toast-in 0.3s ease-out;
}
.toast-leave-active {
  animation: toast-out 0.25s ease-in;
}
@keyframes toast-in {
  from { transform: translateX(100%); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}
@keyframes toast-out {
  from { transform: translateX(0); opacity: 1; }
  to { transform: translateX(100%); opacity: 0; }
}
</style>
