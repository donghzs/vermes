<!--
  G13: 成长页 /growth —— 全宽路由页，像「设置」一样占满主内容区。
  把原本挤在侧栏底部的成长面板 + 能力自检 + 我懂你，搬到台前。
  数据源与侧栏 EvolutionPanel 完全同源（复用它 fullPage 模式）。
-->
<template>
  <div class="h-full flex flex-col bg-gray-50 dark:bg-gray-900">
    <!-- 页头 -->
    <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center gap-3 bg-white dark:bg-gray-800 shrink-0">
      <div class="w-9 h-9 rounded-xl bg-emerald-500/10 flex items-center justify-center text-xl">🧠</div>
      <div>
        <h1 class="text-base font-semibold text-gray-900 dark:text-white">我的成长</h1>
        <p class="text-xs text-gray-400 dark:text-gray-500">它怎么学、会什么、眼里怎么看你的，都在这一页</p>
      </div>
    </div>

    <!-- 正文（全宽滚动） -->
    <div class="flex-1 overflow-y-auto">
      <div class="max-w-5xl mx-auto px-6 py-6 space-y-6">
        <!-- 成长核心：复用 EvolutionPanel fullPage 模式 -->
        <EvolutionPanel fullPage />
      </div>
    </div>
  </div>
</template>

<script setup>
import EvolutionPanel from './EvolutionPanel.vue'
</script>
