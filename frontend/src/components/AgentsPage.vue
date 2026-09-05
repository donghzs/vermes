<template>
  <div class="h-full flex flex-col bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
    <!-- 顶部 -->
    <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between flex-wrap gap-3">
      <div class="flex items-center gap-3">
        <span class="text-2xl">⛩️</span>
        <h1 class="text-xl font-bold">神魔架 · 请神登堂 / 造神协作</h1>
        <span class="text-sm text-gray-400">
          {{ loading ? '加载中…' : `共 ${agents.length} 个（本机 ${counts.local} · 社区 ${counts.remote}）` }}
        </span>
        <span v-if="recipes.length" class="text-sm text-emerald-600 dark:text-emerald-400">
          · {{ recipes.length }} 个可登堂（ACP）
        </span>
        <span v-if="nativeAgents.length" class="text-sm text-indigo-600 dark:text-indigo-400">
          · {{ nativeAgents.length }} 位自造神
        </span>
      </div>
      <div class="flex items-center gap-2">
        <button
          @click="openForge()"
          class="px-3 py-1.5 text-sm rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white transition"
        >⚒️ 造神</button>
        <input
          v-model="query"
          placeholder="搜索名称 / 描述…"
          class="px-3 py-1 text-sm rounded-lg bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:border-emerald-500 w-56"
        />
        <button
          @click="loadAgents(true)"
          :disabled="loading"
          class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50 transition"
        >{{ loading ? '刷新中…' : '🔄 刷新' }}</button>
      </div>
    </div>

    <!-- 内容 -->
    <div class="flex-1 overflow-y-auto p-6">
      <!-- ⑭ 造神：自造神（原生 agent，可各绑各厂商 API） -->
      <div v-if="nativeAgents.length" class="mb-6">
        <div class="flex items-center gap-2 mb-3">
          <span class="text-sm font-semibold text-gray-600 dark:text-gray-300">⚒️ 自造神（Vermes 原生 · 各绑专属 API）</span>
          <button
            @click="openForge()"
            class="text-xs px-2 py-0.5 rounded-lg bg-indigo-100 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-300 hover:bg-indigo-200 dark:hover:bg-indigo-800 transition"
          >+ 造神</button>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          <div
            v-for="n in nativeAgents"
            :key="n.id"
            class="rounded-xl border border-indigo-200 dark:border-indigo-800 bg-white dark:bg-gray-800 p-4 flex flex-col gap-2 hover:shadow-md transition"
          >
            <div class="flex items-center gap-2 min-w-0">
              <svg viewBox="0 0 32 32" class="w-8 h-8 rounded-full shrink-0">
                <circle cx="16" cy="16" r="16" :fill="`hsl(${n.hue || 0}, 65%, 45%)`" />
                <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="15" font-weight="600">{{ (n.name || '?').slice(0, 1) }}</text>
              </svg>
              <div class="min-w-0">
                <h3 class="font-semibold truncate">{{ n.name }}</h3>
                <p class="text-xs text-gray-400 truncate">@{{ n.id }}</p>
              </div>
              <span v-if="n.is_default" class="ml-auto shrink-0 px-2 py-0.5 text-[10px] rounded-full bg-gray-100 dark:bg-gray-700 text-gray-500">默认</span>
            </div>
            <p v-if="n.description" class="text-xs text-gray-500 dark:text-gray-400" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">{{ n.description }}</p>
            <div class="flex flex-wrap gap-1.5">
              <span v-if="n.provider" class="px-2 py-0.5 text-[11px] rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">{{ n.provider }}</span>
              <span v-if="n.model" class="px-2 py-0.5 text-[11px] rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">{{ n.model }}</span>
              <span class="px-2 py-0.5 text-[11px] rounded-full" :class="n.has_api_key ? 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-600 dark:text-emerald-300' : 'bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-300'">{{ n.has_api_key ? '🔑 专属 Key' : '⚠️ 未绑 Key（用全局）' }}</span>
            </div>
            <div class="mt-auto pt-1 flex items-center gap-2">
              <button
                @click="openForge(n)"
                class="px-2.5 py-1 text-xs rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition"
              >✏️ 编辑</button>
              <span class="text-[11px] text-gray-400">进「诸神会晤」@{{ n.id }} 即可对话</span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="loading && agents.length === 0" class="text-center text-gray-400 py-12">
        <p class="text-lg animate-pulse">⏳ 正在扫描本机 agent…</p>
      </div>
      <div v-else-if="error" class="text-center text-red-400 py-12">
        <p class="text-lg">⚠️ 加载失败</p>
        <p class="text-sm mt-2">{{ error }}</p>
      </div>
      <div v-else-if="filtered.length === 0" class="text-center text-gray-400 py-12">
        <p class="text-lg">🤖 暂未发现智能体</p>
        <p class="text-sm mt-2">本机未安装常见 CLI / App，或封神榜暂无数据</p>
      </div>
      <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <div
          v-for="a in filtered"
          :key="a._key"
          class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4 flex flex-col gap-3 hover:shadow-md transition"
        >
          <div class="flex items-start justify-between gap-2">
            <div class="flex items-center gap-2 min-w-0">
              <span class="text-2xl">{{ kindIcon(a.agent_kind) }}</span>
              <div class="min-w-0">
                <h3 class="font-semibold truncate">{{ a.name || a.id }}</h3>
                <p class="text-xs text-gray-400 truncate">{{ a.id }}</p>
              </div>
            </div>
            <span
              class="shrink-0 px-2 py-0.5 text-xs rounded-full"
              :class="a.source === 'remote' ? 'bg-purple-100 dark:bg-purple-900/40 text-purple-600 dark:text-purple-300' : 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-600 dark:text-emerald-300'"
            >{{ sourceLabel(a.source) }}</span>
          </div>

          <p
            v-if="a.description"
            class="text-sm text-gray-500 dark:text-gray-400"
            style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden"
          >{{ a.description }}</p>

          <div class="flex flex-wrap gap-1.5">
            <span class="px-2 py-0.5 text-[11px] rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">{{ kindLabel(a.agent_kind) }}</span>
            <span class="px-2 py-0.5 text-[11px] rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">鉴权：{{ authLabel(a.auth_scheme) }}</span>
            <span v-if="a.version" class="px-2 py-0.5 text-[11px] rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">v{{ a.version }}</span>
          </div>

          <!--
            ⑭ 字段契约（2026-09-04 董董审计发现 T4 字段错位 bug 后定档）：
            - 顶层  有  agent_kind / auth_scheme / source / install_state / name / version
            - 顶层  无  popularity / homepage / repository（这三个走 extra 或 entry_point）
            - 远端 a.entry_point = homepage（_discover_agents:457 误用，但已用守卫避免重复显示）
            - 远端 a.extra     = {popularity, repository}（homepage 不在 extra）
            - 本地 a.entry_point = 真实 CLI 命令/路径（不当链接用，守卫隐藏）
            - 守护测试：tests/test_agent_discovery.py::test_remote_agent_field_contract
          -->
          <div v-if="a.source !== 'remote' && a.entry_point" class="text-xs text-gray-400 truncate font-mono">↳ {{ a.entry_point }}</div>
          <div v-if="a.source === 'remote' && a.extra && a.extra.popularity" class="text-xs text-amber-500">🔥 热度 {{ a.extra.popularity }}</div>

          <div class="mt-auto pt-1 flex items-center gap-2 text-xs">
            <span class="px-2 py-0.5 rounded-full bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-300">已接入（只读发现）</span>
            <a v-if="a.source === 'remote' && (a.extra && a.extra.repository || a.entry_point)" :href="(a.extra && a.extra.repository) || a.entry_point" target="_blank" rel="noopener" class="text-blue-500 hover:underline">{{ (a.extra && a.extra.repository) ? '仓库' : '主页' }}</a>
          </div>

          <!--
            ⑭ 请神收尾 T4：登堂。
            仅当该 agent 能匹配到一条 ACP 食谱（GET /agents/recipes）时才显示，
            食谱名单由后端下发，不在前端硬编码（历史坑：BricksPage.vue 硬编码）。
          -->
          <div v-if="recipeFor(a)" class="flex items-center gap-2 flex-wrap">
            <span class="px-2 py-0.5 text-[11px] rounded-full bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-300">⚡ ACP 兼容</span>
            <button
              @click="ascend(a)"
              :disabled="stateOf(a._key).status === 'loading'"
              class="px-2.5 py-1 text-xs rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white disabled:opacity-50 transition"
            >{{ ascendLabel(stateOf(a._key)) }}</button>
            <span
              v-if="stateOf(a._key).detail"
              class="text-[11px] truncate"
              :class="stateClass(stateOf(a._key))"
              :title="stateOf(a._key).detail"
            >{{ stateOf(a._key).detail }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 登堂授权弹窗（后端返回 need_auth 时弹出） -->
    <div
      v-if="authModal.open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      @click.self="authModal.open = false"
    >
      <div class="w-[28rem] max-w-[90vw] rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl">
        <h3 class="text-base font-semibold mb-1">
          为「{{ authModal.recipe && authModal.recipe.name }}」配置鉴权
        </h3>
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-3">
          登堂需要环境变量
          <code class="px-1 rounded bg-gray-100 dark:bg-gray-700">{{ authModal.authEnv }}</code>。
          填入后仅在<b>当前 Vermes 进程内</b>生效，重启后需重新填写。
        </p>
        <input
          v-model="authModal.value"
          type="password"
          :placeholder="authModal.authEnv"
          class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 text-gray-900 dark:text-gray-100 focus:outline-none focus:border-emerald-500"
        />
        <p v-if="authModal.error" class="mt-2 text-xs text-red-500">{{ authModal.error }}</p>
        <div class="mt-4 flex justify-end gap-2">
          <button
            @click="authModal.open = false"
            class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600"
          >取消</button>
          <button
            @click="submitAuth"
            class="px-3 py-1.5 text-sm rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white"
          >登堂</button>
        </div>
      </div>
    </div>

    <!-- ⑭ 造神弹窗（原生 agent，可各绑专属 API） -->
    <div
      v-if="forgeModal.open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      @click.self="forgeModal.open = false"
    >
      <div class="w-[30rem] max-w-[92vw] max-h-[90vh] overflow-y-auto rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl">
        <h3 class="text-base font-semibold mb-1">{{ forgeModal.editing ? '编辑神' : '⚒️ 造神' }}</h3>
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-4">
          造一位 Vermes 原生 agent，可绑定专属厂商 API Key——不同神各干各的任务、各用各的厂商。
        </p>
        <div class="flex flex-col gap-3">
          <div>
            <label class="text-xs text-gray-500 mb-1 block">名称 *</label>
            <input v-model="forgeModal.name" placeholder="如：法律顾问 / 数据分析师" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-indigo-500" />
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">一句话描述</label>
            <input v-model="forgeModal.description" placeholder="它的职责 / 擅长领域" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-indigo-500" />
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="text-xs text-gray-500 mb-1 block">厂商（provider）</label>
              <select v-model="forgeModal.provider" @change="onForgeProviderChange" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-indigo-500">
                <option value="">用全局默认（不指定厂商）</option>
                <option v-for="p in modelProviders" :key="p.slug" :value="p.slug">{{ p.name }}<template v-if="p.is_current"> · 当前</template></option>
                <option value="__custom__">自定义厂商…</option>
              </select>
              <input v-if="forgeModal.provider === '__custom__'" v-model="forgeModal.customProvider" placeholder="自定义 provider 名（如 deepseek）" class="mt-2 w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-indigo-500" />
            </div>
            <div>
              <label class="text-xs text-gray-500 mb-1 block">模型（model）</label>
              <select v-if="forgeModels.length" v-model="forgeModal.model" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-indigo-500">
                <option value="">用厂商默认</option>
                <option v-for="m in forgeModels" :key="m" :value="m">{{ m }}</option>
              </select>
              <input v-else v-model="forgeModal.model" placeholder="如 deepseek-chat（留空=厂商默认）" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-indigo-500" />
            </div>
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">专属 API Key（留空 = 用全局 Key）</label>
            <input v-model="forgeModal.apiKey" type="password" :placeholder="forgeModal.editing && forgeModal.editing.has_api_key ? '已配置（留空保持不变）' : 'sk-...'" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-indigo-500" />
            <p v-if="forgeModal.editing && forgeModal.editing.has_api_key" class="text-[11px] text-gray-400 mt-1">已绑专属 Key，留空保持不变；输入新值则覆盖。</p>
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">系统提示词（人设）</label>
            <textarea v-model="forgeModal.systemPrompt" rows="3" placeholder="定义它的性格 / 专长 / 输出风格…" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-indigo-500 resize-none"></textarea>
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 flex items-center justify-between">
              <span>技能集（工具集）</span>
              <button @click="recommendToolsets" class="text-[11px] text-indigo-500 hover:text-indigo-600">✨ 按角色推荐</button>
            </label>
            <p class="text-[11px] text-gray-400 mb-2">留空 = 全量工具；勾选后只给它这些技能，不同角色各干各的活。</p>
            <div v-if="allToolsets.length" class="grid grid-cols-2 gap-1.5 max-h-36 overflow-y-auto pr-1">
              <label v-for="t in allToolsets" :key="t.name" class="flex items-center gap-1.5 px-2 py-1 rounded cursor-pointer text-xs hover:bg-gray-100 dark:hover:bg-gray-700">
                <input type="checkbox" :value="t.name" v-model="forgeModal.toolsets" class="accent-indigo-500" />
                <span class="truncate">{{ t.label || t.name }}</span>
              </label>
            </div>
            <p v-else class="text-[11px] text-gray-400">技能列表加载中/不可用，可留空用全量工具。</p>
          </div>
        </div>
        <p v-if="forgeModal.error" class="mt-3 text-xs text-red-500">{{ forgeModal.error }}</p>
        <div class="mt-4 flex justify-end gap-2">
          <button @click="forgeModal.open = false" class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600">取消</button>
          <button @click="submitForge" class="px-3 py-1.5 text-sm rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white">{{ forgeModal.editing ? '保存' : '造神' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../services/api'
import { showToast as toast } from '../utils/toast'

const agents = ref([])
const loading = ref(false)
const error = ref('')
const query = ref('')

// ⑭ 造神：原生 agent 列表（transport=native 的 Vermes 原生 agent）
const nativeAgents = ref([])
// 造神弹窗状态
const forgeModal = ref({ open: false, editing: null, name: '', description: '', provider: '', model: '', customProvider: '', apiKey: '', systemPrompt: '', hue: 0, toolsets: [] })

// ── 造神：厂商/模型下拉（复用设置页 /api/model/options 已配厂商+精选模型） ──
const modelProviders = ref([])   // [{ slug, name, is_current, models: [] }]
const allToolsets = ref([])      // [{ name, label, description }]（/api/tools/toolsets）
const forgeModels = computed(() => {
  if (!forgeModal.value.provider || forgeModal.value.provider === '__custom__') return []
  const p = modelProviders.value.find(x => x.slug === forgeModal.value.provider)
  return (p && p.models) || []
})

async function loadModelProviders() {
  try {
    const data = await api.getModels()
    modelProviders.value = (data && data.providers) || []
  } catch (e) {
    console.warn('[Agents] 加载厂商/模型列表失败（不影响手动输入）', e)
    modelProviders.value = []
  }
}

async function loadToolsets() {
  try {
    const data = await api.getToolsets()
    allToolsets.value = Array.isArray(data) ? data : []
  } catch (e) {
    console.warn('[Agents] 加载技能集失败（可留空用全量工具）', e)
    allToolsets.value = []
  }
}

function onForgeProviderChange() {
  // 切换厂商时清空之前选的模型（避免张冠李戴：deepseek 的模型配 anthropic 厂商）
  forgeModal.value.model = ''
}

// 角色 → 推荐技能集（与后端 _profile_enabled_toolsets 的 capability_tags 映射对齐）
const _ROLE_TOOLSET_HINTS = {
  '法律': ['file', 'web'],
  '数据': ['code_execution', 'file'],
  '分析': ['code_execution', 'file'],
  '营销': ['web', 'writing'],
  '产品': ['file', 'todo', 'web'],
  '写作': ['scholarforge'],
  '编': ['terminal', 'file', 'code_execution'],
  '研究': ['web', 'search'],
  '健康': ['web', 'search'],
  '教育': ['web', 'search', 'file'],
  '学习': ['web', 'search', 'file'],
}

function recommendToolsets() {
  const name = forgeModal.value.name || ''
  const desc = forgeModal.value.description || ''
  const text = name + desc
  const picked = []
  for (const [kw, ts] of Object.entries(_ROLE_TOOLSET_HINTS)) {
    if (text.includes(kw)) {
      for (const t of ts) if (!picked.includes(t)) picked.push(t)
    }
  }
  // 只在技能列表里有的才勾（避免推荐了不存在的 toolset）
  const valid = new Set(allToolsets.value.map(t => t.name))
  forgeModal.value.toolsets = picked.filter(t => valid.has(t))
}

async function loadAgents(refresh = false) {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ type: 'agent' })
    if (refresh) params.set('refresh', 'true')
    const data = await api.get(`/v1/bricks?${params.toString()}`)
    agents.value = (data.bricks || []).map(a => ({ ...a, _key: a.id }))
  } catch (e) {
    console.error('Agent 发现加载失败', e)
    error.value = e && e.message ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return agents.value
  return agents.value.filter(a =>
    `${a.name || ''} ${a.description || ''} ${a.id || ''}`.toLowerCase().includes(q)
  )
})

