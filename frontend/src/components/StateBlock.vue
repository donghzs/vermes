<script setup>
// 2.4.9 桌面端体验专项 B1：统一状态块（loading / empty / error / ok）
//
// 背景：此前各页面的"加载中 / 暂无 / 失败"是各写各的（15+ 组件各自实现，
// 文案与视觉都不统一，例如"暂无记录""暂无数据""加载失败"混用）。本组件把
// 三态表现收敛成一套，纯增量——不触碰任何现有业务逻辑，可逐页替换。
import { computed } from 'vue'

const props = defineProps({
  /** loading | empty | error | ok */
  state: { type: String, default: 'empty' },
  /** 主文案；留空则用 preset 默认文案 */
  text: { type: String, default: '' },
  /** 次要说明（灰、更小），用于解释"为什么是空的/失败了怎么办" */
  detail: { type: String, default: '' },
  /** 覆盖 preset 图标 */
  icon: { type: String, default: '' },
  /** 紧凑模式（行内小块，用于面板内嵌而非整页占位） */
  compact: { type: Boolean, default: false },
  /** U-P0-4：可行动按钮文案；有值时显示默认操作按钮（也可用 #actions 插槽自定义） */
  actionLabel: { type: String, default: '' },
})

const emit = defineEmits(['action'])

const PRESET = {
  loading: { icon: '⏳', text: '加载中…', cls: 'text-gray-400' },
  empty: { icon: '📭', text: '暂无数据', cls: 'text-gray-400' },
  error: { icon: '⚠️', text: '加载失败', cls: 'text-amber-600 dark:text-amber-400' },
  ok: { icon: '✅', text: '一切正常', cls: 'text-emerald-600 dark:text-emerald-400' },
}

const preset = computed(() => PRESET[props.state] || PRESET.empty)
const icon = computed(() => props.icon || preset.value.icon)
const text = computed(() => props.text || preset.value.text)
</script>

<template>
  <div
    :class="[
      compact ? 'py-1.5' : 'py-6',
      'flex flex-col items-center justify-center gap-1 text-center select-none',
    ]"
  >
    <div v-if="state === 'loading'" class="animate-spin text-base">⏳</div>
    <div v-else class="text-base">{{ icon }}</div>
    <div :class="['text-xs', preset.cls]">{{ text }}</div>
    <div v-if="detail" class="text-[10px] text-gray-400 max-w-[300px] leading-snug">{{ detail }}</div>
    <slot name="actions">
      <button
        v-if="actionLabel"
        type="button"
        data-testid="state-block-action"
        class="mt-1.5 text-xs px-2.5 py-1 rounded-lg bg-green-500 text-white hover:bg-green-600 transition"
        @click="emit('action')"
      >{{ actionLabel }}</button>
    </slot>
    <slot />
  </div>
</template>
