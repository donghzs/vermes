<script setup>
// 独立的全屏技能市场页（替代之前复用 SkillManager 紧凑布局导致空间浪费的问题）。
// 路由 /skill-market 直接渲染本组件，不再走 SkillManager。
import { ref, onMounted, computed } from 'vue'
import api from '../services/api.js'
import { toast } from '../utils/toast'

// ── 技能中文名映射表（基于已知的常用 skill 命名补全）。
//    未命中的仍显示原英文名（让用户学会真名也算一种教育）──
const CN_NAME_MAP = {
  // 学术 / 写作
  'scholarforge': '学术写作',
  'scholar-forge': '学术写作',
  'academic-search': '学术搜索',
  'paper-search': '论文检索',
  'arxiv-search': 'arXiv 论文',
  'citation-manager': '文献管理',
  'latex-helper': 'LaTeX 助手',
  // 编程 / 开发
  'git-commit': 'Git 提交',
  'github': 'GitHub 集成',
  'code-review': '代码审查',
  'lsp-diagnose': '代码诊断',
  'lsp-completion': '代码补全',
  'code-execution': '代码执行',
  'skill-vetter': '技能安全审查',
  'skillvetter': '技能安全审查',
  'browser-tools': '浏览器自动化',
  'browser-tools-mcp': '浏览器自动化',
  // 文件 / 系统
  'read-file': '读取文件',
  'write-file': '写入文件',
  'edit-file': '编辑文件',
  'search-files': '搜索文件',
  'list-directory': '列出目录',
  'terminal': '终端命令',
  'code_execution': '代码执行',
  'code_execution_tool': '代码执行',
  // 知识 / 记忆
  'memory': '记忆管理',
  'ontology': '知识图谱',
  'ima': 'IMA 知识库',
  'ima-skills': 'IMA 知识库',
  // 网络 / 搜索
  'web-search': '网页搜索',
  'web-search-v2': '网页搜索',
  'google-search': '谷歌搜索',
  'fetch-url': '抓取网页',
  'browse-url': '浏览网页',
  'url-fetch': '网页抓取',
  // 多媒体
  'image-gen': '图像生成',
  'image-gen-provider': '图像生成',
  'vision-analyze': '图像识别',
  'tts': '语音合成',
  'stt': '语音转写',
  // 文本处理
  'humanizer': '文本润色',
  'humanize': '文本润色',
  'translator': '翻译助手',
  'translate': '翻译助手',
  'summarize': '摘要生成',
  // 工具集
  'weather': '查询天气',
  'cron': '定时任务',
  'scheduler': '定时任务',
  'email-skill': '邮件工具',
  'imap-smtp-email': '邮箱收发',
  'find-skills': '发现技能',
  'qclaw-rules': 'QClaw 规则',
  'qclaw-env': 'QClaw 环境',
  'qclaw-cron-skill': 'QClaw 定时',
  'qclaw-generate-image': 'QClaw 图像',
  'cloud-upload-backup': '云端备份',
  'tencentmap-jsapi-gl-skill': '腾讯地图',
  'qclaw-skill-creator': 'QClaw 技能',
  'another_them': '人格克隆',
  'pdf': 'PDF 处理',
  'xlsx': '表格处理',
  'docx': 'Word 处理',
  'vermes-build': 'Vermes 构建',
}
function cnName(name) {
  if (!name) return ''
  return CN_NAME_MAP[name.toLowerCase()] || CN_NAME_MAP[name] || name
}

// 标签转中文
function cnTag(t) {
  const map = {
    'academic': '学术', 'writing': '写作', 'paper': '论文', 'research': '研究',
    'code': '代码', 'review': '审查', 'git': 'Git', 'github': 'GitHub',
    'file': '文件', 'memory': '记忆', 'knowledge': '知识', 'web': '网页',
    'image': '图像', 'video': '视频', 'audio': '音频', 'search': '搜索',
    'translate': '翻译', 'weather': '天气', 'cron': '定时', 'email': '邮件',
    'pdf': 'PDF', 'docx': 'Word', 'xlsx': 'Excel', 'pptx': 'PPT',
  }
  return map[(t || '').toLowerCase()] || t
}

// 信任等级
function trustLabel(item) {
  if (item.source === 'official') return '官方'
  if (item.trust_level === 'trusted') return '已认证'
  if (item.trust_level === 'community') return '社区'
  return item.trust_level || item.trust || item.source || ''
}
function trustClass(item) {
  if (item.source === 'official') return 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400'
  if (item.trust_level === 'trusted') return 'bg-green-100 dark:bg-green-900/30 text-green-600 dark:text-green-400'
  if (item.trust_level === 'community') return 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-600 dark:text-yellow-400'
  return 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
}

