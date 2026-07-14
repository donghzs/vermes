<template>
  <div>
    <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
      <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">文献库</span>
      <div class="flex items-center gap-1">
        <button @click="$emit('import-bibtex')" class="p-1 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-400 hover:text-gray-600 text-[10px]" title="导入 BibTeX">📥</button>
        <button @click="$emit('toggle-source-selector')" class="p-1 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-400 hover:text-gray-600 text-[10px]" title="搜索源">
          {{ activeSources.length }}/{{ allSearchSources.length }} 源
        </button>
        <button @click="$emit('search')" :disabled="searchLoading" class="p-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-xs flex items-center gap-1">
          <span v-if="searchLoading" class="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
          <svg v-else class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
          检索
        </button>
      </div>
    </div>
    <!-- 搜索源选择器 -->
    <div v-if="showSourceSelector" class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-900 space-y-1">
      <div class="text-[10px] text-gray-400 mb-1">搜索源（{{ activeSources.length }}/{{ allSearchSources.length }}）</div>
      <label v-for="src in allSearchSources" :key="src.id"
        :class="['flex items-center gap-1.5 px-1.5 py-0.5 rounded text-[10px] cursor-pointer', src.paid && !src.configured ? 'opacity-50' : '']">
        <input type="checkbox" :value="src.id" v-model="localActiveSources"
          :disabled="src.paid && !src.configured"
          class="w-3 h-3 rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
        <span :class="src.paid ? 'text-amber-600' : 'text-gray-600 dark:text-gray-300'">
          {{ src.icon }} {{ src.label }}
          <span v-if="!src.accessible && !src.paid" class="text-[8px] text-red-400 ml-0.5">离线</span>
        </span>
        <button v-if="src.paid && !src.configured" @click.stop.prevent="$emit('open-paid-config', src)"
          class="ml-auto text-[9px] px-1.5 py-0.5 bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 rounded hover:bg-amber-200">
          配置
        </button>
        <span v-else-if="src.paid" class="ml-auto text-[9px] text-green-600">✓已配置</span>
      </label>
    </div>
    <!-- 付费源配置弹窗 -->
    <div v-if="showPaidSourceConfig" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="localShowPaidSourceConfig = false">
      <div class="bg-white dark:bg-gray-800 rounded-xl p-6 w-96 max-w-[90vw] shadow-2xl">
        <div class="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-1">
          {{ paidSourceConfigTarget?.icon }} 配置 {{ paidSourceConfigTarget?.label }}
        </div>
        <div class="text-xs text-gray-500 mb-3">{{ paidSourceConfigTarget?.paid ? '付费文献源，需填入 API Key' : '' }}</div>
        
        <!-- API Key -->
        <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">API Key</label>
        <input v-model="localPaidSourceApiKey" type="password" placeholder="输入 API Key..."
          class="w-full px-3 py-2 text-xs bg-gray-50 dark:bg-gray-900 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:border-amber-500 dark:text-gray-100 mb-3" />
        
        <!-- CNKI 网关 URL（仅 CNKI） -->
        <template v-if="paidSourceConfigTarget?.needsGatewayUrl">
          <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">网关 URL</label>
          <input v-model="localPaidSourceGatewayUrl" type="text" placeholder="https://your-cnki-gateway.com/api"
            class="w-full px-3 py-2 text-xs bg-gray-50 dark:bg-gray-900 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:border-amber-500 dark:text-gray-100 mb-3" />
          <div class="text-[10px] text-gray-400 mb-3">知网无公开API，需自建网关服务。可参考开源网关方案。</div>
        </template>
        
        <!-- 注册链接 -->
        <div v-if="paidSourceConfigTarget?.registerUrl" class="text-[10px] text-blue-500 mb-3">
          <a :href="paidSourceConfigTarget.registerUrl" target="_blank" class="hover:underline">📎 前往注册获取 API Key →</a>
        </div>
        
        <div class="flex gap-2 justify-end">
          <button @click="localShowPaidSourceConfig = false" class="px-3 py-1.5 text-xs text-gray-500 hover:text-gray-700 rounded-lg">取消</button>
          <button @click="$emit('save-paid-key')" :disabled="!localPaidSourceApiKey.trim()"
            class="px-4 py-1.5 bg-amber-600 hover:bg-amber-700 text-white rounded-lg text-xs font-medium disabled:opacity-40">激活</button>
        </div>
      </div>
    </div>
    <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700">
      <div class="relative">
        <input v-model="localLiteratureSearch" placeholder="语义搜索文献..." class="w-full pl-7 pr-7 py-1.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-xs focus:outline-none focus:border-blue-500 dark:text-gray-100"/>
        <svg class="w-3 h-3 absolute left-2 top-2 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
        <span v-if="semanticSearchLoading" class="absolute right-2 top-2 w-3 h-3 border-2 border-blue-300 border-t-blue-600 rounded-full animate-spin"></span>
        <button v-else-if="localLiteratureSearch" @click="localLiteratureSearch = ''" class="absolute right-2 top-2 text-gray-400 hover:text-gray-600 text-xs">✕</button>
      </div>
    </div>
    <!-- 标签筛选栏 -->
    <div v-if="allTags.length > 0" class="px-3 py-1.5 border-b border-gray-200 dark:border-gray-700 flex items-center gap-1 flex-wrap bg-gray-50 dark:bg-gray-900/50">
      <button @click="localFilterTag = ''" :class="['text-[10px] px-2 py-0.5 rounded-full transition-colors', !localFilterTag ? 'bg-gray-700 text-white' : 'bg-gray-200 dark:bg-gray-700 text-gray-500 hover:bg-gray-300']">全部</button>
      <button v-for="t in allTags" :key="t.tag" @click="localFilterTag = localFilterTag === t.tag ? '' : t.tag"
        :class="['text-[10px] px-2 py-0.5 rounded-full transition-colors', localFilterTag === t.tag ? 'bg-blue-600 text-white' : tagColor(t.tag) + ' hover:opacity-80']">
        {{ t.tag }} <span class="opacity-60">{{ t.count }}</span>
      </button>
    </div>
    <div class="flex-1 overflow-y-auto py-2">
      <div v-if="!literature.length" class="px-3 py-8 text-center">
        <div class="text-3xl mb-2">📭</div>
        <p class="text-xs text-gray-400 mb-2">文献库为空</p>
        <p class="text-[10px] text-gray-400 mb-3">运行「文献综述」Agent 或点击上方检索按钮</p>
        <button @click="$emit('search')" :disabled="searchLoading" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-xs flex items-center gap-1">
          <span v-if="searchLoading" class="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
          <span v-else>🔍</span> {{ searchLoading ? '搜索中...' : '开始检索' }}</button>
      </div>
      <div v-for="paper in filteredLiterature" :key="paper.id" 
        @click="$emit('toggle-paper-expand', paper)"
        :class="['px-3 py-2.5 cursor-pointer transition-all', expandedPaper?.id === paper.id ? 'bg-blue-50 dark:bg-blue-900/20' : 'hover:bg-white dark:hover:bg-gray-700/50 border-b border-gray-100 dark:border-gray-700/50', citedPaperIds.has(paper.id) ? 'border-l-2 border-l-green-400' : '']">
        <div class="flex items-start gap-2">
          <span class="text-[10px] px-1 py-0.5 rounded bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 shrink-0 mt-0.5 font-mono">{{ paper.year || '?' }}</span>
          <div class="flex-1 min-w-0">
            <div class="text-xs font-medium text-gray-800 dark:text-gray-200 line-clamp-2 leading-snug">{{ paper.title }}</div>
            <div class="text-[10px] text-gray-500 mt-1">{{ paper.authors?.slice(0, 3).join(', ') || '未知作者' }}{{ paper.authors?.length > 3 ? ' 等' : '' }}</div>
            <div class="flex items-center gap-2 mt-1.5">
              <span v-if="paper.venue" class="text-[10px] px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-gray-500 truncate max-w-[120px]">{{ paper.venue }}</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded text-gray-400" :class="sourceBadgeClass(paper.source)">{{ paper.source || '未知源' }}</span>
              <span v-if="citedPaperIds.has(paper.id)" class="text-[9px] px-1 py-0.5 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 rounded" title="已在正文中引用">✅ 已引用</span>
              <span v-for="tag in (literatureTags[paper.id] || []).slice(0, 2)" :key="tag"
                :class="['text-[8px] px-1 py-0.5 rounded-full', tagColor(tag)]">{{ tag }}</span>
              <span v-if="(literatureTags[paper.id] || []).length > 2" class="text-[8px] text-gray-400">+{{ (literatureTags[paper.id] || []).length - 2 }}</span>
              <button @click.stop="$emit('copy-bibtex', paper)" class="text-[10px] text-gray-400 hover:text-blue-600" title="复制 BibTeX">📋</button>
              <button @click.stop="$emit('insert-citation', paper)" class="text-[10px] text-blue-600 hover:text-blue-700">引用</button>
            </div>
          </div>
          <svg :class="['w-3 h-3 text-gray-400 mt-1 shrink-0 transition-transform', expandedPaper?.id === paper.id ? 'rotate-180' : '']" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
        </div>
        <!-- 展开摘要 + BibTeX -->
        <div v-if="expandedPaper?.id === paper.id" class="mt-2 pt-2 border-t border-gray-100 dark:border-gray-700/50">
          <!-- 标签管理 -->
          <div class="flex items-center gap-1 flex-wrap mb-2">
            <span v-for="tag in (literatureTags[paper.id] || [])" :key="tag"
              :class="['text-[9px] px-1.5 py-0.5 rounded-full flex items-center gap-0.5 group', tagColor(tag)]">
              {{ tag }}
              <button @click.stop="$emit('remove-tag', paper, tag)" class="opacity-0 group-hover:opacity-100 text-[8px] leading-none">✕</button>
            </span>
            <button v-if="!tagInputVisible[paper.id]" @click.stop="$emit('toggle-tag-input', paper.id, true)"
              class="text-[9px] px-1.5 py-0.5 rounded-full border border-dashed border-gray-300 dark:border-gray-600 text-gray-400 hover:text-gray-600">+ 标签</button>
            <div v-else class="flex items-center gap-1">
              <input v-model="localTagInput" @keyup.enter="onAddTag(paper)" @blur="onAddTag(paper)"
                placeholder="标签名" class="text-[10px] px-1.5 py-0.5 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 w-16" />
            </div>
          </div>
          <p v-if="paper.abstract" class="text-[10px] text-gray-500 dark:text-gray-400 leading-relaxed mb-2 max-h-24 overflow-y-auto">{{ paper.abstract }}</p>
          <div class="bg-gray-950 text-green-400 text-[10px] p-2 rounded font-mono relative group">
            <button @click.stop="$emit('copy-bibtex', paper)" class="absolute top-1 right-1 px-1.5 py-0.5 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-[9px] opacity-0 group-hover:opacity-100 transition-opacity">复制</button>
            <pre class="whitespace-pre-wrap" v-text="'@article{' + (paper.citeKey || 'ref') + ',\n  title={' + (paper.title || '') + '},\n  author={' + (paper.authors?.join(' and ') || '') + '},\n  year={' + (paper.year || '') + '},\n  journal={' + (paper.venue || '') + '}\n}'"></pre>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  literature: { type: Array, default: () => [] },
  searchLoading: { type: Boolean, default: false },
  activeSources: { type: Array, default: () => [] },
  allSearchSources: { type: Array, default: () => [] },
  literatureSearch: { type: String, default: '' },
  filteredLiterature: { type: Array, default: () => [] },
  expandedPaper: { type: Object, default: null },
  citedPaperIds: { type: Set, default: () => new Set() },
  showSourceSelector: { type: Boolean, default: false },
  showPaidSourceConfig: { type: Boolean, default: false },
  paidSourceConfigTarget: { type: Object, default: null },
  paidSourceApiKey: { type: String, default: '' },
  paidSourceGatewayUrl: { type: String, default: '' },
  // 语义搜索
  semanticSearchLoading: { type: Boolean, default: false },
  // 标签系统
  literatureTags: { type: Object, default: () => ({}) },
  allTags: { type: Array, default: () => [] },
  filterTag: { type: String, default: '' },
  tagInputVisible: { type: Object, default: () => ({}) },
  tagInputValue: { type: Object, default: () => ({}) },
})