const counts = computed(() => {
  let local = 0, remote = 0
  for (const a of agents.value) {
    if (a.source === 'remote') remote++
    else local++
  }
  return { local, remote }
})

function kindIcon(kind) {
  return ({ cli: '🖥️', app: '📦', mcp: '🔌', remote: '☁️', subprocess: '⚙️' })[kind] || '🤖'
}
function kindLabel(kind) {
  return ({ cli: 'CLI', app: 'App', mcp: 'MCP', remote: '远端', subprocess: '子进程' })[kind] || (kind || '未知')
}
function authLabel(scheme) {
  return ({ none: '无需鉴权', apikey: 'API Key', oauth: 'OAuth', local: '本地', bearer: 'Bearer' })[scheme] || (scheme || '—')
}
function sourceLabel(src) {
  // ⑭ 神魔堂产品语义（2026-09-04 董董定调）：远端=封神榜（市场投票/热度），本机=本机发现
  return src === 'remote' ? '🔥 封神榜' : '本机发现'
}

/* ── ⑭ 请神收尾 T4：登堂 ────────────────────────────────────────── */

// 可登堂的 ACP 食谱（后端下发，前端不硬编码）
const recipes = ref([])
// 每个 agent 的登堂状态：idle / loading / success / fail / need_auth
const ascendState = ref({})
// 鉴权弹窗
const authModal = ref({ open: false, key: '', recipe: null, authEnv: '', value: '', error: '' })