// ── State ──
const skills = ref([])            // 已安装列表
const marketQuery = ref('')
const marketSource = ref('all')
const marketItems = ref([])
const marketLoading = ref(false)
const marketError = ref('')
const marketTotal = ref(0)
const auditData = ref({})          // { skillName: { entries, installed } } P0-3 审计详情
const auditExpanded = ref({})      // { skillName: bool } 审计展开态
const installingName = ref('')
const marketPage = ref(1)
const isTrending = ref(false)  // 🔥 热门模式
const pageSize = 12  // 网格 3~4 列 × 3 行 = 12 卡片

const sourceOptions = [
  { id: 'all', label: '全部来源' },
  { id: 'official', label: '⭐ 官方' },
  { id: 'clawhub', label: '🟣 QClaw' },
  { id: 'github', label: '🐙 GitHub' },
  { id: 'skillhub', label: '🔶 Skillhub' },
  { id: 'lobehub', label: '🟠 LobeHub' },
  { id: 'claude-marketplace', label: '🟢 Claude' },
  { id: 'browse-sh', label: '🔵 Browse.sh' },
  { id: 'skills-sh', label: '🟣 Skills.sh' },
  { id: 'well-known', label: '⚪ WellKnown' },
]

const pagedMarket = computed(() => {
  const start = (marketPage.value - 1) * pageSize
  return marketItems.value.slice(start, start + pageSize)
})
const totalPages = computed(() => Math.max(1, Math.ceil(marketItems.value.length / pageSize)))

function isInstalled(name) {
  return skills.value.some(s => s.name === name)
}

async function loadSkills() {
  try {
    const data = await api.getSkills()
    skills.value = Array.isArray(data) ? data : []
  } catch (e) {
    console.error('Failed to load skills:', e)
  }
}

async function searchMarket() {
  marketLoading.value = true
  marketError.value = ''
  marketPage.value = 1
  try {
    let data
    if (isTrending.value) {
      data = await api.getTrendingSkills()
    } else {
      data = await api.searchSkills(marketQuery.value.trim(), marketSource.value, 48)
    }
    marketItems.value = data?.items || data?.results || data?.skills || []
    marketTotal.value = data?.total ?? marketItems.value.length
  } catch (e) {
    marketError.value = e.message || '搜索失败'
  } finally {
    marketLoading.value = false
  }
}

async function installMarket(item) {
  if (installingName.value) return
  installingName.value = item.name
  try {
    await api.installSkill({ name: item.name, source: item.source, url: item.url })
    toast.success(`已安装：${cnName(item.name)}`)
    await loadSkills()
  } catch (e) {
    toast.error(`安装失败：${e.message || e}`)
  } finally {
    installingName.value = ''
  }
}

async function uninstallMarket(item) {
  if (!confirm(`确认卸载「${cnName(item.name)}」？`)) return
  installingName.value = item.name
  try {
    await api.uninstallSkill(item.name)
    toast.success(`已卸载：${cnName(item.name)}`)
    await loadSkills()
  } catch (e) {
    toast.error(`卸载失败：${e.message || e}`)
  } finally {
    installingName.value = ''
  }
}

// P0-3 供应链安全审计：按需拉取审计详情，点击展开
async function toggleAudit(name) {
  const isOpen = auditExpanded.value[name]
  auditExpanded.value = { ...auditExpanded.value, [name]: !isOpen }
  if (isOpen || auditData.value[name]) return
  try {
    auditData.value = { ...auditData.value, [name]: await api.auditSkill(name) }
  } catch (e) {
    auditData.value = { ...auditData.value, [name]: { entries: [], installed: null, error: e.message } }
  }
}

function verdictLabel(v) {
  return { safe: '安全', caution: '注意', dangerous: '危险', n_a: '—' }[v] || v || '—'
}
function verdictClass(v) {
  return {
    safe: 'bg-green-100 dark:bg-green-900/30 text-green-600 dark:text-green-400',
    caution: 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-600 dark:text-yellow-400',
    dangerous: 'bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400',
  }[v] || 'bg-gray-100 dark:bg-gray-700 text-gray-500'
}

function setSource(id) {
  isTrending.value = false
  marketSource.value = id
  searchMarket()
}

function setTrending() {
  isTrending.value = true
  marketSource.value = 'all'
  searchMarket()
}