const emit = defineEmits([
  'search', 'import-bibtex', 'toggle-source-selector', 'open-paid-config',
  'save-paid-key', 'toggle-paper-expand', 'copy-bibtex', 'insert-citation',
  'source-badge-class',
  'update:literatureSearch', 'update:activeSources', 'update:showPaidSourceConfig',
  'update:paidSourceApiKey', 'update:paidSourceGatewayUrl',
  'update:filterTag',
  'add-tag', 'remove-tag', 'toggle-tag-input',
])

// Local mutable mirrors for v-model in template
const localLiteratureSearch = ref(props.literatureSearch)
const localActiveSources = ref([...props.activeSources])
const localShowPaidSourceConfig = ref(props.showPaidSourceConfig)
const localPaidSourceApiKey = ref(props.paidSourceApiKey)
const localPaidSourceGatewayUrl = ref(props.paidSourceGatewayUrl)
const localFilterTag = ref(props.filterTag)

watch(localLiteratureSearch, v => emit('update:literatureSearch', v))
watch(localActiveSources, v => emit('update:activeSources', v), { deep: true })
watch(localShowPaidSourceConfig, v => emit('update:showPaidSourceConfig', v))
watch(localPaidSourceApiKey, v => emit('update:paidSourceApiKey', v))
watch(localPaidSourceGatewayUrl, v => emit('update:paidSourceGatewayUrl', v))
watch(localFilterTag, v => emit('update:filterTag', v))