const recipeIndex = computed(() => {
  const m = new Map()
  for (const r of recipes.value) {
    if (r && r.name) m.set(String(r.name).toLowerCase(), r)
  }
  return m
})

/** agent 是否有匹配的 ACP 食谱（按 id → name 顺序匹配）。 */
function recipeFor(a) {
  const idx = recipeIndex.value
  return (
    idx.get(String(a.id || '').toLowerCase()) ||
    idx.get(String(a.name || '').toLowerCase()) ||
    null
  )
}

function stateOf(key) {
  return ascendState.value[key] || { status: 'idle', detail: '' }
}

function setState(key, status, detail = '') {
  ascendState.value = { ...ascendState.value, [key]: { status, detail } }
}

function ascendLabel(st) {
  return ({
    idle: '⛩️ 登堂',
    loading: '登堂中…',
    success: '✅ 已登堂',
    fail: '⚠️ 重试',
    need_auth: '🔑 配置鉴权',
  })[st.status] || '⛩️ 登堂'
}

function stateClass(st) {
  return ({
    success: 'text-emerald-600 dark:text-emerald-400',
    fail: 'text-red-500',
    need_auth: 'text-amber-600 dark:text-amber-400',
    loading: 'text-gray-400',
  })[st.status] || 'text-gray-400'
}

