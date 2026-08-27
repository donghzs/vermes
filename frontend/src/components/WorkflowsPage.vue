<template>
  <div class="flex flex-col h-full p-6 text-slate-300">
    <h1 class="text-2xl font-bold mb-4">工作流编排</h1>
    <p class="text-slate-400 mb-6">A2 工作流后端已闭环（G1-G6），前端可视化编辑器待实现。</p>

    <!-- 占位：后续实现 DAG 可视化编辑 -->
    <div class="flex-1 flex items-center justify-center border border-dashed border-slate-700 rounded-lg">
      <div class="text-center">
        <div class="text-5xl mb-3">🔧</div>
        <p class="text-slate-500">可视化 DAG 编辑器（G5）</p>
        <p class="text-sm text-slate-600 mt-1">SSE 契约已就绪，待前端消费</p>
      </div>
    </div>

    <!-- 已有能力概览 -->
    <div class="mt-6 grid grid-cols-2 gap-3">
      <div class="p-3 bg-slate-800/50 rounded-lg">
        <div class="text-emerald-400 text-sm font-medium mb-1">✅ 后端已闭环</div>
        <ul class="text-xs text-slate-400 space-y-1">
          <li>G1a 拓扑引擎 + G1b 执行器</li>
          <li>G2 断点续跑 / 失败语义</li>
          <li>G3 可复用模板</li>
          <li>G4 步骤间数据流</li>
          <li>G6 触发器（cron + webhook）</li>
        </ul>
      </div>
      <div class="p-3 bg-slate-800/50 rounded-lg">
        <div class="text-amber-400 text-sm font-medium mb-1">⏸ 前端待实现</div>
        <ul class="text-xs text-slate-400 space-y-1">
          <li>G5 可视化 DAG 编辑器</li>
          <li>触发器配置 UI</li>
          <li>执行进度实时面板</li>
          <li>重试 / 跳过 / 续跑按钮</li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup>
// A2 工作流前端占位页
// 后端 G1-G6 已闭环，前端 G5 可视化编辑器待实现
</script>
