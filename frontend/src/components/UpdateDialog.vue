<template>
  <Transition name="update-fade">
    <div v-if="visible" class="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 backdrop-blur-sm" @click.self="later">
      <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-md w-full mx-4 overflow-hidden border border-gray-200 dark:border-gray-600">
        <!-- 头部 -->
        <div class="px-5 pt-5 pb-3 flex items-start gap-3">
          <span class="text-2xl shrink-0">🚀</span>
          <div class="flex-1 min-w-0">
            <h3 class="text-base font-semibold text-gray-900 dark:text-white">
              发现新版本 v{{ update.latestVersion }}
            </h3>
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              当前 v{{ update.currentVersion }} · 建议更新以获得最新功能与修复
            </p>
          </div>
        </div>

        <!-- 更新日志 -->
        <div v-if="update.releaseNotes" class="px-5 pb-3">
          <div class="max-h-40 overflow-y-auto text-sm text-gray-600 dark:text-gray-300 bg-gray-50 dark:bg-gray-900/60 rounded-lg p-3 whitespace-pre-wrap leading-relaxed">
            {{ update.releaseNotes }}
          </div>
        </div>

        <!-- 进度（下载中 / 应用 / 即将重启） -->
        <div v-if="update.updating" class="px-5 pb-3">
          <div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2 overflow-hidden">
            <div
              class="h-full bg-green-500 transition-all duration-200"
              :style="{ width: Math.max(3, update.updateProgress || 0) + '%' }"
            ></div>
          </div>
          <p class="text-xs text-gray-500 dark:text-gray-400 mt-2 text-center">
            {{ update.updateMessage || '处理中…' }}
          </p>
        </div>

        <!-- 底部按钮 -->
        <div class="px-5 py-3 border-t border-gray-200 dark:border-gray-700 flex gap-2 justify-end">
          <!-- 非下载态：主操作 = 下载并安装（Electron/Web 分支内部自行处理下载+应用） -->
          <button
            v-if="!update.updating && !justDownloaded"
            @click="startUpdate"
            class="px-3 py-1.5 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 transition font-medium">
            下载并安装
          </button>
          <!-- Electron：下载完成 → 显式「安装并重启」 -->
          <button
            v-if="!update.updating && justDownloaded && isDesktop"
            @click="installNow"
            class="px-3 py-1.5 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 transition font-medium">
            安装并重启
          </button>

          <button
            v-if="!update.updating"
            @click="ignore"
            class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 transition">
            忽略此版本
          </button>
          <button
            v-if="!update.updating"
            @click="later"
            class="px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700 transition">
            稍后
          </button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useUpdateStore } from '../stores/update'

const update = useUpdateStore()
const isDesktop = typeof window !== 'undefined' && !!window.vermes?.isDesktop

// 本会话内「稍后」隐藏标记（不写持久化，下次启动仍会再提示）
const sessionDismissed = ref(false)

// Electron：下载完成（status=done）后需用户点「安装并重启」才 quitAndInstall
const justDownloaded = computed(() => update.updateStatus === 'done')

const visible = computed(() => update.hasUpdate && !sessionDismissed.value)

function startUpdate() {
  update.startUpdate()
}
function installNow() {
  update.installUpdate()
}
function later() {
  sessionDismissed.value = true
}
function ignore() {
  update.dismissUpdate()
  sessionDismissed.value = true
}
</script>

<style scoped>
.update-fade-enter-active,
.update-fade-leave-active {
  transition: opacity 0.15s ease;
}
.update-fade-enter-from,
.update-fade-leave-to {
  opacity: 0;
}
</style>