async function loadRecipes() {
  try {
    const data = await api.listAgentRecipes()
    recipes.value = (data && data.recipes) || []
  } catch (e) {
    console.error('ACP 食谱加载失败', e)
    recipes.value = []
  }
}

async function ascend(a, authValue = '') {
  const recipe = recipeFor(a)
  if (!recipe) {
    setState(a._key, 'fail', '暂无登堂食谱（无匹配 ACP recipe）')
    return
  }
  setState(a._key, 'loading', '')
  try {
    const data = await api.registerAgentProfile(recipe.name, authValue)
    if (!data || data.ok === false) {
      setState(a._key, 'fail', (data && data.error) || '注册失败')
      return
    }
    if (data.status === 'need_auth') {
      setState(a._key, 'need_auth', `需要 ${data.auth_env || 'API Key'}`)
      authModal.value = {
        open: true, key: a._key, recipe,
        authEnv: data.auth_env || '', value: '', error: '',
      }
      return
    }
    if (data.status === 'success') {
      setState(a._key, 'success', (data.health && data.health.detail) || '已登堂')
      return
    }
    if (data.status === 'fail') {
      setState(a._key, 'fail', (data.health && data.health.detail) || '健康检查未通过')
      return
    }
    setState(a._key, 'fail', (data && data.error) || '未知状态')
  } catch (e) {
    setState(a._key, 'fail', (e && e.message) ? e.message : String(e))
  }
}

