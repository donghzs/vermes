<template>
  <div v-if="chat.pendingApproval" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
    <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-lg w-full mx-4 overflow-hidden border border-gray-200 dark:border-gray-600" :class="{ 'border-red-400 dark:border-red-500 ring-1 ring-red-400/40': chat.pendingApproval.category === 'self_modify_rollback' }">
      <!-- Header -->
      <div class="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center gap-3">
        <span class="text-2xl">⚠️</span>
        <div>
          <h3 class="text-base font-semibold text-gray-900 dark:text-white">工具审批请求</h3>
          <p class="text-xs text-gray-500 dark:text-gray-400">Agent 想执行以下命令</p>
          <span v-if="chat.pendingApproval.category === 'self_modify_rollback'"
            class="ml-auto px-2 py-0.5 text-xs rounded-full bg-red-500/15 text-red-600 dark:text-red-400 font-medium">撤销确认</span>
        </div>
      </div>

      <!-- Body -->
      <div class="px-5 py-4 space-y-3">
        <div>
          <div class="text-xs text-gray-500 dark:text-gray-400 mb-1">命令</div>
          <pre class="text-sm font-mono bg-gray-100 dark:bg-gray-900 text-gray-800 dark:text-gray-200 p-3 rounded-lg overflow-x-auto whitespace-pre-wrap break-all">{{ chat.pendingApproval.command }}</pre>
        </div>
        <div v-if="chat.pendingApproval.description">
          <div class="text-xs text-gray-500 dark:text-gray-400 mb-1">说明</div>
          <div class="text-sm text-gray-700 dark:text-gray-300">{{ chat.pendingApproval.description }}</div>
        </div>
        <div v-if="chat.pendingApproval.diff">
          <div class="text-xs text-gray-500 dark:text-gray-400 mb-1">改动预览（diff）</div>
          <pre class="text-xs font-mono bg-gray-100 dark:bg-gray-900 text-gray-800 dark:text-gray-200 p-3 rounded-lg overflow-x-auto whitespace-pre-wrap break-all max-h-64 overflow-y-auto">{{ chat.pendingApproval.diff }}</pre>
        </div>
        <!-- G7: 底线理由——为什么问你 -->
        <div v-if="approvalReason" class="text-xs text-amber-600 dark:text-amber-400 bg-amber-500/5 rounded-lg px-3 py-2">
          💡 {{ approvalReason }}
        </div>
      </div>

      <!-- Actions -->
      <div class="px-5 py-4 border-t border-gray-200 dark:border-gray-700 space-y-2">
        <!-- 告诉用户批准不会被反复追问，否则「仅本次」会被误当成唯一安全选项 -->
        <p class="text-xs text-gray-500 dark:text-gray-400">
          {{ grantHint }}
        </p>
        <div class="flex gap-2 justify-end flex-wrap">
          <button @click="chat.resolveApproval('deny')"
            class="px-4 py-2 text-sm rounded-lg bg-red-500/10 text-red-600 dark:text-red-400 hover:bg-red-500/20 transition font-medium">
            拒绝
          </button>
          <button @click="chat.resolveApproval('once')"
            class="px-4 py-2 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 transition">
            仅本次
          </button>
          <button v-if="showAlways" @click="chat.resolveApproval('always')"
            class="px-4 py-2 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 transition">
            始终允许
          </button>
          <button @click="chat.resolveApproval('session')"
            class="px-4 py-2 text-sm rounded-lg bg-green-500/10 text-green-600 dark:text-green-400 hover:bg-green-500/20 transition font-medium"
            :class="{ 'ring-1 ring-green-500/50': recommended === 'session' }">
            本次会话允许
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useChatStore } from '../stores/chat'
import { personaApprovalReason } from '../utils/persona-copy'
const chat = useChatStore()

// G7: 为什么问你——底线理由
const approvalReason = computed(() => {
  const cat = chat.pendingApproval?.category || ''
  if (cat === 'self_modify_rollback') return '这是撤销我之前的改写，确认一下要不要回退'
  if (cat === 'source_modify') return personaApprovalReason('source_modify')
  if (cat === 'high_risk') return personaApprovalReason('high_risk')
  if (cat === 'skill_install') return personaApprovalReason('skill_install')
  if (cat === 'config_change') return personaApprovalReason('config_change')
  return ''
})

// 后端 approve_privileged_action 会带上这两个字段；命令审批不带，保持旧行为。
const scopeOptions = computed(() => chat.pendingApproval?.scope_options || [])
const showAlways = computed(() => scopeOptions.value.includes('always'))
const recommended = computed(() => chat.pendingApproval?.default_choice || '')

const grantHint = computed(() => {
  const ttl = chat.pendingApproval?.grant_ttl_minutes
  if (showAlways.value) {
    return ttl
      ? `「仅本次」批准后 ${ttl} 分钟内同类操作不再询问；「本次会话允许」到本次对话结束；「始终允许」写入配置，可在设置里撤销。`
      : '「本次会话允许」到本次对话结束；「始终允许」写入配置，可在设置里撤销。'
  }
  return '批准后同类操作在短时间内不会重复询问。'
})
</script>