// Keep local mirrors in sync when parent changes the prop
watch(() => props.literatureSearch, v => { if (v !== localLiteratureSearch.value) localLiteratureSearch.value = v })
watch(() => props.activeSources, v => { localActiveSources.value = [...v] })
watch(() => props.showPaidSourceConfig, v => { localShowPaidSourceConfig.value = v })
watch(() => props.paidSourceApiKey, v => { localPaidSourceApiKey.value = v })
watch(() => props.paidSourceGatewayUrl, v => { localPaidSourceGatewayUrl.value = v })
watch(() => props.filterTag, v => { localFilterTag.value = v })

const localTagInput = ref('')

const onAddTag = (paper) => {
  const tag = localTagInput.value.trim()
  if (tag) {
    emit('add-tag', paper, tag)
    localTagInput.value = ''
  }
  emit('toggle-tag-input', paper.id, false)
}

const tagColors = ['bg-blue-100 dark:bg-blue-900/30 text-blue-600', 'bg-green-100 dark:bg-green-900/30 text-green-600', 'bg-purple-100 dark:bg-purple-900/30 text-purple-600', 'bg-amber-100 dark:bg-amber-900/30 text-amber-600', 'bg-pink-100 dark:bg-pink-900/30 text-pink-600']
const tagColor = (tag) => {
  let hash = 0
  for (let i = 0; i < tag.length; i++) hash = ((hash << 5) - hash + tag.charCodeAt(i)) | 0
  return tagColors[Math.abs(hash) % tagColors.length]
}

const sourceBadgeClass = (source) => {
  const m = {
    arxiv: 'bg-red-100 dark:bg-red-900/20 text-red-600',
    crossref: 'bg-purple-100 dark:bg-purple-900/20 text-purple-600',
    semantic_scholar: 'bg-amber-100 dark:bg-amber-900/20 text-amber-600',
    doaj: 'bg-green-100 dark:bg-green-900/20 text-green-600',
    pubmed: 'bg-cyan-100 dark:bg-cyan-900/20 text-cyan-600',
  }
  return m[source] || 'bg-gray-100 dark:bg-gray-700 text-gray-500'
}
</script>
