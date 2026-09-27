<template>
  <!-- T5 联邦状态条：谁在编排 · A2A/接力 · 作用域（只展示当前会话 timeline 信号） -->
  <div
    v-if="signal"
    class="px-3 py-1.5 flex items-center gap-2 text-[11px] rounded-lg border border-cyan-200 dark:border-cyan-800 bg-cyan-50 dark:bg-cyan-900/20 text-cyan-800 dark:text-cyan-100"
  >
    <span class="w-1.5 h-1.5 rounded-full bg-cyan-500 shrink-0 animate-pulse" />
    <span class="font-medium shrink-0">联邦</span>
    <span class="min-w-0 flex-1 truncate">
      <template v-if="signal.kind === 'peer'">
        <b>{{ signal.from }}</b> ↔ <b>{{ signal.to }}</b>
        · {{ signal.transport }}
        · {{ scopeLabel }}
      </template>
      <template v-else>
        <b>{{ signal.from }}</b> 公开接力
        · {{ signal.transport }}
      </template>
    </span>
    <button
      class="shrink-0 text-cyan-600 dark:text-cyan-300 hover:underline"
      @click="expanded = !expanded"
    >{{ expanded ? '收起' : `事件 ${signals.length}` }}</button>
  </div>

  <!-- 事件列表（仅当前会话，不跨房） -->
  <div
    v-if="expanded && signals.length"
    class="mt-1 rounded-lg border border-cyan-100 dark:border-cyan-900/50 bg-white dark:bg-gray-900/40 px-2.5 py-2 space-y-1"
  >
    <div
      v-for="(s, i) in recent"
      :key="s.id || i"
      class="text-[11px] text-gray-600 dark:text-gray-300 flex gap-2"
    >
      <span class="text-cyan-500 shrink-0 font-medium">
        {{ s.kind === 'peer' ? 'A2A' : '接力' }}
      </span>
      <span class="min-w-0 truncate">
        <template v-if="s.kind === 'peer'">
          {{ s.from }} ↔ {{ s.to }} · {{ federationScopeLabel(s.scope) }}
        </template>
        <template v-else>
          {{ s.from }} 点名接力
        </template>
      </span>
    </div>
    <p class="text-[10px] text-gray-400 pt-0.5">
      <template v-if="signals.length > 6">共 {{ signals.length }} 条 · </template>
      仅当前会话可见
    </p>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import {
  parseFederationSignals,
  latestFederation,
  federationScopeLabel,
} from '../utils/federation'

const props = defineProps({
  /** 当前房/会话的消息时间线（scope 边界：只认这份，不跨房拉） */
  timeline: { type: Array, default: () => [] },
  roomId: { type: String, default: '' },
})

const expanded = ref(false)

const signals = computed(() => parseFederationSignals(props.timeline, { roomId: props.roomId }))
const signal = computed(() => latestFederation(props.timeline, { roomId: props.roomId }))
const recent = computed(() => signals.value.slice(-6).reverse())
const scopeLabel = computed(() =>
  signal.value ? federationScopeLabel(signal.value.scope) : ''
)
</script>
