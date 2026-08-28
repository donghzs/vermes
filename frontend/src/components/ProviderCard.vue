<script setup>
/**
 * ProviderCard.vue — 可折叠的 Provider 配置面板
 *
 * 通用组件，所有 Provider（vbit/deepseek/xiaomi/ollama/...）共用。
 * 推荐区用大图标+描述，高级区用紧凑行。
 */
import { ref } from 'vue'
import { toast } from '../utils/toast'

const props = defineProps({
  provider: { type: Object, required: true },
  expanded: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },       // 紧凑模式（高级区）
  iconClass: { type: String, default: '' },          // 图标样式类
  iconText: { type: String, default: '' },           // 图标文字
  description: { type: String, default: '' },        // 描述文字
  linkUrl: { type: String, default: '' },            // 获取 Key 链接
  linkText: { type: String, default: '' },           // 链接文字
  showDelete: { type: Boolean, default: false },     // 显示清除按钮
  hideKeyInput: { type: Boolean, default: false },   // 隐藏 Key 输入
  hideBaseUrl: { type: Boolean, default: false },    // 隐藏 Base URL
  defaultBaseUrl: { type: String, default: '' },
  // 能力徽标（P0-4）：[{ label, cls, title }]，由 Settings.vue 的 capBadges() 生成。
  // 不传 = 不渲染，任何未接线的调用方行为完全不变。
  badges: { type: Array, default: () => [] },
})

const emit = defineEmits(['sync', 'save', 'delete', 'set-model', 'add-model', 'remove-model', 'toggle', 'test'])

const customModelInput = ref('')

function onAddModel() {
  const modelId = customModelInput.value.trim()
  if (!modelId) return
  emit('add-model', props.provider, modelId)
  customModelInput.value = ''
}
</script>

<template>
  <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
    <!-- Header -->
    <button @click="emit('toggle', provider.id)" class="w-full px-4 py-3 flex items-center justify-between hover:bg-gray-50 dark:hover:bg-gray-750 transition">
      <div class="flex items-center gap-3">
        <div :class="['rounded-lg flex items-center justify-center text-sm font-bold', compact ? 'w-8 h-8' : 'w-10 h-10', iconClass || (provider.key ? 'bg-green-100 dark:bg-green-900 text-green-600 dark:text-green-400' : 'bg-gray-100 dark:bg-gray-700 text-gray-400')]">
          {{ iconText || provider.name.charAt(0) }}
        </div>
        <div class="text-left">
          <div :class="['font-medium text-gray-800 dark:text-gray-200', compact ? 'text-sm' : '']">{{ provider.name }}</div>
          <div v-if="compact" class="text-xs text-gray-400">{{ provider.models?.length || 0 }} 个模型 · {{ provider.key ? '已配置' : '未配置' }}</div>
          <div v-else-if="description" class="text-xs text-gray-500 dark:text-gray-400">{{ description }}</div>
          <!-- 能力徽标（P0-4）：支持的高亮、不支持的灰显划掉，配之前就能看出这家能干什么 -->
          <div v-if="badges.length" class="flex flex-wrap gap-1 mt-1">
            <span v-for="b in badges" :key="b.label" :title="b.title || ''"
              :class="['text-[10px] px-1.5 py-0.5 rounded whitespace-nowrap', b.cls]">{{ b.label }}</span>
          </div>
        </div>
      </div>
      <svg class="w-4 h-4 text-gray-400 transition-transform" :class="expanded ? 'rotate-180' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
    </button>

    <!-- Body -->
    <div v-if="expanded" class="px-4 pb-4 space-y-3 border-t border-gray-100 dark:border-gray-700">
      <!-- API Key -->
      <div v-if="!hideKeyInput" class="pt-3">
        <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">API Key</label>
        <input v-model="provider.key" type="password" placeholder="sk-..." class="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500" />
        <a v-if="linkUrl" :href="linkUrl" target="_blank" class="inline-block mt-1 text-xs text-blue-500 hover:text-blue-600">{{ linkText || '→ 获取 Key ↗' }}</a>
      </div>
      <!-- Base URL -->
      <div v-if="!hideBaseUrl">
        <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Base URL</label>
        <input v-model="provider.baseUrl" type="text" :placeholder="defaultBaseUrl || 'https://...'" class="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500" />
      </div>
      <!-- Actions -->
      <div class="flex gap-2">
        <button @click="emit('sync', provider)" :disabled="provider.syncing" class="px-4 py-1.5 bg-green-500 hover:bg-green-600 disabled:bg-green-300 text-white rounded-lg text-xs font-medium transition flex items-center gap-1">
          <svg v-if="provider.syncing" class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg>
          {{ provider.syncing ? '同步中...' : '🔄 同步模型' }}
        </button>
        <button @click="emit('test', provider)" :disabled="provider.testing" class="px-4 py-1.5 bg-blue-500 hover:bg-blue-600 disabled:bg-blue-300 text-white rounded-lg text-xs font-medium transition flex items-center gap-1">
          <svg v-if="provider.testing" class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg>
          {{ provider.testing ? '测试中...' : '🔌 测试连接' }}
        </button>
        <button @click="emit('save')" class="px-4 py-1.5 bg-gray-200 dark:bg-gray-600 hover:bg-gray-300 dark:hover:bg-gray-500 text-gray-700 dark:text-gray-200 rounded-lg text-xs font-medium transition">💾 保存</button>
        <button v-if="showDelete" @click="emit('delete', provider)" class="px-4 py-1.5 bg-red-50 dark:bg-red-900/30 hover:bg-red-100 dark:hover:bg-red-900/50 text-red-500 rounded-lg text-xs font-medium transition">🗑 清除</button>
      </div>
      <div v-if="provider.testResult" class="text-xs" :class="provider.testResult.ok ? 'text-green-600 dark:text-green-400' : 'text-red-500'">
        {{ provider.testResult.ok ? provider.testResult.message : '❌ ' + provider.testResult.error }}
      </div>
      <!-- Model list -->
      <div v-if="provider.models && provider.models.length > 0" class="space-y-1">
        <div class="text-xs text-gray-500 dark:text-gray-400 font-medium">可用模型</div>
        <div v-for="m in provider.models" :key="m" class="flex items-center justify-between px-3 py-1.5 bg-gray-50 dark:bg-gray-700 rounded-lg">
          <span class="text-sm text-gray-700 dark:text-gray-300">{{ m }}</span>
          <div class="flex items-center gap-2">
            <button @click="emit('set-model', provider, m)" class="text-xs px-2 py-1 bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-400 rounded hover:bg-green-200 dark:hover:bg-green-800/60 transition font-medium">✓ 设为当前</button>
            <button v-if="showDelete" @click="emit('remove-model', provider, m)" class="text-gray-400 hover:text-red-500 text-xs">✕</button>
          </div>
        </div>
      </div>
      <!-- Custom model input -->
      <div class="flex gap-2">
        <input v-model="customModelInput" @keyup.enter="onAddModel" placeholder="手动输入模型名..." class="flex-1 px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500" />
        <button @click="onAddModel" class="px-3 py-1.5 bg-blue-500 hover:bg-blue-600 text-white rounded-lg text-xs font-medium transition">+ 添加</button>
      </div>
    </div>
  </div>
</template>
