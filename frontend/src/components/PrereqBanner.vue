<script setup>
// U-P0-4：前置条件横幅 —— 把「还没配 X / 还没有 Y」统一成一条可行动的提示条。
// 与 StateBlock 的分工：StateBlock 占列表/面板的空态；本组件挂在页面/模块顶部，
// 表达「当前缺前置条件，先补上再继续」。条件满足时由父组件控制 visible=false 隐藏。
import { computed } from 'vue'

const props = defineProps({
  /** 是否展示；条件满足时父组件传 false */
  visible: { type: Boolean, default: true },
  /** 短标题，如「还没有论文项目」 */
  title: { type: String, default: '' },
  /** 解释文案：缺什么、为什么要先补 */
  text: { type: String, default: '' },
  /** amber（默认）| blue | green | red | gray */
  tone: { type: String, default: 'amber' },
  primaryLabel: { type: String, default: '' },
  secondaryLabel: { type: String, default: '' },
  dismissible: { type: Boolean, default: false },
})

defineEmits(['primary', 'secondary', 'dismiss'])

const toneCls = computed(() => {
  const map = {
    amber: 'bg-amber-50 dark:bg-amber-900/25 border-amber-200 dark:border-amber-800 text-amber-800 dark:text-amber-200',
    blue: 'bg-blue-50 dark:bg-blue-900/25 border-blue-200 dark:border-blue-800 text-blue-800 dark:text-blue-200',
    green: 'bg-emerald-50 dark:bg-emerald-900/25 border-emerald-200 dark:border-emerald-800 text-emerald-800 dark:text-emerald-200',
    red: 'bg-red-50 dark:bg-red-900/25 border-red-200 dark:border-red-800 text-red-800 dark:text-red-200',
    gray: 'bg-gray-50 dark:bg-gray-800/60 border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300',
  }
  return map[props.tone] || map.amber
})
</script>

<template>
  <div
    v-if="visible"
    :class="['shrink-0 flex items-center gap-3 px-4 py-2.5 flex-wrap border-b text-sm', toneCls]"
    data-testid="prereq-banner"
  >
    <span class="min-w-0">
      <b v-if="title">{{ title }}</b>
      <span v-if="title && text"> —— {{ text }}</span>
      <span v-else-if="text">{{ text }}</span>
      <slot />
    </span>
    <span class="ml-auto flex items-center gap-2 shrink-0">
      <slot name="actions">
        <button
          v-if="primaryLabel"
          type="button"
          data-testid="prereq-primary"
          class="px-3 py-1 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-xs font-medium transition"
          @click="$emit('primary')"
        >{{ primaryLabel }}</button>
        <button
          v-if="secondaryLabel"
          type="button"
          data-testid="prereq-secondary"
          class="px-3 py-1 rounded-lg border border-amber-300 dark:border-amber-700 hover:bg-amber-100 dark:hover:bg-amber-900/40 text-xs transition"
          @click="$emit('secondary')"
        >{{ secondaryLabel }}</button>
      </slot>
      <button
        v-if="dismissible"
        type="button"
        data-testid="prereq-dismiss"
        class="px-2 py-1 text-xs underline opacity-80"
        @click="$emit('dismiss')"
      >知道了</button>
    </span>
  </div>
</template>
