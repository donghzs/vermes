<script setup>
import { ref, onMounted } from 'vue'
import api from '../services/api.js'
import { useChatStore } from '../stores/chat.js'

const experts = ref([])
const loading = ref(false)
const busyId = ref('')
const frequent = ref([])   // 常用来宾（按使用频次）

const chat = useChatStore()

const categoryLabels = {
  '01-ProductDesign': '产品设计',
  '02-Engineering': '技术工程',
  '03-GameSpatial': '游戏空间',
  '04-DataAI': '数据智能',
  '05-MarketingGrowth': '营销增长',
  '06-ContentCreative': '内容创作',
  '07-SalesCommerce': '销售商务',
  '08-FinanceInvestment': '金融投资',
  '09-OperationsHR': '运营人力',
  '10-ProjectQuality': '项目质量',
  '11-SecurityCompliance': '法务安全',
  '12-IndustryConsultant': '行业顾问',
}

function zh(obj, fallback = '') {
  if (!obj) return fallback
  return obj.zh || obj.en || fallback
}

async function loadExperts() {
  loading.value = true
  try {
    const data = await api.getExperts()
    experts.value = Array.isArray(data) ? data : []
  } catch (e) {
    console.error('Failed to load experts:', e)
    experts.value = []
  } finally {
    loading.value = false
  }
}

// 常用来宾：按使用频次拉取 top-3，并映射到专家目录
async function loadFrequent() {
  try {
    const data = await api.getUsageRecommendations('expert', 3)
    const items = (data && data.items) || []
    const byId = new Map(experts.value.map((e) => [e.id, e]))
    const mapped = []
    for (const it of items) {
      const ex = byId.get(it.id)
      if (ex) mapped.push({ ...ex, _count: it.count })
    }
    frequent.value = mapped
  } catch (e) {
    frequent.value = []
  }
}

async function useExpert(expert, promptText) {
  busyId.value = expert.id
  try {
    // 尽力启用该专家映射的技能（已装则开启，未装则尝试安装）
    for (const ss of (expert.skills_status || [])) {
      try {
        if (ss.installed && !ss.enabled) {
          await api.toggleSkill(ss.name, true)
        } else if (!ss.installed) {
          await api.installSkill({ identifier: ss.name, name: ss.name })
        }
      } catch (e) {
        // 技能不可用时不影响进入对话
      }
    }
    // 记录使用（越用越懂用户）——失败不影响主流程
    try {
      await api.recordUsage({ kind: 'expert', id: expert.id, title: zh(expert.profession) })
    } catch (e) { /* no-op */ }
    const text = promptText || expert.prompt || zh(expert.quickPrompts?.[0]) || ''
    await chat.createSession(zh(expert.profession) || '专家')
    await chat.sendMessage(text)
  } catch (e) {
    console.error('useExpert failed:', e)
  } finally {
    busyId.value = ''
  }
}

onMounted(() => {
  loadExperts().then(loadFrequent)
})
</script>

<template>
  <div class="space-y-3">
    <div class="flex items-center gap-2">
      <span class="text-lg">🎓</span>
      <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300">专家</h3>
      <span class="text-xs text-gray-400">陪你解决问题的 AI 搭档</span>
    </div>

    <div class="space-y-2 max-h-80 overflow-y-auto">
      <!-- 常用来宾：按使用频次推荐 -->
      <div v-if="frequent.length" class="rounded-lg bg-blue-50 dark:bg-blue-950/40 p-3 space-y-2">
        <div class="text-[10px] text-blue-500 font-medium">常用来宾 · 越用越懂你</div>
        <div class="flex flex-wrap gap-1.5">
          <button v-for="ex in frequent" :key="ex.id"
                  @click="useExpert(ex)"
                  :disabled="busyId === ex.id"
                  class="text-[10px] px-2 py-1 rounded-full bg-white dark:bg-gray-800 border border-blue-200 dark:border-blue-800 text-blue-600 dark:text-blue-300 hover:border-blue-400 disabled:opacity-50">
            {{ zh(ex.profession) }}
            <span class="text-gray-400 ml-0.5">×{{ ex._count }}</span>
          </button>
        </div>
      </div>

      <div v-for="expert in experts" :key="expert.id"
           class="rounded-lg bg-gray-50 dark:bg-gray-800 p-3 space-y-2">
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <div class="text-sm font-medium text-gray-800 dark:text-gray-100 truncate">
              {{ zh(expert.profession) }}
              <span class="text-[10px] text-gray-400 font-normal ml-1">{{ zh(expert.displayName) }}</span>
            </div>
            <div class="text-[10px] text-blue-500 mt-0.5">
              {{ categoryLabels[expert.categoryId] || expert.categoryId || '' }}
              <span v-if="expert.ready" class="text-green-500 ml-1">· 已就绪</span>
              <span v-else class="text-orange-400 ml-1">· 需安装能力</span>
            </div>
          </div>
        </div>

        <p class="text-[11px] text-gray-500 dark:text-gray-400 leading-snug">{{ zh(expert.displayDescription) }}</p>

        <div class="flex flex-wrap gap-1">
          <span v-for="t in (expert.tags || [])" :key="zh(t)"
                class="text-[9px] px-1.5 py-0.5 rounded-full bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400">
            {{ zh(t) }}
          </span>
        </div>

        <div class="flex flex-wrap gap-1.5">
          <button v-for="(qp, i) in (expert.quickPrompts || [])" :key="i"
                  @click="useExpert(expert, zh(qp))"
                  :disabled="busyId === expert.id"
                  class="text-[10px] px-2 py-1 rounded-full border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-blue-400 hover:text-blue-500 disabled:opacity-50">
            {{ zh(qp) }}
          </button>
        </div>

        <button @click="useExpert(expert)"
                :disabled="busyId === expert.id"
                class="w-full text-xs py-1.5 rounded-lg bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50">
          {{ busyId === expert.id ? '准备中…' : '用一下' }}
        </button>
      </div>

      <div v-if="loading" class="text-center py-4 text-xs text-gray-400 animate-pulse">加载中…</div>
      <div v-else-if="experts.length === 0" class="text-center py-6 text-xs text-gray-400">
        <div class="text-2xl mb-1">🎓</div>
        <div>暂无专家</div>
      </div>
    </div>
  </div>
</template>
