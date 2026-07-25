<script setup>
// FlowGuide：写作流程引导（P0c-3）。
// 把「文献→大纲→写作→评分→查重」串成 stepper，点「打开工具」跳到工具箱并选中对应工具，
// 复用既有 invokeTool + quality_gate 链路（避免重复实现）。
import { ref } from 'vue'
import { useScholarStore } from '../../stores/scholar'

const scholar = useScholarStore()

const steps = [
  {
    tool: 'scholarforge_search',
    emoji: '🔍',
    title: '1. 检索文献',
    desc: '用 query 检索 arXiv/CrossRef 等 7 个免费源，沉淀到当前项目。',
    tip: '建议先选定项目，检索结果自动归属。',
  },
  {
    tool: 'scholarforge_outline',
    emoji: '🗂️',
    title: '2. 生成大纲',
    desc: '给 topic + paper_type，产出章节结构、每章要点与预估字数。',
    tip: '可把大纲复制进 write 的 context 保持一致。',
  },
  {
    tool: 'scholarforge_write',
    emoji: '✍️',
    title: '3. 撰写章节',
    desc: '选 section_type（引言/方法/…），写回时自动过 quality_gate（AIGC+查重+设计缺陷）。',
    tip: '写回类工具触发闸门，质量报告落库到 QualityView。',
  },
  {
    tool: 'scholarforge_score',
    emoji: '📊',
    title: '4. 三维度评分',
    desc: '对 content 做原创性 / 逻辑性 / 引用完整性评分，给出改进建议。',
    tip: '把待评章节文本粘贴进 content。',
  },
  {
    tool: 'scholarforge_plagiarism_check',
    emoji: '🛡️',
    title: '5. 查重检测',
    desc: 'SimHash + N-gram + AIGC 启发式，输出相似度与高风险段落。',
    tip: '最终定稿前跑一遍，确认 AI 痕迹与重复率可控。',
  },
]

const done = ref(new Set())

function openStep(s) {
  done.value.add(s.tool)
  done.value = new Set(done.value) // 触发响应
  scholar.runToolInBox(s.tool)
}
</script>

<template>
  <div class="p-4 space-y-4">
    <p class="text-sm text-gray-500">
      标准论文写作链路：检索 → 大纲 → 写作 → 评分 → 查重。点「打开工具」会跳到工具箱并预选中对应工具，
      全部经质量护栏，无需手动串联。
    </p>

    <div class="space-y-3">
      <div
        v-for="s in steps"
        :key="s.tool"
        class="flex items-start gap-3 rounded-lg border p-4 transition"
        :class="
          done.has(s.tool)
            ? 'border-emerald-300 dark:border-emerald-700 bg-emerald-50/40 dark:bg-emerald-900/10'
            : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800'
        "
      >
        <div class="text-2xl leading-none">{{ s.emoji }}</div>
        <div class="flex-1 min-w-0">
          <h3 class="text-sm font-semibold flex items-center gap-2">
            {{ s.title }}
            <span
              v-if="done.has(s.tool)"
              class="text-xs text-emerald-600 dark:text-emerald-400"
            >✓ 已打开</span>
          </h3>
          <p class="mt-1 text-xs text-gray-500 leading-snug">{{ s.desc }}</p>
          <p class="mt-1 text-xs text-gray-400 leading-snug">💡 {{ s.tip }}</p>
        </div>
        <button
          class="shrink-0 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium transition"
          @click="openStep(s)"
        >
          打开工具
        </button>
      </div>
    </div>

    <p class="text-xs text-gray-400">
      提示：更多单步工具（润色/去AI痕迹/引用格式化/统计校验等）可在「🧰 工具箱」自由调用。
    </p>
  </div>
</template>