async function submitAuth() {
  const m = authModal.value
  if (!m.value.trim()) {
    authModal.value = { ...m, error: '请输入 API Key' }
    return
  }
  const a = agents.value.find(x => x._key === m.key)
  authModal.value = { ...m, open: false, error: '' }
  if (a) await ascend(a, m.value.trim())
}

/* ── ⑭ 造神：原生 agent CRUD（per-agent 专属 API） ─────────────── */

async function loadNativeAgents() {
  try {
    const data = await api.listNativeAgents()
    nativeAgents.value = (data && data.agents) || []
  } catch (e) {
    console.error('原生 agent 加载失败', e)
    nativeAgents.value = []
  }
}

function openForge(editing = null) {
  forgeModal.value = editing
    ? {
        open: true, editing, error: '',
        name: editing.name || '', description: editing.description || '',
        provider: editing.provider || '', model: editing.model || '', customProvider: '',
        apiKey: '', systemPrompt: editing.system_prompt || '',
        hue: editing.hue || 0, toolsets: Array.isArray(editing.toolsets) ? [...editing.toolsets] : [],
      }
    : {
        open: true, editing: null, error: '',
        name: '', description: '', provider: '', model: '', customProvider: '',
        apiKey: '', systemPrompt: '', hue: 0, toolsets: [],
      }
}

