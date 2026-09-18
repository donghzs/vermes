<script setup>
/**
 * U-P0-6 场景状态条 — 简洁优先。
 * 一切正常 → 极细灰字 chips（几乎不占视觉）；
 * 缺前置条件 → 父组件传 alert，由 PrereqBanner 承载可点修复（平时不出现彩条）。
 */
import { computed } from 'vue'
import PrereqBanner from './PrereqBanner.vue'

const props = defineProps({
  /** [{ key, icon?, label, title? }] — 仅在「可继续干活」时展示上下文 */
  items: { type: Array, default: () => [] },
  /** { title, text, primaryLabel, secondaryLabel, tone } 有值才显示横幅 */
  alert: { type: Object, default: null },
})

const emit = defineEmits(['primary', 'secondary', 'dismiss'])

const showAlert = computed(() => !!(props.alert && (props.alert.title || props.alert.text)))
const showChips = computed(() => !showAlert.value && props.items.length > 0)
</script>

<template>
  <PrereqBanner
    v-if="showAlert"
    :visible="true"
    :title="alert.title || ''"
    :text="alert.text || ''"
    :tone="alert.tone || 'amber'"
    :primary-label="alert.primaryLabel || ''"
    :secondary-label="alert.secondaryLabel || ''"
    :dismissible="!!alert.dismissible"
    @primary="emit('primary')"
    @secondary="emit('secondary')"
    @dismiss="emit('dismiss')"
  />
  <div
    v-else-if="showChips"
    class="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-1 text-[11px] text-gray-400 dark:text-gray-500 border-b border-gray-100 dark:border-gray-800"
    data-testid="scene-status-chips"
  >
    <span
      v-for="it in items"
      :key="it.key"
      class="inline-flex items-center gap-1 min-w-0"
      :title="it.title || it.label"
    >
      <span v-if="it.icon">{{ it.icon }}</span>
      <span class="truncate max-w-[14rem]">{{ it.label }}</span>
    </span>
  </div>
</template>
