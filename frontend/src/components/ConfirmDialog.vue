<template>
  <Transition name="confirm-fade">
    <div v-if="confirmState.visible" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm" @click.self="_resolve(false)">
      <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-sm w-full mx-4 overflow-hidden border border-gray-200 dark:border-gray-600"
        :class="{ 'border-red-400 dark:border-red-500 ring-1 ring-red-400/40': confirmState.danger }">
        <div class="px-5 py-4">
          <div class="flex items-start gap-3">
            <span class="text-xl shrink-0">{{ confirmState.danger ? '⚠️' : 'ℹ️' }}</span>
            <div class="flex-1 min-w-0">
              <h3 v-if="confirmState.title" class="text-sm font-semibold text-gray-900 dark:text-white mb-1">{{ confirmState.title }}</h3>
              <p class="text-sm text-gray-600 dark:text-gray-300 whitespace-pre-wrap">{{ confirmState.message }}</p>
            </div>
          </div>
        </div>
        <div class="px-5 py-3 border-t border-gray-200 dark:border-gray-700 flex gap-2 justify-end">
          <button @click="_resolve(false)"
            class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 transition">
            {{ confirmState.cancelText }}
          </button>
          <button @click="_resolve(true)"
            class="px-3 py-1.5 text-sm rounded-lg transition font-medium"
            :class="confirmState.danger
              ? 'bg-red-500 text-white hover:bg-red-600'
              : 'bg-green-500 text-white hover:bg-green-600'">
            {{ confirmState.confirmText }}
          </button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { confirmState, useConfirm } from '../composables/useConfirm'
const { _resolve } = useConfirm()
</script>

<style scoped>
.confirm-fade-enter-active,
.confirm-fade-leave-active {
  transition: opacity 0.15s ease;
}
.confirm-fade-enter-from,
.confirm-fade-leave-to {
  opacity: 0;
}
</style>