async function submitForge() {
  const m = forgeModal.value
  if (!m.name.trim()) {
    forgeModal.value = { ...m, error: '请填写名称' }
    return
  }
  // 编辑时留空 apiKey = __KEEP__（保持原 key 不变）；造神留空 = 用全局
  const apiKey = m.apiKey.trim()
    ? m.apiKey.trim()
    : (m.editing ? '__KEEP__' : '')
  try {
    // 自定义厂商：用 customProvider 输入值
    const finalProvider = m.provider === '__custom__' ? (m.customProvider || '').trim() : m.provider
    const data = await api.upsertNativeAgent({
      id: m.editing ? m.editing.id : undefined,
      name: m.name.trim(),
      description: m.description,
      provider: finalProvider,
      model: m.model,
      api_key: apiKey,
      system_prompt: m.systemPrompt,
      hue: m.hue,
      toolsets: m.toolsets || [],
    })
    if (data && data.ok) {
      forgeModal.value = { ...forgeModal.value, open: false }
      toast(m.editing ? '已保存' : '造神成功', 'success')
      await loadNativeAgents()
    } else {
      forgeModal.value = { ...m, error: (data && data.error) || '保存失败' }
    }
  } catch (e) {
    forgeModal.value = { ...m, error: (e && e.message) || String(e) }
  }
}

onMounted(() => {
  loadAgents()
  loadRecipes()
  loadNativeAgents()
  loadModelProviders()
  loadToolsets()
})
</script>