function goPage(p) {
  if (p < 1 || p > totalPages.value) return
  marketPage.value = p
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

onMounted(async () => {
  await loadSkills()
  await searchMarket()
})
</script>

<template>
  <div class="flex flex-col h-full bg-gradient-to-br from-slate-50 to-blue-50 dark:from-gray-900 dark:to-gray-800">
    <!-- 顶部 Hero 区 -->
    <div class="px-8 py-6 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
      <div class="max-w-6xl mx-auto">
        <h1 class="text-2xl font-bold text-gray-900 dark:text-gray-100 flex items-center gap-3">
          <span class="text-3xl">🧩</span>
          技能市场
        </h1>
        <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
          发现并安装 AI 技能扩展你的 Agent 能力 — 搜索 / 筛选 / 一键安装
        </p>

        <!-- 搜索 + 来源筛选 -->
        <div class="mt-4 flex items-center gap-3">
          <div class="flex-1 relative">
            <input
              v-model="marketQuery"
              @keyup.enter="searchMarket"
              type="text"
              placeholder="搜索技能，如：论文、网页、翻译、天气..."
              class="w-full pl-10 pr-4 py-2.5 rounded-xl bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
            />
            <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
            </svg>
          </div>
          <button @click="searchMarket" class="px-5 py-2.5 rounded-xl bg-blue-500 text-white font-medium hover:bg-blue-600 transition flex items-center gap-1.5">
            <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
            搜索
          </button>
        </div>
        <div class="mt-3 flex flex-wrap gap-2">
          <button v-for="opt in sourceOptions" :key="opt.id"
                  @click="setSource(opt.id)"
                  :class="marketSource === opt.id && !isTrending
                    ? 'bg-blue-500 text-white shadow-sm'
                    : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700 hover:border-blue-400'"
                  class="text-sm px-4 py-1.5 rounded-full font-medium transition">
            {{ opt.label }}
          </button>
          <button @click="setTrending"
                  :class="isTrending
                    ? 'bg-orange-500 text-white shadow-sm'
                    : 'bg-white dark:bg-gray-800 text-orange-600 dark:text-orange-400 border border-orange-200 dark:border-orange-800 hover:border-orange-400'"
                  class="text-sm px-4 py-1.5 rounded-full font-medium transition">
            🔥 热门
          </button>
        </div>

        <!-- 统计 -->
        <div class="mt-3 flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
          <span>共找到 <b class="text-blue-500">{{ marketTotal || marketItems.length }}</b> 个技能，已安装 <b class="text-green-500">{{ skills.length }}</b> 个</span>
          <span v-if="marketQuery.trim()">关键词：<b class="text-gray-700 dark:text-gray-300">"{{ marketQuery }}"</b></span>
        </div>
      </div>
    </div>

    <!-- 主体网格 -->
    <div class="flex-1 overflow-y-auto px-8 py-6">
      <div class="max-w-6xl mx-auto">
        <!-- 加载中 -->
        <div v-if="marketLoading" class="text-center py-20 text-gray-400">
          <div class="inline-block animate-spin text-3xl mb-3">⏳</div>
          <div>正在搜索技能...</div>
        </div>

        <!-- 错误 -->
        <div v-else-if="marketError" class="text-center py-20">
          <div class="text-5xl mb-3">⚠️</div>
          <div class="text-red-500">{{ marketError }}</div>
          <button @click="searchMarket" class="mt-4 px-4 py-2 rounded-lg bg-blue-500 text-white">重试</button>
        </div>

        <!-- 空 -->
        <div v-else-if="pagedMarket.length === 0" class="text-center py-20 text-gray-400">
          <div class="text-6xl mb-4">🔍</div>
          <div class="text-lg">没有找到匹配的技能</div>
          <div class="text-sm mt-2">换个关键词，或切换来源试试</div>
        </div>

        <!-- 网格卡片 -->
        <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          <div v-for="item in pagedMarket" :key="item.identifier || item.name"
               class="bg-white dark:bg-gray-800 rounded-2xl p-5 border border-gray-200 dark:border-gray-700 hover:border-blue-400 hover:shadow-lg transition-all flex flex-col">
            <!-- 标题行 -->
            <div class="flex items-start justify-between gap-2 mb-2">
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 mb-1">
                  <h3 class="text-base font-semibold text-gray-800 dark:text-gray-100 truncate" :title="item.name">
                    {{ cnName(item.name) }}
                  </h3>
                  <span :class="trustClass(item)" class="text-[10px] px-1.5 py-0.5 rounded-full font-medium flex-shrink-0">
                    {{ trustLabel(item) }}
                  </span>
                </div>
                <div class="text-[11px] text-gray-400 font-mono truncate" :title="item.name">{{ item.name }}</div>
              </div>
            </div>

            <!-- 描述（最多 4 行） -->
            <p class="text-sm text-gray-600 dark:text-gray-300 leading-relaxed line-clamp-4 mb-3 min-h-[5rem]">
              {{ item.description || '暂无描述' }}
            </p>

            <!-- 标签 -->
            <div v-if="item.tags && item.tags.length" class="flex flex-wrap gap-1 mb-3">
              <span v-for="t in item.tags.slice(0, 4)" :key="t"
                    class="text-[10px] px-2 py-0.5 rounded-full bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400">
                {{ cnTag(t) }}
              </span>
            </div>

            <!-- 底部：作者 + 操作 + 审计入口 -->
            <div class="mt-auto pt-3 border-t border-gray-100 dark:border-gray-700 flex items-center justify-between gap-2">
              <div class="text-[11px] text-gray-400 truncate flex-1 min-w-0">
                <span v-if="item.author">@{{ item.author }}</span>
                <span v-else-if="item.source" class="capitalize">{{ item.source }}</span>
              </div>
              <div class="flex items-center gap-1.5 flex-shrink-0">
                <button v-if="isInstalled(item.name)" @click="toggleAudit(item.name)"
                        class="text-[10px] px-2 py-1 rounded-md text-gray-400 hover:text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/20 transition">
                  {{ auditExpanded[item.name] ? '▾' : '▸' }} 审计
                </button>
                <button v-if="!isInstalled(item.name)" @click="installMarket(item)"
                        :disabled="installingName === item.name"
                        class="text-xs px-3 py-1.5 rounded-lg bg-blue-500 text-white font-medium hover:bg-blue-600 disabled:opacity-50 transition">
                  {{ installingName === item.name ? '安装中…' : '+ 安装' }}
                </button>
                <button v-else @click="uninstallMarket(item)"
                        :disabled="installingName === item.name"
                        class="text-xs px-3 py-1.5 rounded-lg bg-green-100 dark:bg-green-900/30 text-green-600 dark:text-green-400 font-medium hover:bg-red-100 hover:text-red-600 disabled:opacity-50 transition">
                  ✓ 已装
                </button>
              </div>
            </div>

            <!-- P0-3 供应链安全审计详情 -->
            <div v-if="auditExpanded[item.name]" class="mt-2 pt-2 border-t border-dashed border-gray-200 dark:border-gray-700 text-xs">
              <div v-if="!auditData[item.name]" class="text-gray-400 py-2">加载中…</div>
              <div v-else-if="auditData[item.name].error" class="text-red-400 py-2">加载失败: {{ auditData[item.name].error }}</div>
              <div v-else-if="!auditData[item.name].entries?.length && !auditData[item.name].installed" class="text-gray-400 py-2">暂无审计记录</div>
              <div v-else class="space-y-1.5">
                <!-- 当前安装快照 -->
                <div v-if="auditData[item.name].installed" class="flex items-center gap-2 pb-1">
                  <span class="text-gray-500">当前：</span>
                  <span :class="verdictClass(auditData[item.name].installed.scan_verdict)" class="px-1.5 py-0.5 rounded font-medium">
                    {{ verdictLabel(auditData[item.name].installed.scan_verdict) }}
                  </span>
                  <span class="text-gray-400 font-mono text-[10px]" :title="auditData[item.name].installed.skill_hash">
                    sha: {{ auditData[item.name].installed.skill_hash?.slice(0, 12) || '—' }}…
                  </span>
                </div>
                <!-- 历史记录 -->
                <div v-if="auditData[item.name].entries?.length" class="space-y-1">
                  <div class="text-gray-500 pb-0.5">审计历史 ({{ auditData[item.name].entries.length }})</div>
                  <div v-for="(e, i) in auditData[item.name].entries.slice(0, 5)" :key="i" class="flex items-start gap-1.5 py-0.5">
                    <span class="text-[10px] text-gray-400 font-mono flex-shrink-0 mt-0.5">{{ (e.ts || '').slice(5, 16) }}</span>
                    <span :class="{ 'text-green-500': e.action === 'INSTALL', 'text-red-500': e.action === 'BLOCKED', 'text-gray-400': e.action === 'UNINSTALL' }" class="font-medium flex-shrink-0">{{ e.action }}</span>
                    <span :class="verdictClass(e.verdict)" class="px-1 rounded text-[10px] flex-shrink-0">{{ verdictLabel(e.verdict) }}</span>
                    <span v-if="e.findings?.length" class="text-amber-500 text-[10px]">{{ e.findings.length }} 项发现</span>
                    <span v-if="e.scan_summary" class="text-gray-400 truncate" :title="e.scan_summary">{{ e.scan_summary }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 分页 -->
        <div v-if="totalPages > 1" class="flex items-center justify-center gap-3 mt-8 pb-4">
          <button @click="goPage(marketPage - 1)" :disabled="marketPage === 1"
                  class="px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-700 disabled:opacity-40 hover:bg-white dark:hover:bg-gray-800 transition">
            ‹ 上一页
          </button>
          <span class="text-sm text-gray-500">{{ marketPage }} / {{ totalPages }}</span>
          <button @click="goPage(marketPage + 1)" :disabled="marketPage === totalPages"
                  class="px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-700 disabled:opacity-40 hover:bg-white dark:hover:bg-gray-800 transition">
            下一页 ›
          </button>
        </div>
      </div>
    </div>
  </div>
</template>