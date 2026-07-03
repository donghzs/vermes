<!--
  ScholarForge Pro — 专业论文写作界面
  融合 Overleaf 双栏预览 + Scrivener 大纲导航 + Zotero 文献管理
  目标用户：本科/研究生/博士/研究人员
-->
<template>
  <div class="h-full flex flex-col overflow-hidden bg-white dark:bg-gray-900"
    @keydown="handleKeyboard"
    role="application" aria-label="ScholarForge 论文写作编辑器">
    <!-- ═══════════════════════════════════════════════════════════════
         顶部导航栏 — 项目级操作
         ═══════════════════════════════════════════════════════════════ -->
    <header class="h-12 border-b border-gray-200 dark:border-gray-700 flex items-center px-3 bg-white dark:bg-gray-800 shrink-0 gap-2">
      <!-- 左侧：项目信息 -->
      <div class="flex items-center gap-2 min-w-0 flex-shrink">
        <button @click="$router.push('/')" class="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg text-gray-500" aria-label="返回首页" title="返回首页">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"/></svg>
        </button>
        <div class="h-5 w-px bg-gray-200 dark:bg-gray-600"></div>
        <div class="flex items-center gap-2 min-w-0">
          <span class="text-lg shrink-0">📚</span>
          <div class="relative" ref="projectSwitcherRef">
            <button @click="showProjectSwitcher = !showProjectSwitcher"
              class="flex items-center gap-1.5 px-2 py-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg text-sm font-medium text-gray-800 dark:text-gray-100 min-w-0"
              aria-label="切换项目" aria-haspopup="listbox" :aria-expanded="showProjectSwitcher">
              <span class="max-w-[240px] truncate">{{ project.title || '未命名项目' }}</span>
              <svg class="w-3 h-3 text-gray-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
            </button>
            <!-- 项目下拉 -->
            <div v-if="showProjectSwitcher" class="absolute left-0 top-full mt-1 w-80 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl z-50 py-1 max-h-96 overflow-y-auto" role="listbox" aria-label="项目列表">
              <div class="px-3 py-2 text-xs text-gray-500 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
                <span>论文项目 ({{ projects.length }})</span>
                <button @click="showProjectSwitcher=false; backToProjectList()" class="text-blue-600 hover:text-blue-700">全部项目</button>
              </div>
              <div v-if="!projects.length" class="px-3 py-4 text-xs text-gray-400 text-center">还没有项目</div>
              <div v-for="p in projects" :key="p.id" @click="switchProject(p)"
                :class="['px-3 py-2 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 flex items-start gap-2', project.id === p.id ? 'bg-green-50 dark:bg-green-900/20' : '']">
                <span class="text-sm shrink-0 mt-0.5">{{ typeIcon(p.paper_type) }}</span>
                <div class="flex-1 min-w-0">
                  <div class="text-sm font-medium text-gray-800 dark:text-gray-200 truncate">{{ p.title }}</div>
                  <div class="text-[10px] text-gray-500 mt-0.5">
                    {{ p.paper_type }} · {{ p.total_words || 0 }}字 · {{ p.literature_count || 0 }}文献 · {{ formatRelativeTime(p.updated_at) }}
                  </div>
                </div>
                <button @click.stop="deleteProject(p)" class="text-gray-300 hover:text-red-500 shrink-0" title="删除项目">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M1 7h22M9 7V5a2 2 0 012-2h2a2 2 0 012 2v2"/></svg>
                </button>
              </div>
              <div class="border-t border-gray-100 dark:border-gray-700 px-2 py-1">
                <button @click="showProjectSwitcher=false; backToProjectList()"
                  class="w-full px-3 py-2 text-left text-sm text-green-600 hover:bg-green-50 dark:hover:bg-green-900/20 rounded flex items-center gap-2">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                  新建论文项目
                </button>
              </div>
            </div>
          </div>
        </div>
        <span class="text-xs px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-700 text-gray-500 ml-2 shrink-0 hidden sm:inline">{{ project.paper_type || project.type || '论文' }}</span>
      </div>

      <!-- 中间：写作阶段 + 模型选择器 → 折叠下拉 -->
      <div class="hidden md:block relative stage-dropdown-anchor flex-shrink-0">
        <button @click.stop="showStageDropdown = !showStageDropdown"
          class="flex items-center gap-1.5 px-2.5 py-1.5 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-lg text-[11px] transition-colors" title="写作阶段与模型设置">
          <span class="w-5 h-5 rounded bg-green-600 text-white flex items-center justify-center text-[10px] font-medium">
            {{ stages.findIndex(s=>s.id===activeStage) + 1 || 1 }}
          </span>
          <span class="hidden lg:inline text-gray-600 dark:text-gray-300 font-medium">{{ stages.find(s=>s.id===activeStage)?.label || '写作' }}</span>
          <span class="text-[10px] text-gray-400">{{ agentProviders[activeStage] ? agentProviderLabel(activeStage) : '未选模型' }}</span>
          <svg :class="['w-3 h-3 text-gray-400 transition-transform', showStageDropdown ? 'rotate-180' : '']" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
        </button>
        
        <!-- 阶段 + 模型下拉面板 -->
        <div v-if="showStageDropdown"
          class="absolute top-full left-0 mt-1 w-[260px] bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl z-50 max-h-[420px] flex flex-col overflow-hidden">
          <div class="px-3 py-2 text-[10px] text-gray-400 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
            <span>写作阶段 · 模型设置</span>
            <button @click="showStageDropdown = false" class="text-gray-400 hover:text-gray-600">✕</button>
          </div>
          <div class="flex-1 overflow-y-auto py-1">
            <div v-for="(stage, idx) in stages" :key="stage.id"
              :class="['px-3 py-2 flex items-center gap-2 transition-colors', activeStage === stage.id ? 'bg-green-50 dark:bg-green-900/20' : 'hover:bg-gray-50 dark:hover:bg-gray-700/50']">
              <!-- 阶段选择按钮 -->
              <button @click="activeStage = stage.id"
                :class="['w-6 h-6 rounded flex items-center justify-center text-[10px] font-medium shrink-0 transition-colors', activeStage === stage.id ? 'bg-green-600 text-white' : 'bg-gray-100 dark:bg-gray-700 text-gray-500 hover:bg-gray-200']">
                {{ idx + 1 }}
              </button>
              <div class="flex-1 min-w-0">
                <div class="text-[11px] font-medium text-gray-700 dark:text-gray-200">{{ stage.icon }} {{ stage.label }}</div>
                <div class="text-[9px] text-gray-400 truncate">{{ stage.description }}</div>
              </div>
              <!-- 该阶段模型选择 (必须有 agent-dropdown-anchor 类，让 handleClickOutside 不误关) -->
              <div class="relative shrink-0 agent-dropdown-anchor">
                <button @click.stop="toggleAgentDropdown(stage.id, $event)"
                  class="text-[10px] px-2 py-1 bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded hover:border-green-400 transition-colors max-w-[90px] truncate">
                  {{ agentProviders[stage.id] ? agentProviderLabel(stage.id) : '选' }}
                </button>
                <div v-if="openAgentDropdown === stage.id"
                  class="absolute right-0 top-full mt-1 w-[180px] bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl z-[60] max-h-[240px] flex flex-col overflow-hidden">
                  <div class="flex-1 overflow-y-auto py-0.5">
                    <div v-if="!configuredProviders.length" class="text-[10px] text-gray-400 py-4 px-3 text-center">暂无模型</div>
                    <template v-for="p in configuredProviders" :key="p.key">
                      <div class="px-2 pt-1.5 pb-0.5 text-[8px] uppercase text-gray-400 font-semibold">{{ p.key }}</div>
                      <button v-for="m in getModelsForProvider(p.key)" :key="p.key + '-' + m"
                        @click="setAgentProvider(stage.id, p.key, m); closeAgentDropdown()"
                        :class="['w-full text-left px-2.5 py-1 text-[10px] transition-colors', agentProviders[stage.id]?.provider === p.key && agentProviders[stage.id]?.model === m ? 'bg-green-50 text-green-700 font-medium' : 'text-gray-600 hover:bg-gray-100']">
                        {{ m }}
                      </button>
                    </template>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧：精简工具栏（P1: 9按钮→4按钮） -->
      <div class="flex items-center gap-1 flex-shrink-0 relative" ref="moreToolsRef">
        <!-- 文献库 -->
        <button @click="panelStore.openLiterature()" 
          :class="['p-2 rounded-lg transition-colors relative', showLiteraturePanel ? 'bg-blue-50 text-blue-600' : 'text-gray-500 hover:bg-gray-100']"
          title="文献库" aria-label="文献库" :aria-expanded="showLiteraturePanel">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/></svg>
          <span v-if="literatureCount > 0" class="absolute -top-0.5 -right-0.5 w-4 h-4 bg-blue-600 text-white text-[9px] rounded-full flex items-center justify-center">{{ literatureCount }}</span>
        </button>
        
        <!-- AI 助手 -->
        <button @click="panelStore.openAI()"
          :class="['p-2 rounded-lg transition-colors relative', showAIPanel ? 'bg-purple-50 text-purple-600' : 'text-gray-500 hover:bg-gray-100']"
          title="AI 助手" aria-label="AI 助手" :aria-expanded="showAIPanel">
          <span class="text-base">🤖</span>
        </button>

        <!-- 更多工具（下拉菜单） -->
        <button @click.stop="showMoreToolsDropdown = !showMoreToolsDropdown"
          :class="['p-2 rounded-lg transition-colors flex items-center', showMoreToolsDropdown ? 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200' : 'text-gray-500 hover:bg-gray-100']"
          title="更多工具" aria-label="更多工具" :aria-expanded="showMoreToolsDropdown">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14M5 6h14M5 18h14"/></svg>
          <!-- 徽章：有分析结果时显示红点 -->
          <span v-if="citationErrors > 0 || (plagResult && plagResult.overall_similarity > 0.3)" class="absolute -top-0.5 -right-0.5 w-2 h-2 bg-red-500 rounded-full"></span>
        </button>

        <!-- 更多工具下拉菜单 -->
        <div v-if="showMoreToolsDropdown" class="absolute right-0 top-full mt-1 w-56 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl z-[80] py-1">
          <!-- 引用核查 -->
          <button @click="showMoreToolsDropdown = false; toggleRightPanel('citation')"
            :class="['w-full px-3 py-2 flex items-center gap-2.5 text-sm transition-colors', activeRightPanel === 'citation' ? 'bg-amber-50 text-amber-600' : 'text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50']">
            <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            <span class="flex-1 text-left">引用核查</span>
            <span v-if="citationErrors + citationWarnings > 0" 
              :class="['px-1.5 text-white text-[9px] rounded-full', citationErrors > 0 ? 'bg-red-500' : 'bg-amber-500']">
              {{ citationErrors + citationWarnings }}
            </span>
          </button>

          <!-- 共识度分析 -->
          <button @click="showMoreToolsDropdown = false; toggleRightPanel('consensus')"
            :class="['w-full px-3 py-2 flex items-center gap-2.5 text-sm transition-colors', activeRightPanel === 'consensus' ? 'bg-green-50 text-green-600' : 'text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50']">
            <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
            <span class="flex-1 text-left">共识度分析</span>
            <span v-if="consensusResults.length > 0" class="px-1.5 bg-green-500 text-white text-[9px] rounded-full">{{ consensusResults.length }}</span>
          </button>

          <!-- 查重 + AIGC -->
          <button @click="showMoreToolsDropdown = false; toggleRightPanel('plag')" :disabled="plagLoading"
            :class="['w-full px-3 py-2 flex items-center gap-2.5 text-sm transition-colors disabled:opacity-50', activeRightPanel === 'plag' ? 'bg-purple-50 text-purple-600' : 'text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50']">
            <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"/></svg>
            <span class="flex-1 text-left">查重 + AIGC</span>
            <span v-if="plagResult" 
              :class="['px-1.5 text-white text-[9px] rounded-full', plagResult.overall_similarity > 0.3 ? 'bg-red-500' : plagResult.aigc_overall_ratio > 0.4 ? 'bg-amber-500' : 'bg-green-500']">
              {{ Math.round(Math.max(plagResult.overall_similarity, plagResult.aigc_overall_ratio) * 100) }}%
            </span>
          </button>

          <!-- 论文评分 -->
          <button @click="showMoreToolsDropdown = false; toggleRightPanel('score')" :disabled="scoreLoading"
            :class="['w-full px-3 py-2 flex items-center gap-2.5 text-sm transition-colors disabled:opacity-50', activeRightPanel === 'score' ? 'bg-yellow-50 text-yellow-600' : 'text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50']">
            <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z"/></svg>
            <span class="flex-1 text-left">论文评分</span>
            <span v-if="scoreResult?.overall" 
              :class="['px-1.5 text-white text-[9px] rounded-full', scoreResult.overall >= 7 ? 'bg-green-500' : scoreResult.overall >= 5 ? 'bg-amber-500' : 'bg-red-500']">
              {{ scoreResult.overall }}
            </span>
          </button>

          <div class="border-t border-gray-100 dark:border-gray-700 my-1"></div>

          <!-- 复制富文本 -->
          <button @click="showMoreToolsDropdown = false; copyRichText()"
            class="w-full px-3 py-2 flex items-center gap-2.5 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
            <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
            <span class="flex-1 text-left">复制富文本</span>
            <span class="text-[10px] text-gray-400">Ctrl+Shift+C</span>
          </button>

          <!-- 版本历史 -->
          <button @click="showMoreToolsDropdown = false; toggleRightPanel('snapshots'); loadSnapshots()"
            :class="['w-full px-3 py-2 flex items-center gap-2.5 text-sm transition-colors', activeRightPanel === 'snapshots' ? 'bg-violet-50 text-violet-600' : 'text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50']">
            <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            <span class="flex-1 text-left">版本历史</span>
          </button>
        </div>

        <div class="h-4 w-px bg-gray-200 dark:bg-gray-600 mx-0.5"></div>

        <!-- 导出 -->
        <button @click="showExportPanel = true" class="p-2 lg:px-2.5 lg:py-1.5 bg-green-600 hover:bg-green-700 text-white rounded-lg transition-colors flex items-center justify-center shrink-0" title="导出论文 (Ctrl+E)" aria-label="导出论文">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
          <span class="hidden lg:inline ml-1 text-xs font-medium">导出</span>
        </button>
      </div>
    </header>

    <!-- ═══════════════════════════════════════════════════════════════
         项目列表页（无选中项目时显示）
         ═══════════════════════════════════════════════════════════════ -->
    <ProjectList v-if="!hasProject" @select-project="onProjectSelected" />

    <!-- ═══════════════════════════════════════════════════════════════
         主体三栏布局
         ═══════════════════════════════════════════════════════════════ -->
    <div v-else class="flex-1 flex overflow-hidden relative">
      
      <!-- ┌─────────────────────────────────────────────────────────────┐
           │ 左栏：大纲导航 (可折叠)
           └─────────────────────────────────────────────────────────────┘ -->
      <div :class="['border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 flex flex-col shrink-0 transition-all duration-200', leftCollapsed ? 'w-10' : 'w-64']">
        <!-- 折叠按钮 -->
        <button @click="toggleLeftBar" class="h-8 border-b border-gray-200 dark:border-gray-700 flex items-center justify-center text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors" title="大纲">
          <svg :class="['w-4 h-4 transition-transform', leftCollapsed ? 'rotate-180' : '']" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" width="18" height="18" stroke-width="2" d="M15 19l-7-7 7-7"/></svg>
        </button>
        <template v-if="!leftCollapsed">
          <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">论文结构</span>
          <button @click="addSection" class="p-1 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-400 hover:text-gray-600">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
          </button>
        </div>
        <div class="flex-1 overflow-y-auto py-2">
          <div class="space-y-0.5">
            <div v-for="(section, idx) in outline" :key="section.id"
              :class="[
                'px-3 py-2 text-sm transition-colors border-l-2 group',
                activeSection === section.id 
                  ? 'bg-white dark:bg-gray-700 border-green-500 text-gray-900 dark:text-gray-100' 
                  : 'border-transparent text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700/50'
              ]"
            >
              <!-- 查看模式 -->
              <div v-if="editingSectionId !== section.id" @click="activeSection = section.id" class="cursor-pointer">
                <div class="flex items-center gap-2">
                  <span class="text-xs text-gray-400 font-mono">{{ section.number }}</span>
                  <span class="truncate flex-1">{{ section.title || '未命名章节' }}</span>
                  <span v-if="section.wordCount" class="text-[10px] text-gray-400">{{ section.wordCount }}字</span>
                  <!-- 悬浮操作按钮 -->
                  <button @click.stop="startEditSection(section)" class="hidden group-hover:inline-block p-0.5 hover:bg-gray-200 dark:hover:bg-gray-600 rounded text-gray-400" title="重命名">
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
                  </button>
                  <button @click.stop="rewriteSection(section.id)" class="hidden group-hover:inline-block p-0.5 hover:bg-blue-100 dark:hover:bg-blue-900/30 rounded text-gray-400 hover:text-blue-500" title="AI 修改本章">
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                  </button>
                  <button @click.stop="deleteSection(section.id)" class="hidden group-hover:inline-block p-0.5 hover:bg-red-100 dark:hover:bg-red-900/30 rounded text-gray-400 hover:text-red-500" title="删除">
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M1 7h22"/></svg>
                  </button>
                </div>
                <div v-if="section.status === 'writing'" class="mt-1 flex items-center gap-1">
                  <span class="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></span>
                  <span class="text-[10px] text-green-600">AI 写作中...</span>
                </div>
                <div v-else-if="section.status === 'completed'" class="mt-1 flex items-center gap-1">
                  <span class="text-[10px] text-gray-400">✓ 已完成</span>
                </div>
              </div>
              <!-- 编辑模式 -->
              <div v-else class="flex flex-col gap-1">
                <input v-model="editSectionNumber" placeholder="编号"
                  class="w-12 px-1 py-0.5 text-xs font-mono bg-gray-100 dark:bg-gray-900 border border-gray-300 dark:border-gray-600 rounded focus:outline-none focus:border-green-500 dark:text-gray-100"
                  @keydown.enter="saveEditSection(section)" @keydown.escape="cancelEditSection" />
                <input v-model="editSectionTitle" placeholder="章节标题" ref="sectionTitleInput"
                  class="w-full px-1.5 py-0.5 text-xs bg-gray-100 dark:bg-gray-900 border border-gray-300 dark:border-gray-600 rounded focus:outline-none focus:border-green-500 dark:text-gray-100"
                  @keydown.enter="saveEditSection(section)" @keydown.escape="cancelEditSection" />
                <div class="flex gap-1">
                  <button @click="saveEditSection(section)" class="text-[10px] px-1.5 py-0.5 bg-green-600 text-white rounded">保存</button>
                  <button @click="cancelEditSection" class="text-[10px] px-1.5 py-0.5 bg-gray-300 dark:bg-gray-600 text-gray-600 dark:text-gray-300 rounded">取消</button>
                </div>
              </div>
            </div>
          </div>
        </div>
        
        <!-- 字数统计 -->
        <div class="px-3 py-2 border-t border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-700/50">
          <div class="flex justify-between text-xs text-gray-500 mb-1">
            <span>总字数</span>
            <span class="font-medium text-gray-700 dark:text-gray-300">{{ totalWordCount.toLocaleString() }}</span>
          </div>
          <div class="w-full h-1.5 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden">
            <div class="h-full bg-green-500 rounded-full transition-all" :style="{ width: Math.min((totalWordCount / project.targetWords) * 100, 100) + '%' }"></div>
          </div>
          <div class="flex justify-between text-[10px] text-gray-400 mt-1">
            <span>目标: {{ project.targetWords.toLocaleString() }} 字</span>
            <span>{{ Math.round((totalWordCount / project.targetWords) * 100) }}%</span>
          </div>
        </div>
        </template>
      </div>

      <!-- ┌─────────────────────────────────────────────────────────────┐
           │ 中栏：编辑器 (Overleaf 双栏风格)
           └─────────────────────────────────────────────────────────────┘ -->
      <main class="flex-1 flex flex-col min-w-0 bg-white dark:bg-gray-900">
        <!-- 编辑器工具栏 -->
        <div class="h-10 border-b border-gray-200 dark:border-gray-700 flex items-center px-3 gap-1 bg-gray-50 dark:bg-gray-800 shrink-0">
          <div class="flex items-center gap-0.5">
            <button @click="editor.format('bold')" class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-600 dark:text-gray-300" title="粗体 (Ctrl+B)" aria-label="粗体">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 12h8a4 4 0 100-8H6v8zm0 0h10a4 4 0 110 8H6v-8z"/></svg>
            </button>
            <button @click="editor.format('italic')" class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-600 dark:text-gray-300" title="斜体 (Ctrl+I)" aria-label="斜体">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>
            </button>
            <button @click="editor.format('heading')" class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-600 dark:text-gray-300" title="标题 (Ctrl+H)" aria-label="标题">
              <span class="text-xs font-bold">H</span>
            </button>
          </div>
          <div class="h-4 w-px bg-gray-300 dark:bg-gray-600 mx-1"></div>
          <div class="flex items-center gap-0.5">
            <button @click="insertCitation" class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-600 dark:text-gray-300 flex items-center gap-1" title="插入引用 (Ctrl+K)" aria-label="插入引用">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"/></svg>
              <span class="text-xs">引用</span>
            </button>
            <button @click="insertFormula" class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-600 dark:text-gray-300" title="插入公式 (Ctrl+Shift+F)" aria-label="插入公式">
              <span class="text-xs font-serif italic">∑</span>
            </button>
            <button @click="insertTable" class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-600 dark:text-gray-300" title="插入表格 (Ctrl+T)" aria-label="插入表格">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
            </button>
          </div>
          <div class="flex-1"></div>
          <div class="flex items-center gap-2">
            <button @click="viewMode = 'edit'" :class="['px-2 py-1 rounded text-xs', viewMode === 'edit' ? 'bg-white dark:bg-gray-700 shadow-sm text-gray-800 dark:text-gray-100' : 'text-gray-500 hover:text-gray-700']" aria-label="编辑视图" aria-pressed="viewMode === 'edit'">编辑</button>
            <button @click="viewMode = 'split'" :class="['px-2 py-1 rounded text-xs', viewMode === 'split' ? 'bg-white dark:bg-gray-700 shadow-sm text-gray-800 dark:text-gray-100' : 'text-gray-500 hover:text-gray-700']" aria-label="分栏视图" aria-pressed="viewMode === 'split'">分栏</button>
            <button @click="viewMode = 'preview'" :class="['px-2 py-1 rounded text-xs', viewMode === 'preview' ? 'bg-white dark:bg-gray-700 shadow-sm text-gray-800 dark:text-gray-100' : 'text-gray-500 hover:text-gray-700']" aria-label="预览视图" aria-pressed="viewMode === 'preview'">预览</button>
          </div>
        </div>

        <!-- 编辑器主体 -->
        <div class="flex-1 flex overflow-hidden min-w-0">
          <!-- 编辑区 -->
          <div :class="['flex flex-col min-w-[360px] min-w-0', viewMode === 'split' ? 'flex-1' : viewMode === 'edit' ? 'w-full' : 'hidden']">
            <div class="flex items-center gap-2 px-3 py-1.5 bg-gray-100 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 text-xs">
              <button @click="pasteAndParse" class="flex items-center gap-1 px-2 py-0.5 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 rounded hover:bg-green-200 transition-colors">
                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"/></svg>
                粘贴并识别结构
              </button>
              <span class="text-gray-400">支持自动识别：标题/章节/参考文献</span>
              <div class="flex-1"></div>
              <span class="text-[10px] flex items-center gap-1" :class="saveStatus === 'saved' ? 'text-green-600' : saveStatus === 'saving' ? 'text-amber-500' : 'text-gray-400'">
                <span v-if="saveStatus === 'saved'" class="w-1.5 h-1.5 bg-green-500 rounded-full"></span>
                <span v-else-if="saveStatus === 'saving'" class="w-1.5 h-1.5 bg-amber-500 rounded-full animate-pulse"></span>
                <span v-else class="w-1.5 h-1.5 bg-gray-400 rounded-full"></span>
                {{ saveStatus === 'saved' ? '已保存' : saveStatus === 'saving' ? '保存中...' : '未保存' }}
              </span>
            </div>
            <div class="flex-1 relative">
            <textarea
              ref="editorRef"
              v-model="currentContent"
              @input="onEditorInput"
              @mouseup="onTextSelect"
              @keyup="onTextSelect"
              class="w-full h-full p-4 resize-none outline-none font-mono text-sm leading-relaxed text-gray-800 dark:text-gray-200 bg-white dark:bg-gray-900"
              placeholder="开始写作...选中文字可 AI 改写"
              spellcheck="false"
              aria-label="论文正文编辑器"
              role="textbox"
              aria-multiline="true"
            ></textarea>
            <!-- 写作字数进度条 -->
            <div v-if="project.targetWords" class="absolute bottom-2 left-4 right-4 flex items-center gap-2 bg-gray-900/80 backdrop-blur rounded-lg px-3 py-1.5 text-[10px]">
              <span class="text-gray-400 shrink-0">字数:</span>
              <span class="font-mono font-medium" :class="totalWordCount >= project.targetWords ? 'text-green-400' : totalWordCount >= project.targetWords * 0.6 ? 'text-amber-400' : 'text-gray-200'">{{ totalWordCount.toLocaleString() }}</span>
              <span class="text-gray-500">/ {{ project.targetWords.toLocaleString() }}</span>
              <div class="flex-1 h-1 bg-gray-700 rounded-full overflow-hidden">
                <div class="h-full rounded-full transition-all duration-300" :class="totalWordCount >= project.targetWords ? 'bg-green-400' : totalWordCount >= project.targetWords * 0.6 ? 'bg-amber-400' : 'bg-blue-400'" :style="{ width: Math.min((totalWordCount / project.targetWords) * 100, 100) + '%' }"></div>
              </div>
              <span class="text-gray-400 font-mono">{{ Math.round(Math.min((totalWordCount / project.targetWords) * 100, 100)) }}%</span>
            </div>
            <!-- 浮动选择菜单 (学 Jenni AI) -->
            <div v-if="showSelectionMenu" :style="selectionMenuStyle"
              class="absolute z-50 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600 rounded-lg shadow-xl py-1 flex items-stretch">
              <button @mousedown.prevent="inlineEdit('polish')" class="px-2.5 py-1.5 text-xs text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-1 whitespace-nowrap">✨ 润色</button>
              <button @mousedown.prevent="inlineEdit('rewrite')" class="px-2.5 py-1.5 text-xs text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-1 whitespace-nowrap">🔄 改写</button>
              <button @mousedown.prevent="inlineEdit('expand')" class="px-2.5 py-1.5 text-xs text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-1 whitespace-nowrap">📖 扩写</button>
              <button @mousedown.prevent="inlineEdit('shorten')" class="px-2.5 py-1.5 text-xs text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-1 whitespace-nowrap">✂️ 缩写</button>
              <button @mousedown.prevent="inlineEdit('translate-en')" class="px-2.5 py-1.5 text-xs text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-1 whitespace-nowrap">🌐 译英</button>
            </div>
            </div>
          </div>
          
          <!-- 预览区 -->
          <div v-if="viewMode !== 'edit'" :class="['border-l border-gray-200 dark:border-gray-700 flex flex-col bg-white dark:bg-gray-900 min-w-[360px]', viewMode === 'split' ? 'flex-1' : 'w-full']">
            <div class="flex-1 overflow-y-auto p-6">
              <div class="max-w-3xl mx-auto prose prose-sm dark:prose-invert" v-html="renderedContent"></div>
            </div>
          </div>
        </div>

        <!-- 底部 AI 输入栏 -->
        <div class="border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 p-3 shrink-0">
          <div class="max-w-4xl mx-auto flex items-end gap-2">
            <div class="flex-1 relative">
              <textarea
                v-model="aiInput"
                @keydown.enter.exact.prevent="sendToAI"
                rows="2"
                class="w-full px-4 py-2.5 pr-10 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl text-sm resize-none focus:outline-none focus:border-green-500 focus:ring-2 focus:ring-green-500/20 dark:text-gray-100"
                :placeholder="aiPlaceholder"
              ></textarea>
              <button @click="showAICommands = !showAICommands" class="absolute right-2 top-2 p-1 text-gray-400 hover:text-gray-600">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
              </button>
            </div>
            <div class="flex flex-col items-end gap-2">
            <button 
              @click="sendToAI" 
              :disabled="!aiInput.trim() || aiStreaming"
              class="px-5 py-2.5 bg-green-600 hover:bg-green-700 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl text-sm font-medium flex items-center gap-2 transition-all"
            >
              <span v-if="aiStreaming" class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
              <span v-else>🚀</span>
              {{ aiStreaming ? '生成中...' : 'AI 写作' }}
            </button>
            <button v-if="aiStreaming" @click="abortStreaming"
              class="px-4 py-1.5 bg-red-100 hover:bg-red-200 text-red-600 rounded-lg text-xs font-medium transition-colors"
              aria-label="停止生成">
              ⏹ 停止
            </button>
          </div>
          </div>
          
          <!-- AI 快捷指令 -->
          <div v-if="showAICommands" class="max-w-4xl mx-auto mt-2 flex flex-wrap gap-2">
            <button v-for="cmd in aiCommands" :key="cmd.id" @click="runAICommand(cmd)" 
              class="px-3 py-1.5 bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-xs text-gray-600 dark:text-gray-300 hover:border-green-500 hover:text-green-600 transition-colors">
              {{ cmd.icon }} {{ cmd.name }}
            </button>
          </div>
        </div>
      </main>

      <!-- ┌─────────────────────────────────────────────────────────────┐
           │ 右栏：浮出式面板（P1: 从固定 320px 改为浮出 480px）
           └─────────────────────────────────────────────────────────────┘ -->
      <!-- 遮罩层 -->
      <div v-if="!rightCollapsed" @click="panelStore.closeFloatingPanel()"
        class="absolute inset-0 bg-black/20 z-30 transition-opacity"></div>
      <!-- 浮出面板 -->
      <div v-if="!rightCollapsed"
        class="absolute right-0 top-0 bottom-0 w-full max-w-[480px] border-l border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 flex flex-col shrink-0 z-40 shadow-2xl">
        <!-- 面板顶部栏 -->
        <div class="h-10 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between px-3 bg-white dark:bg-gray-800 shrink-0">
          <span class="text-xs font-semibold text-gray-600 dark:text-gray-300">{{ floatingPanelTitle }}</span>
          <button @click="panelStore.closeFloatingPanel()" class="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded text-gray-400 hover:text-gray-600" title="关闭面板">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        

        <!-- 事件日志条（可折叠） -->
        <div v-if="events.length" class="border-b border-gray-200 dark:border-gray-700">
          <button @click="showEventLog = !showEventLog" class="w-full px-3 py-1.5 flex items-center justify-between text-[10px] text-gray-400 hover:text-gray-600">
            <span>📋 事件日志 ({{ events.length }})</span>
            <svg :class="['w-3 h-3 transition-transform', showEventLog ? 'rotate-180' : '']" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
          </button>
          <div v-if="showEventLog" class="max-h-32 overflow-y-auto px-3 pb-1.5 space-y-0.5">
            <div v-for="(e, i) in events.slice(-20)" :key="i" class="flex items-center gap-1.5 text-[10px]">
              <span :class="{
                'text-amber-500': e.type === 'thinking',
                'text-orange-500': e.type === 'warning',
                'text-blue-500': e.type === 'searching',
                'text-purple-500': e.type === 'writing',
                'text-green-500': e.type === 'done',
                'text-red-500': e.type === 'error',
              }">{{ e.type === 'thinking' ? '💭' : e.type === 'searching' ? '🔍' : e.type === 'writing' ? '✍️' : e.type === 'done' ? '✅' : e.type === 'error' ? '❌' : '•' }}</span>
              <span class="text-gray-500 truncate">{{ e.message }}</span>
            </div>
          </div>
        </div>
        
        <!-- 文献库面板 -->
        <LiteraturePanel
          v-if="showLiteraturePanel"
          :literature="literature"
          :search-loading="searchLoading"
          :active-sources="activeSources"
          :all-search-sources="allSearchSources"
          v-model:literature-search="literatureSearch"
          :filtered-literature="filteredLiterature"
          :expanded-paper="expandedPaper"
          :cited-paper-ids="citedPaperIds"
          :show-source-selector="showSourceSelector"
          v-model:show-paid-source-config="showPaidSourceConfig"
          :paid-source-config-target="paidSourceConfigTarget"
          v-model:paid-source-api-key="paidSourceApiKey"
          v-model:paid-source-gateway-url="paidSourceGatewayUrl"
          @search="searchLiterature"
          @import-bibtex="triggerBibtexImport"
          @toggle-source-selector="showSourceSelector = !showSourceSelector"
          @open-paid-config="openPaidSourceConfig"
          @save-paid-key="savePaidSourceKey"
          @toggle-paper-expand="togglePaperExpand"
          @copy-bibtex="copyBibtex"
          @insert-citation="insertCitation"
          @update:active-sources="activeSources = $event"
        />

        <!-- 引用核查结果面板 (抽屉式) -->
        <CitationPanel
          v-if="activeRightPanel === 'citation'"
          :results="citationResults"
          :replaced-list="citationReplacedList"
          :replaced-count="citationReplaced"
          :errors="citationErrors"
          :warnings="citationWarnings"
          @close="activeRightPanel = null"
        />

        <!-- 共识度分析面板 (抽屉式) -->
        <ConsensusPanel
          v-if="activeRightPanel === 'consensus'"
          :results="consensusResults"
          :loading="consensusLoading"
          @run="runConsensusAnalysis"
          @close="activeRightPanel = null"
        />

        <!-- P1-5: 版本历史面板 -->
        <SnapshotsPanel
          v-if="activeRightPanel === 'snapshots'"
          :snapshots="snapshots"
          :loading="snapshotsLoading"
          @create="createSnapshot"
          @restore="restoreSnapshot"
          @delete="deleteSnapshot"
          @close="activeRightPanel = null"
        />

        <!-- AI 助手面板 -->
        <AIPanel
          v-if="showAIPanel"
          :messages="aiMessages"
          :streaming="aiStreaming"
          :research-depth="researchDepth"
          :research-depths="depthOptions"
          :quick-actions="aiQuickActions"
          @run-pipeline="runStormPipeline"
          @run-action="runAIAction"
          @update:research-depth="researchDepth = $event"
        />

        <!-- 查重 + AIGC 检测面板 (抽屉式) -->
        <PlagPanel
          v-if="activeRightPanel === 'plag'"
          :result="plagResult"
          :loading="plagLoading"
          @run="runPlagcheck"
          @close="activeRightPanel = null"
        />
        
        <!-- 论文评分面板 (抽屉式) -->
        <ScorePanel
          v-if="activeRightPanel === 'score'"
          :result="scoreResult"
          :loading="scoreLoading"
          :dimensions="scoreDimensions"
          @run="runScore"
          @close="activeRightPanel = null"
        />
      </div>
    </div>

    <!-- 导出面板 (Modal) — 已拆分为 WriterExportModal -->
    <WriterExportModal
      :visible="showExportPanel"
      :project-title="project.title || ''"
      :project-id="project.id"
      :build-full-paper="buildFullPaper"
      :save-current-section="saveCurrentSection"
      :active-section="activeSection"
      :current-content="currentContent"
      :section-contents="sectionContents"
      @close="showExportPanel = false"/>

    <!-- P0-3: 逐段修改弹窗 — 已拆分为 WriterRewriteModal -->
    <WriterRewriteModal
      :visible="showRewriteModal"
      :target-title="rewriteTarget.title"
      :target-key="rewriteTarget.key"
      :project-id="project.id"
      :section-contents="sectionContents"
      :active-section="activeSection"
      :outline="outline"
      @close="showRewriteModal = false"
      @rewrite-done="onRewriteDone"/>
  </div>

      
      <!-- Checkpoint 阶段确认弹窗 -->
      <Teleport to='body'>
        <div v-if='showCheckpointConfirm' class='fixed inset-0 z-[200] flex items-center justify-center bg-black/50'>
          <div class='bg-white dark:bg-gray-800 rounded-2xl shadow-2xl p-6 max-w-md mx-4'>
            <div class='text-center mb-4'>
              <div class='text-5xl mb-3'>📋</div>
              <h3 class='text-lg font-semibold text-gray-800 dark:text-gray-100 mb-1'>阶段完成</h3>
              <p class='text-sm text-gray-500 dark:text-gray-400'>
                {{ pendingCheckpoint?.message || '继续下一阶段？' }}
              </p>
              <p v-if='pendingCheckpoint?.remaining?.length' class='text-xs text-gray-400 mt-1'>
                剩余：{{ pendingCheckpoint?.remaining?.join(' → ') }}
              </p>
            </div>
            <div class='flex justify-center gap-3 mt-5'>
              <button @click='() => { showCheckpointConfirm = false; pendingCheckpoint = null; if (aiAbortController?.value) aiAbortController.value.abort() }'
                class='px-4 py-2 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-xl border border-gray-200 dark:border-gray-600'>
                暂停
              </button>
              <button @click='resumePipeline'
                class='px-5 py-2 bg-green-600 hover:bg-green-700 text-white rounded-xl text-sm font-medium shadow'>
                继续 🚀
              </button>
            </div>
          </div>
        </div>
      </Teleport>

</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, reactive , defineAsyncComponent} from 'vue'
import { useRouter } from 'vue-router'
import DOMPurify from 'dompurify'
import ProjectList from './ProjectList.vue'
const WriterExportModal = defineAsyncComponent(() => import('./WriterExportModal.vue'))
const WriterRewriteModal = defineAsyncComponent(() => import('./WriterRewriteModal.vue'))
const LiteraturePanel = defineAsyncComponent(() => import('./panels/LiteraturePanel.vue'))
const CitationPanel = defineAsyncComponent(() => import('./panels/CitationPanel.vue'))
const ConsensusPanel = defineAsyncComponent(() => import('./panels/ConsensusPanel.vue'))
const SnapshotsPanel = defineAsyncComponent(() => import('./panels/SnapshotsPanel.vue'))
const AIPanel = defineAsyncComponent(() => import('./panels/AIPanel.vue'))
const PlagPanel = defineAsyncComponent(() => import('./panels/PlagPanel.vue'))
const ScorePanel = defineAsyncComponent(() => import('./panels/ScorePanel.vue'))
import { useScholarPanelStore } from '../stores/scholar-panel.js'
import MarkdownIt from 'markdown-it'
import texmath from 'markdown-it-texmath'
import katex from 'katex'
import 'katex/dist/katex.min.css'

// ── markdown-it 实例（学术论文渲染：公式 + 表格 + 代码高亮）
const md = new MarkdownIt({ html: false, breaks: false, linkify: true })
  .use(texmath, { engine: katex, delimiters: ['dollars', 'brackets', 'doxygen', 'gitlab'] })
md.enable(['table', 'strikethrough'])

const router = useRouter()

// ── Pinia store: right panel shared state
const panelStore = useScholarPanelStore()

// Destructure store state for template usage (reactive)
const activeRightPanel = computed({
  get: () => panelStore.activeRightPanel,
  set: (v) => { panelStore.activeRightPanel = v }
})
const showLiteraturePanel = computed({
  get: () => panelStore.showLiteraturePanel,
  set: (v) => { panelStore.showLiteraturePanel = v }
})
const showAIPanel = computed({
  get: () => panelStore.showAIPanel,
  set: (v) => { panelStore.showAIPanel = v }
})
const showEventLog = computed({
  get: () => panelStore.showEventLog,
  set: (v) => { panelStore.showEventLog = v }
})
const rightCollapsed = computed({
  get: () => panelStore.rightCollapsed,
  set: (v) => { panelStore.rightCollapsed = v }
})

// ═══════════════════════════════════════════════════════════════════
// 项目状态
// ═══════════════════════════════════════════════════════════════════
const currentModel = ref('')
const currentProvider = ref('')
const events = ref([])  // 事件日志
// showEventLog now from store: panelStore.showEventLog

// ═════════════════════════════════════════════════════════════════
// 多项目独立工作空间
// ═════════════════════════════════════════════════════════════════
const projects = ref([])
const project = ref({})
const showProjectSwitcher = ref(false)
const projectSwitcherRef = ref(null)
const isLoadingProject = ref(false)
const sectionContents = ref({})

const hasProject = computed(() => !!project.value?.id)

// 多用户隔离: 从 localStorage 生成持久 client UUID
const clientId = ref(localStorage.getItem('sf_client_id') || '')
if (!clientId.value) {
  clientId.value = 'sf_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 9)
  localStorage.setItem('sf_client_id', clientId.value)
}

const stages = ref([])  // 动态从 /api/scholar/agents 加载
const activeStage = ref('')
const showStageDropdown = ref(false)  // 阶段/模型折叠下拉

const loadStages = async () => {
  try {
    const r = await fetch('/api/scholar/agents')
    if (r.ok) {
      const d = await r.json()
      stages.value = (d.agents || []).map(a => ({
        id: a.name,
        name: a.icon + ' ' + a.label,
        icon: a.icon,
        label: a.label,
        description: a.description,
        promptHint: a.promptHint,
        completed: false
      }))
      if (stages.value.length > 0 && !activeStage.value) {
        activeStage.value = stages.value[0].id
      }
    }
  } catch (e) { console.error('load stages', e) }
}

// ═══════════════════════════════════════════════════════════════════
// Agent-Provider 模型分配（每个 Agent 独立选择厂商和模型）
// ═══════════════════════════════════════════════════════════════════
const agentProviders = ref({})  // { topic: {provider, model}, ... }
const availableProviders = ref([])
const showModelPicker = ref(false)
const modelPickerTarget = ref(null)  // agent name being edited
const openAgentDropdown = ref(null)   // currently-open agent dropdown (stage id)
const providerSearch = ref('')       // search box in dropdown

const loadAgentProviders = async () => {
  if (!project.value?.id) { agentProviders.value = {}; return }
  try {
    const r = await fetch(`/api/scholar/projects/${project.value.id}/agent-providers`)
    if (r.ok) {
      const d = await r.json()
      const map = {}
      d.agents.forEach(a => { map[a.agent] = { provider: a.provider, model: a.model } })
      agentProviders.value = map
    }
  } catch (e) { console.error('load agent providers', e) }
}

const loadAvailableProviders = async () => {
  try {
    // 复用 Vermes 的 /api/config/cloud-models — 单源真相, 26 个云厂商 + 推荐标记
    const r = await fetch('/api/config/cloud-models')
    if (r.ok) {
      const d = await r.json()
      const recommended = d.recommended_providers || []
      const recIds = new Set(recommended.map(p => p.id))
      const freeSet = new Set(recommended.filter(p => p.free).map(p => p.id))
      // 叠加已配置 Key 的标记
      let configuredKeys = new Set()
      try {
        const r2 = await fetch('/api/scholar/providers')
        if (r2.ok) {
          const d2 = await r2.json()
          configuredKeys = new Set((d2.providers || []).filter(p => p.has_key).map(p => p.key))
        }
      } catch {}
      // deepseek/vbit/agnes/ollama/oneapi 中转 视为可用 (Vermes 默认走 ONEAPI 代理)
      const oneapiAvail = ['deepseek', 'vbit', 'agnes', 'ollama', 'oneapi']
      availableProviders.value = (d.cloud_models || []).map(key => ({
        key,
        name: key,
        has_key: configuredKeys.has(key) || oneapiAvail.includes(key),
        recommended: recIds.has(key),
        free: freeSet.has(key),
      }))
    }
  } catch (e) { console.error('load providers', e) }
}

const setAgentProvider = async (agent, provider, model) => {
  try {
    await fetch(`/api/scholar/projects/${project.value.id}/agent-providers`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent, provider, model }),
    })
    agentProviders.value[agent] = { provider, model }
  } catch (e) { console.error('set agent provider', e) }
}

const onModelSelect = async (agent, provider, event) => {
  const model = event.target.value
  if (!model) return
  await setAgentProvider(agent, provider, model)
  showModelPicker.value = false
}

const agentProviderLabel = (agent) => {
  const ap = agentProviders.value[agent]
  if (!ap) return ''
  const p = ap.provider || 'deepseek'
  const m = (ap.model || 'v4-flash').replace('deepseek-', '').replace('claude-', '').replace('gpt-', '')
  return `${p}/${m}`
}

const getAgentProviderTitle = (agent) => {
  const ap = agentProviders.value[agent]
  if (!ap || !ap.provider) return '点击选择模型'
  return ap.model ? `${ap.provider} / ${ap.model}` : `${ap.provider} (默认模型)`
}

const toggleAgentDropdown = async (stageId, event) => {
  if (openAgentDropdown.value === stageId) {
    openAgentDropdown.value = null
    return
  }
  openAgentDropdown.value = stageId
  providerSearch.value = ''
  await loadAvailableProviders()
}

const closeAgentDropdown = () => {
  openAgentDropdown.value = null
  providerSearch.value = ''
}

// 搜索过滤后的厂商（只显示已配置 Key 的）
const configuredProviders = computed(() =>
  availableProviders.value.filter(p => p.has_key)
)

const filteredProviders = computed(() => {
  const list = configuredProviders.value
  if (!providerSearch.value.trim()) return list
  const q = providerSearch.value.toLowerCase()
  return list.filter(p => {
    if (p.key.toLowerCase().includes(q)) return true
    const ms = getModelsForProvider(p.key)
    return ms.some(m => m.toLowerCase().includes(q))
  })
})

// 每个厂商的模型候选 (从用户已配置 Key 的厂商中只列真实可用的)
const PROVIDER_MODELS = {
  deepseek: ['deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-reasoner'],
  openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'o1-preview', 'o1-mini'],
  gemini: ['gemini-2.5-pro', 'gemini-2.0-flash', 'gemini-1.5-pro'],
  qwen: ['qwen3-max', 'qwen-plus', 'qwen-turbo', 'qwen-long'],
  baichuan: ['baichuan4', 'baichuan3-turbo'],
  vbit: ['vbit-pro', 'vbit-flash'],
  openrouter: ['openrouter/auto', 'openrouter/sonnet', 'openrouter/gpt-4o'],
  agnes: ['agnes/default', 'agnes-2.0-flash', 'agnes-pro'],
  anthropic: ['claude-sonnet-4-20250514', 'claude-3-opus-20240229', 'claude-3-5-sonnet-20241022'],
  moonshot: ['moonshot-v1-128k', 'moonshot-v1-32k', 'moonshot-v1-8k'],
  zhipu: ['glm-4-plus', 'glm-4-flash', 'glm-4-air'],
  alibaba: ['qwen3-max', 'qwen-plus', 'qwen-turbo'],
}
const getModelsForProvider = (key) => PROVIDER_MODELS[key] || ['default']

// ═══════════════════════════════════════════════════════════════════
// 大纲与章节
// ═══════════════════════════════════════════════════════════════════
const outline = ref([])
const activeSection = ref(null)

const totalWordCount = computed(() => outline.value.reduce((sum, s) => sum + (s.wordCount || 0), 0))

// hasProject 已移至上文

// ═══════════════════════════════════════════════════════════════════
// 编辑器状态
// ═══════════════════════════════════════════════════════════════════
const editorRef = ref(null)
const currentContent = ref('')
const viewMode = ref('split') // 'edit' | 'split' | 'preview'

// ── 浮动选择菜单 (Phase 1.1 — 学 Jenni AI) ──
const showSelectionMenu = ref(false)
const selectionMenuStyle = ref({})
const selectedText = ref('')
const inlineEditLoading = ref(false)

const onTextSelect = () => {
  const ta = editorRef.value
  if (!ta) return
  const start = ta.selectionStart
  const end = ta.selectionEnd
  const text = currentContent.value.slice(start, end).trim()
  if (text.length < 10) {
    showSelectionMenu.value = false
    return
  }
  selectedText.value = text
  // 计算菜单位置（在选中文字上方居中）
  const rect = ta.getBoundingClientRect()
  const lineHeight = 20
  const charWidth = 8.4 // monospace 近似
  const cursorTop = (start > 0 ? currentContent.value.slice(0, start).split('\n').length - 1 : 0) * lineHeight
  const cursorLeft = ((start > 0 ? currentContent.value.slice(currentContent.value.lastIndexOf('\n', start - 1) + 1, start).length : 0) * charWidth)
  selectionMenuStyle.value = {
    left: `${Math.min(cursorLeft + 10, rect.width - 420)}px`,
    top: `${Math.max(cursorTop - 42, 0)}px`
  }
  showSelectionMenu.value = true
  
  // P1-1: 选中文字桥接到 AI 输入框 (若输入框为空则自动填入上下文)
  if (!aiInput.value.trim()) {
    aiInput.value = `关于以下内容：\n> ${text.slice(0, 200)}${text.length > 200 ? '...' : ''}\n\n`
  }
}

const inlineEdit = async (action) => {
  showSelectionMenu.value = false
  if (!selectedText.value || inlineEditLoading.value) return
  inlineEditLoading.value = true
  const ta = editorRef.value
  try {
    const r = await fetch('/api/scholar/inline-edit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: selectedText.value, action }),
    })
    if (!r.ok) throw new Error(await r.text())
    const { text: newText } = await r.json()
    // 替换选中文本
    const start = ta.selectionStart
    const end = ta.selectionEnd
    currentContent.value = currentContent.value.slice(0, start) + newText + currentContent.value.slice(end)
    onEditorInput()
    // 选中替换后的文本
    setTimeout(() => {
      ta.focus()
      ta.setSelectionRange(start, start + newText.length)
    }, 50)
  } catch (e) {
    alert('AI 编辑失败: ' + e.message)
  } finally {
    inlineEditLoading.value = false
  }
}

// Markdown 渲染（markdown-it + KaTeX 公式 + 引用核查着色）
const renderedContent = computed(() => {
  let html = md.render(currentContent.value || '')
  // 引用标记着色 — markdown-it 渲染后的 [1] 是纯文本，用正则替换为 sup + tooltip
  html = html.replace(/\[(\d+)\]/g, (match, num) => {
    const result = citationResults.value.find(r => r.ref === parseInt(num))
    let cls = 'text-blue-600 cursor-pointer hover:underline'
    let tooltip = ''
    if (result) {
      if (result.score >= 7) cls = 'text-green-600 bg-green-50 dark:bg-green-900/30 px-0.5 rounded cursor-pointer hover:bg-green-100'
      else if (result.score >= 3) cls = 'text-amber-600 bg-amber-50 dark:bg-amber-900/30 px-0.5 rounded cursor-pointer hover:bg-amber-100'
      else cls = 'text-red-600 bg-red-50 dark:bg-red-900/30 px-0.5 rounded cursor-pointer hover:bg-red-100'
      tooltip = ` title="${result.reason?.replace(/"/g, '&quot;') || ''}"`
    }
    return `<sup class="${cls}"${tooltip}>[${num}]</sup>`
  })
  return DOMPurify.sanitize(html)
})

const editor = {
  format: (type) => {
    if (!editorRef.value) return
    const ta = editorRef.value
    const start = ta.selectionStart
    const end = ta.selectionEnd
    const selected = currentContent.value.slice(start, end)
    let formatted = ''
    if (type === 'bold') {
      formatted = selected ? `**${selected}**` : '**粗体**'
    } else if (type === 'italic') {
      formatted = selected ? `*${selected}*` : '*斜体*'
    } else if (type === 'heading') {
      // 在行首插入 ##
      const lineStart = currentContent.value.lastIndexOf('\n', start - 1) + 1
      const before = currentContent.value.slice(0, lineStart)
      const line = currentContent.value.slice(lineStart, end || start)
      const after = currentContent.value.slice(end || start)
      const hasHeading = /^#{1,6}\s/.test(line)
      if (hasHeading) {
        // 取消标题
        currentContent.value = before + line.replace(/^#{1,6}\s/, '') + after
      } else {
        currentContent.value = before + '## ' + line + after
      }
      onEditorInput()
      return
    }
    if (selected) {
      currentContent.value = currentContent.value.slice(0, start) + formatted + currentContent.value.slice(end)
      ta.setSelectionRange(start + 2, start + 2 + selected.length)
    } else {
      currentContent.value = currentContent.value.slice(0, start) + formatted + currentContent.value.slice(end)
      ta.setSelectionRange(start + 2, start + 2 + formatted.length - 4)
    }
    ta.focus()
    onEditorInput()
  }
}

const onEditorInput = () => {
  // 更新字数统计 + 自动保存
  if (!activeSection.value || !project.value?.id) return
  sectionContents.value[activeSection.value] = currentContent.value
  const section = outline.value.find(s => s.id === activeSection.value)
  if (section) section.wordCount = currentContent.value.length
  // 防抖保存
  scheduleAutosave()
}

// ── 全局键盘快捷键
const handleKeyboard = (e) => {
  const isMeta = e.metaKey || e.ctrlKey
  if (!isMeta) return

  // 编辑器格式快捷键
  if (e.key === 'b' || e.key === 'B') { e.preventDefault(); editor.format('bold'); return }
  if (e.key === 'i' || e.key === 'I') { e.preventDefault(); editor.format('italic'); return }
  if (e.key === 'h' || e.key === 'H') { e.preventDefault(); editor.format('heading'); return }
  if (e.key === 't' || e.key === 'T') { e.preventDefault(); insertTable(); return }
  if (e.key === 'k' || e.key === 'K') { e.preventDefault(); insertCitation(); return }
  if (e.key === 'Escape') { e.preventDefault(); showExportPanel.value = false; showSelectionMenu.value = false; showLiteraturePanel.value = false; showAIPanel.value = false; activeRightPanel.value = null; return }
  if (e.shiftKey && (e.key === 'F' || e.key === 'f')) { e.preventDefault(); insertFormula(); return }
  if (e.shiftKey && (e.key === 'C' || e.key === 'C'.toLowerCase())) { e.preventDefault(); copyRichText(); return }
  // 保存
  if (e.key === 's' || e.key === 'S') { e.preventDefault(); saveNow(); return }
}

// 智能粘贴：识别论文结构并自动拆分
const pasteAndParse = async () => {
  try {
    const text = await navigator.clipboard.readText()
    if (!text.trim()) {
      alert('剪贴板为空')
      return
    }
    
    const parsed = parsePaperStructure(text)
    
    if (parsed.sections.length === 0) {
      // 没识别到结构，直接粘贴到当前章节
      currentContent.value = text
      onEditorInput()
      alert('未识别到章节结构，已粘贴到当前章节')
      return
    }
    
    // 确认是否覆盖
    if (outline.value.length > 0) {
      if (!confirm(`识别到 ${parsed.sections.length} 个章节，是否覆盖当前大纲和内容？`)) return
    }
    
    // 重建大纲和内容
    outline.value = parsed.sections.map((sec, idx) => ({
      id: 'sec-' + Date.now() + '-' + idx,
      number: sec.number,
      title: sec.title,
      wordCount: sec.content.length,
      status: 'completed'
    }))
    
    sectionContents.value = {}
    parsed.sections.forEach((sec, idx) => {
      const key = outline.value[idx].id
      sectionContents.value[key] = sec.content
    })
    
    // 设置标题
    if (parsed.title) {
      project.value.title = parsed.title
    }
    
    // 提取参考文献
    if (parsed.references.length > 0) {
      literature.value = parsed.references.map((ref, idx) => ({
        id: 'ref-' + idx,
        title: ref.title || '未知文献',
        authors: ref.authors || [],
        year: ref.year || '',
        venue: ref.venue || ''
      }))
    }
    
    // 激活第一章
    if (outline.value.length > 0) {
      activeSection.value = outline.value[0].id
      currentContent.value = sectionContents.value[outline.value[0].id] || ''
    }
    
    alert(`✅ 识别完成：${parsed.title || '未命名论文'}\n共 ${parsed.sections.length} 章，${parsed.references.length} 篇参考文献`)
    
  } catch (e) {
    alert('粘贴失败: ' + e.message)
  }
}

// 论文结构解析器
const parsePaperStructure = (text) => {
  const result = {
    title: '',
    sections: [],
    references: []
  }
  
  const lines = text.split('\n')
  let currentSection = null
  let currentContent = []
  let inReferences = false
  
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim()
    
    // 识别标题 (# 开头)
    if (line.startsWith('# ') && !result.title) {
      result.title = line.substring(2).trim()
      continue
    }
    
    // 识别参考文献节 (修复：统一字符类，避免重复)
    if (/^#{1,3}\s*参考文献|^#{1,3}\s*References/i.test(line)) {
      if (currentSection) {
        currentSection.content = currentContent.join('\n').trim()
        result.sections.push(currentSection)
      }
      inReferences = true
      currentSection = null
      continue
    }
    
    // 在参考文献节内
    if (inReferences) {
      // 匹配 [n] 作者. 标题. 期刊, 年份 格式 (允许前导空格，更宽松)
      const refMatch = line.match(/^\s*\[(\d+)\]\s*(.+)/)
      if (refMatch) {
        const refText = refMatch[2].trim()
        // 尝试提取作者、标题、年份、期刊（更鲁棒的解析）
        const yearMatch = refText.match(/(\d{4})/)
        const year = yearMatch ? yearMatch[1] : ''
        // 按点号分割，但保留可能的复杂标题
        const parts = refText.split(/\.\s+/)
        const authors = parts[0] ? parts[0].split(',').map(s => s.trim()).filter(s => s) : []
        // 标题：取第二部分，如果没有则取剩余部分（除去可能的年份和期刊）
        let title = parts[1] || refText
        // 清理标题中的多余点号
        title = title.replace(/\.$/, '').trim()
        // 期刊：最后一部分（如果包含字母且不是纯年份）
        let venue = parts[parts.length - 1] || ''
        if (venue && /\d{4}/.test(venue)) venue = parts[parts.length - 2] || ''
        result.references.push({
          authors,
          title,
          year,
          venue: venue || ''
        })
      }
      continue
    }
    
    // 识别章节标题 (## 或 1. 或 第1章)
    const sectionMatch = line.match(/^(?:#{2,3}\s*|(\d+[\.\d]*)\s+|第[一二三四五六七八九十\d]+章\s*)(.+)/)
    if (sectionMatch) {
      // 保存上一节
      if (currentSection) {
        currentSection.content = currentContent.join('\n').trim()
        result.sections.push(currentSection)
      }
      
      const number = sectionMatch[1] || ''
      const title = sectionMatch[2].trim()
      
      currentSection = {
        number: number,
        title: title,
        content: ''
      }
      currentContent = []
      continue
    }
    
    // 普通内容行
    if (currentSection) {
      currentContent.push(lines[i])
    } else if (!result.title && line) {
      // 还没找到标题前的内容，可能是摘要
      currentContent.push(lines[i])
    }
  }
  
  // 保存最后一节
  if (currentSection) {
    currentSection.content = currentContent.join('\n').trim()
    result.sections.push(currentSection)
  }
  
  // 如果没识别到章节，但内容很长，尝试按空行分段
  if (result.sections.length === 0 && text.length > 500) {
    const paragraphs = text.split(/\n{2,}/)
    if (paragraphs.length >= 3) {
      // 假设第一段是摘要，后面按段落分
      result.sections.push({
        number: '',
        title: '摘要',
        content: paragraphs[0]
      })
      paragraphs.slice(1).forEach((p, idx) => {
        if (p.trim().length > 100) {
          result.sections.push({
            number: String(idx + 1),
            title: p.substring(0, 30).replace(/[#*]/g, '') + '...',
            content: p
          })
        }
      })
    }
  }
  
  return result
}

// ═══════════════════════════════════════════════════════════════════
// 文献库
// ═══════════════════════════════════════════════════════════════════
const literature = ref([])

const literatureSearch = ref('')
const searchLoading = ref(false)
const selectedLiterature = ref(null)
const expandedPaper = ref(null)

// 研究深度选择器
const researchDepth = ref(2)  // 1=快速, 2=标准(默认), 3=深度
const depthOptions = [
  { value: 1, label: '快速', icon: '⚡', desc: '单轮检索，适合快速了解' },
  { value: 2, label: '标准', icon: '🔍', desc: '多视角检索+聚合' },
  { value: 3, label: '深度', icon: '🕸️', desc: '3轮递归+缺口分析+全链路' },
]

const filteredLiterature = computed(() => {
  if (!literatureSearch.value) return literature.value
  const q = literatureSearch.value.toLowerCase()
  return literature.value.filter(p => 
    p.title.toLowerCase().includes(q) || 
    p.authors.some(a => a.toLowerCase().includes(q))
  )
})

const literatureCount = computed(() => literature.value.length)

// 检测文献是否被正文引用
const citedPaperIds = computed(() => {
  const ids = new Set()
  if (!currentContent.value) return ids
  const cited = currentContent.value.match(/\\[(\\d+)\\]/g) || []
  for (const m of cited) {
    const num = parseInt(m.replace(/[\\[\\]]/g, ''))
    if (literature.value[num - 1]) ids.add(literature.value[num - 1].id)
  }
  return ids
})

const selectLiterature = (paper) => {
  selectedLiterature.value = paper
}

const insertCitation = (paper) => {
  if (!editorRef.value) return
  const ta = editorRef.value
  const start = ta.selectionStart
  if (paper) {
    // P1-6: 统一 [n] 纯数字格式，与 _clean_citation_format 后端一致
    const citeKey = paper.citeKey || `ref${paper.id || Date.now()}`
    const num = citationIndex.value[citeKey] || (citationMaxNum.value + 1)
    if (!citationIndex.value[citeKey]) {
      citationIndex.value[citeKey] = num
      citationMaxNum.value = num
    }
    const cite = `[${num}]`
    currentContent.value = currentContent.value.slice(0, start) + cite + currentContent.value.slice(start)
    ta.setSelectionRange(start + cite.length, start + cite.length)
  } else {
    const cite = '[?]'
    currentContent.value = currentContent.value.slice(0, start) + cite + currentContent.value.slice(start)
  }
  ta.focus()
  onEditorInput()
}

const togglePaperExpand = (paper) => {
  expandedPaper.value = expandedPaper.value?.id === paper.id ? null : paper
}

const copyBibtex = (paper) => {
  const citeKey = paper.citeKey || `ref${paper.id || Date.now()}`
  const bibtex = `@article{${citeKey},
  title={${paper.title || ''}},
  author={${(paper.authors || []).join(' and ')}},
  year={${paper.year || ''}},
  journal={${paper.venue || ''}}
}`
  navigator.clipboard.writeText(bibtex).then(() => {
    // 短暂提示（静默）
  }).catch(() => {
    // fallback: 选中文本
    const ta = document.createElement('textarea')
    ta.value = bibtex
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  })
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

// ═══════════════════════════════════════════════════════════════════
// 右侧浮动功能面板系统（P0-3: 顶部功能向左弹出，避免挤压）
// ═══════════════════════════════════════════════════════════════════
// activeRightPanel now managed by panelStore
const rightPanelWidth = ref(480)   // P1: 面板宽度增大到 480px

// P1: 更多工具下拉菜单
const showMoreToolsDropdown = ref(false)
const moreToolsRef = ref(null)

// P1: 浮出面板标题
const floatingPanelTitle = computed(() => {
  if (showLiteraturePanel.value) return '📚 文献库'
  if (showAIPanel.value) return '🤖 AI 助手'
  const titles = {
    citation: '✅ 引用核查',
    consensus: '📊 共识度分析',
    plag: '🔍 查重 + AIGC 检测',
    score: '⭐ 论文评分',
    snapshots: '📜 版本历史',
  }
  return titles[activeRightPanel.value] || '工具面板'
})

// 切换右侧面板（互斥，点同一按钮关闭）— now delegates to store
const toggleRightPanel = (name) => {
  panelStore.togglePanel(name)
}

// ═══════════════════════════════════════════════════════════════════
// 引用核查状态
// ═══════════════════════════════════════════════════════════════════
const citationResults = ref([])      // [{ref, score, reason}]
const citationReplaced = ref(0)      // 真实文献替换计数
const citationReplacedList = ref([]) // [{title, source, year}]
const citationErrors = computed(() => citationResults.value.filter(r => r.score < 3).length)
const citationWarnings = computed(() => citationResults.value.filter(r => r.score >= 3 && r.score < 7).length)

// ═══════════════════════════════════════════════════════════════════
// P1-5: 版本历史
// ═══════════════════════════════════════════════════════════════════
const snapshots = ref([])
const snapshotsLoading = ref(false)

const loadSnapshots = async () => {
  if (!project.value?.id) return
  snapshotsLoading.value = true
  try {
    const r = await fetch(`/api/scholar/projects/${project.value.id}/snapshots`)
    snapshots.value = (await r.json()).snapshots || []
  } catch (e) { /* silently fail */ }
  snapshotsLoading.value = false
}

const createSnapshot = async () => {
  if (!project.value?.id) return
  // 构建 payload: 当前全文 + 元信息
  const data = {
    content: currentContent.value,
    section_contents: { ...sectionContents.value },
    overview: project.value.title || ''
  }
  const note = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  try {
    const r = await fetch(`/api/scholar/projects/${project.value.id}/snapshots`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: note, note: `${totalWordCount.value} 字`, data })
    })
    if (r.ok) {
      await loadSnapshots()
    }
  } catch (e) {
    alert('快照保存失败: ' + e.message)
  }
}

const restoreSnapshot = async (snap) => {
  if (!confirm(`恢复「${snap.label}」的版本？当前未保存内容将丢失。`)) return
  try {
    const r = await fetch(`/api/scholar/snapshots/${snap.id}`)
    const full = await r.json()
    const p = full.payload || {}
    if (p.content) currentContent.value = p.content
    if (p.section_contents) sectionContents.value = { ...p.section_contents }
    // 保存到后端
    await saveCurrentSection()
    activeRightPanel.value = null
    alert('✅ 已恢复到: ' + (snap.label || '快照'))
  } catch (e) {
    alert('恢复失败: ' + e.message)
  }
}

const deleteSnapshot = async (sid) => {
  if (!confirm('删除此快照？')) return
  try {
    await fetch(`/api/scholar/snapshots/${sid}`, { method: 'DELETE' })
    snapshots.value = snapshots.value.filter(s => s.id !== sid)
  } catch (e) {
    alert('删除失败: ' + e.message)
  }
}

// ═══════════════════════════════════════════════════════════════════
// 共识度分析
// ═══════════════════════════════════════════════════════════════════
const consensusLoading = ref(false)
const consensusResults = ref([])     // [{claim, support, oppose, neutral, total, consensus_pct, confidence, per_paper, _expanded}]

// ── P0-2: 查重 + AIGC 检测 ──
const plagLoading = ref(false)
const plagResult = ref(null)

// ── P2: 论文评分 ──
const scoreLoading = ref(false)
const scoreResult = ref(null)  // {originality:{score,reasoning}, logic:{...}, citation_completeness:{...}, overall, overall_reasoning}
const scoreDimensions = [
  { key: 'originality', label: '原创性', icon: '💡', weight: '30%' },
  { key: 'logic', label: '逻辑性', icon: '🔗', weight: '35%' },
  { key: 'citation_completeness', label: '引用完整性', icon: '📖', weight: '35%' },
]

const runScore = async () => {
  activeRightPanel.value = 'score'
  if (!project.value?.id) return
  // 防御：保存当前编辑内容 + 刷新 SSE 防抖
  await flushSseSaves()
  await saveCurrentSection()
  if (!hasSectionContent()) {
    alert('论文内容为空，请先完成写作再评分')
    return
  }
  scoreLoading.value = true
  try {
    const r = await fetch(`/api/scholar/projects/${project.value.id}/score`)
    if (!r.ok) throw new Error(await r.text())
    scoreResult.value = await r.json()
  } catch (e) {
    alert('评分失败: ' + e.message)
  } finally {
    scoreLoading.value = false
  }
}

const runPlagcheck = async () => {
  activeRightPanel.value = 'plag'
  if (!project.value?.id) return
  // 防御：保存当前内容 + 刷新 SSE 防抖
  await flushSseSaves()
  await saveCurrentSection()
  plagLoading.value = true
  try {
    // 收集全部章节内容
    const content = Object.values(sectionContents.value).join('\n\n') || currentContent.value
    const r = await fetch(`/api/scholar/projects/${project.value.id}/plagcheck`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, title: project.value.title || '' }),
    })
    if (!r.ok) throw new Error(await r.text())
    plagResult.value = await r.json()
  } catch (e) {
    alert('查重检测失败: ' + e.message)
  } finally {
    plagLoading.value = false
  }
}

const runConsensusAnalysis = async () => {
  if (consensusLoading.value || !project.value?.id) return
  // 防御：保存当前内容 + 刷新 SSE 防抖
  await flushSseSaves()
  await saveCurrentSection()
  if (!hasSectionContent()) {
    alert('论文内容为空，请先完成写作再分析共识度')
    return
  }
  consensusLoading.value = true
  try {
    // 1. 先提取关键论断
    const claimsResp = await fetch(`/api/scholar/projects/${project.value.id}/claims`)
    if (!claimsResp.ok) {
      const err = await claimsResp.json().catch(() => ({ detail: `HTTP ${claimsResp.status}` }))
      alert(err.detail || '提取论断失败')
      return
    }
    const claimsData = await claimsResp.json()
    if (!claimsData.claims?.length) {
      alert('未能从论文中提取到关键论断，请先完成论文写作')
      return
    }
    
    // 2. 对每条论断评估共识度
    const consensusResp = await fetch(`/api/scholar/projects/${project.value.id}/consensus`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),  // 不传 claim，后端自动用 extract_key_claims 结果
    })
    if (!consensusResp.ok) {
      const err = await consensusResp.json().catch(() => ({ detail: `HTTP ${consensusResp.status}` }))
      alert(err.detail || '共识度分析失败')
      return
    }
    const consensusData = await consensusResp.json()
    consensusResults.value = (consensusData.results || []).map(r => ({ ...r, _expanded: false }))
    
    events.value.push({ type: 'done', message: `共识分析: ${consensusData.results?.length || 0}条论断`, time: Date.now() })
  } catch (e) {
    alert(`共识度分析失败: ${e.message}`)
  } finally {
    consensusLoading.value = false
  }
}

// ═══════════════════════════════════════════════════════════════════
// 搜索源选择器
// ═══════════════════════════════════════════════════════════════════
const showSourceSelector = ref(false)

// 动态从后端加载
const allSearchSources = ref([])
const activeSources = ref([])

const loadSearchSources = async () => {
  try {
    const [freeRes, paidRes] = await Promise.all([
      fetch('/api/scholar/sources/connectivity'),
      fetch('/api/scholar/sources/paid'),
    ])
    
    const freeData = freeRes.ok ? await freeRes.json() : { sources: [] }
    const paidData = paidRes.ok ? await paidRes.json() : { sources: [] }
    
    const sources = []
    // 免费源
    for (const s of (freeData.sources || [])) {
      sources.push({
        id: s.name,
        label: s.name.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' '),
        icon: SOURCE_ICONS[s.name] || '📄',
        paid: false,
        configured: true,
        accessible: s.accessible || false,
      })
    }
    // 付费源
    for (const s of (paidData.sources || [])) {
      sources.push({
        id: s.name,
        label: s.display_name,
        icon: SOURCE_ICONS[s.name] || '🔑',
        paid: true,
        configured: s.enabled || false,
        needsGatewayUrl: s.needs_gateway_url || false,
        registerUrl: s.register_url || '',
      })
    }
    allSearchSources.value = sources
    // 初始选中：所有免费的 + 已激活的付费源
    if (!activeSources.value.length) {
      activeSources.value = sources.filter(s => !s.paid || s.configured).map(s => s.id)
    }
  } catch (e) {
    console.error('loadSearchSources', e)
    // fallback: 硬编码基础免费源
    allSearchSources.value = [
      { id: 'openalex', label: 'OpenAlex', icon: '🌐', paid: false, configured: true },
      { id: 'arxiv', label: 'arXiv', icon: '📐', paid: false, configured: true },
      { id: 'crossref', label: 'Crossref', icon: '🔗', paid: false, configured: true },
      { id: 'semantic_scholar', label: 'Semantic Scholar', icon: '📖', paid: false, configured: true },
      { id: 'pubmed', label: 'PubMed', icon: '🩺', paid: false, configured: true },
      { id: 'core', label: 'CORE', icon: '📦', paid: false, configured: true },
      { id: 'doaj', label: 'DOAJ', icon: '🔓', paid: false, configured: true },
    ]
    activeSources.value = allSearchSources.value.map(s => s.id)
  }
}

const SOURCE_ICONS = {
  openalex: '🌐', arxiv: '📐', crossref: '🔗', semantic_scholar: '📖',
  pubmed: '🩺', core: '📦', doaj: '🔓',
  cnki: '🏛️', scopus: '📊', web_of_science: '🎓', google_scholar: '🔍',
}

// 付费源配置弹窗
const showPaidSourceConfig = ref(false)
const paidSourceConfigTarget = ref(null)  // 当前配置的付费源
const paidSourceApiKey = ref('')
const paidSourceGatewayUrl = ref('')

const openPaidSourceConfig = (src) => {
  paidSourceConfigTarget.value = src
  paidSourceApiKey.value = ''
  paidSourceGatewayUrl.value = ''
  showPaidSourceConfig.value = true
}

const savePaidSourceKey = async () => {
  const src = paidSourceConfigTarget.value
  if (!src || !paidSourceApiKey.value.trim()) return
  try {
    const body = { source: src.id, api_key: paidSourceApiKey.value.trim() }
    if (src.needsGatewayUrl && paidSourceGatewayUrl.value.trim()) {
      body.gateway_url = paidSourceGatewayUrl.value.trim()
    }
    const r = await fetch('/api/scholar/sources/activate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (r.ok) {
      src.configured = true
      if (!activeSources.value.includes(src.id)) activeSources.value.push(src.id)
      showPaidSourceConfig.value = false
      events.value.push({ type: 'done', message: `${src.label} 已激活`, time: Date.now() })
    } else {
      const err = await r.json()
      events.value.push({ type: 'error', message: `激活失败: ${err.message || ''}`, time: Date.now() })
    }
  } catch (e) {
    events.value.push({ type: 'error', message: `配置失败: ${e.message}`, time: Date.now() })
  }
}

// P1-8: BibTeX 批量导入
const triggerBibtexImport = () => {
  const input = document.createElement('textarea')
  input.style.cssText = 'position:fixed;top:10%;left:10%;width:80%;height:70%;z-index:9999;background:#1e1e2e;color:#cdd6f4;border:2px solid #89b4fa;border-radius:8px;padding:16px;font:14px monospace'
  input.placeholder = '粘贴 BibTeX 内容...\n\n例如:\n@article{he2016deep,\n  title={Deep Residual Learning for Image Recognition},\n  author={He, Kaiming and Zhang, Xiangyu and Ren, Shaoqing and Sun, Jian},\n  year={2016},\n  journal={CVPR}\n}'
  document.body.appendChild(input)
  input.focus()
  const finish = async () => {
    const text = input.value.trim()
    document.body.removeChild(input)
    if (!text || !project.value?.id) return
    try {
      const r = await fetch(`/api/scholar/projects/${project.value.id}/literature/import`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bibtex: text })
      })
      const data = await r.json()
      alert(`✅ 导入完成：${data.added} 篇新增，${data.skipped} 篇跳过`)
      // 刷新文献列表
      if (project.value?.id) {
        const litRes = await fetch(`/api/scholar/projects/${project.value.id}/literature`)
        if (litRes.ok) {
          const litData = await litRes.json()
          literature.value = (litData.literatures || []).map(l => ({
            id: l.id, title: l.title, authors: l.authors, year: l.year,
            venue: l.venue, abstract: l.abstract, url: l.url, doi: l.doi,
          }))
        }
      }
    } catch (e) {
      alert('导入失败: ' + e.message)
    }
  }
  input.addEventListener('blur', finish)
  input.addEventListener('keydown', (e) => { if (e.key === 'Escape') { input.blur() } })
}

const searchLiterature = async () => {
  const q = literatureSearch.value.trim() || project.value.title || ''
  if (!q) {
    alert('请输入检索关键词')
    return
  }
  searchLoading.value = true
  events.value.push({ type: 'searching', message: `检索: ${q} (${activeSources.value.join(', ')})`, time: Date.now() })
  try {
    const srcParam = activeSources.value.join(',')
    const r = await fetch(`/api/scholar/search?query=${encodeURIComponent(q)}&limit=10&sources=${encodeURIComponent(srcParam)}`)
    if (r.ok) {
      const d = await r.json()
      const newPapers = (d.results || d.papers || []).map((p, i) => ({
        id: p.id || Date.now() + i,
        title: p.title,
        authors: Array.isArray(p.authors) ? p.authors : (p.authors || '未知').split(','),
        year: p.year || (p.published_date || '').slice(0, 4),
        venue: p.venue || p.journal || '',
        abstract: p.abstract || p.snippet || '',
        url: p.url || '',
        doi: p.doi || '',
        source: p.source || p.meta?.source || 'crossref',
        citeKey: (p.authors?.[0] || 'Unknown').split(' ').pop() + (p.year || ''),
      }))
      // 去重合并
      const existingIds = new Set(literature.value.map(l => l.id))
      for (const np of newPapers) {
        if (!existingIds.has(np.id)) literature.value.push(np)
      }
      if (newPapers.length === 0) {
        events.value.push({ type: 'warning', message: `未找到"${q}"相关文献。建议：改用英文关键词 / 尝试其他搜索源`, time: Date.now() })
      } else {
        events.value.push({ type: 'done', message: `找到 ${newPapers.length} 篇新文献`, time: Date.now() })
      }
    }
  } catch (e) {
    events.value.push({ type: 'error', message: `检索失败: ${e.message}`, time: Date.now() })
  } finally {
    searchLoading.value = false
  }
}

const insertFormula = () => {
  currentContent.value += '\n$$\nE = mc^2\n$$\n'
}

const insertTable = () => {
  currentContent.value += '\n| 列1 | 列2 | 列3 |\n|-----|-----|-----|\n| A | B | C |\n'
}

// ═══════════════════════════════════════════════════════════════════
// AI 助手
// ═══════════════════════════════════════════════════════════════════
// showAIPanel, rightCollapsed now from panelStore
const leftCollapsed = ref(false)   // 左栏大纲折叠
const toggleLeftBar = () => { leftCollapsed.value = !leftCollapsed.value }
// toggleRightBar now delegates to store
const toggleRightBar = () => { panelStore.toggleRightBar() }
const aiInput = ref('')
const aiStreaming = ref(false)
const aiAbortController = ref(null)  // SSE 流式取消
const showCheckpointConfirm = ref(false)  // 分步确认弹窗
const pendingCheckpoint = ref(null)      // 当前待确认的 checkpoint 事件
const showAICommands = ref(false)
const aiMessages = ref([
  { role: 'assistant', content: '你好！我是你的论文写作助手。我可以帮你：\n\n• 选题分析与可行性评估\n• 检索相关文献\n• 生成论文大纲\n• 续写/润色内容\n\n准备好开始了吗？', time: Date.now() - 60000 }
])

const aiPlaceholder = computed(() => {
  const section = outline.value.find(s => s.id === activeSection.value)
  return section ? `让 AI 帮你写「${section.title}」...` : '输入指令让 AI 协助写作...'
})

const aiCommands = [
  { id: 'continue', name: '续写内容', icon: '✍️', prompt: '请根据上文继续写作，保持学术风格' },
  { id: 'polish', name: '润色语言', icon: '✨', prompt: '请润色以下段落的语言表达' },
  { id: 'expand', name: '展开论述', icon: '📖', prompt: '请对以下观点进行更详细的论述' },
  { id: 'summarize', name: '总结要点', icon: '📝', prompt: '请总结以下段落的核心要点' },
  { id: 'cite', name: '添加引用', icon: '📚', prompt: '请为以下观点添加相关文献引用' },
  { id: 'translate', name: '中英互译', icon: '🌐', prompt: '请将以下内容翻译成英文' }
]

const aiQuickActions = [
  { id: 'outline', name: '生成大纲', icon: '📋', agent: 'outline', prompt: '请为这篇论文生成详细大纲，需包含章节标题和子标题' },
  { id: 'abstract', name: '写摘要', icon: '📄', agent: 'writing', section: 'abstract', prompt: '请撰写论文摘要，包含研究背景、方法、主要发现和结论' },
  { id: 'intro', name: '写引言', icon: '🚀', agent: 'writing', section: 'intro', prompt: '请撰写论文引言，涵盖研究背景、问题陈述和研究意义' },
  { id: 'related', name: '文献综述', icon: '📚', agent: 'literature', prompt: '请撰写相关文献综述，归纳已有研究的贡献和不足' },
  { id: 'method', name: '方法描述', icon: '🔬', agent: 'writing', section: 'method', prompt: '请描述研究方法，包括实验设计、数据来源和分析流程' },
  { id: 'check', name: '语法检查', icon: '✓', agent: 'refinement', prompt: '请检查以下内容的语法并修正：' }
]

const sendToAI = async (overrides = {}) => {
  if (!aiInput.value.trim() || aiStreaming.value) return
  
  const userMsg = aiInput.value.trim()
  aiMessages.value.push({ role: 'user', content: userMsg, time: Date.now() })
  aiInput.value = ''
  aiStreaming.value = true
  aiAbortController.value = new AbortController()
  
  // 占位 assistant 消息（流式增量）
  const assistantMsg = { role: 'assistant', content: '', time: Date.now(), streaming: true }
  aiMessages.value.push(assistantMsg)
  
  try {
    // 走后端 /api/scholar/stream — 复用 5 Agent 管线 (默认 activeStage)
    const resp = await fetch('/api/scholar/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: userMsg,
        project_id: project.value.id || 0,
        agent: overrides.agent || activeStage.value || 'writing',
        section: overrides.section || activeSection.value || 'intro',
        pipeline: false,
        depth: researchDepth.value,
        client_id: clientId.value,
      }),
      signal: aiAbortController.value.signal,
    })
    
    if (!resp.ok) {
      const errText = await resp.text().catch(() => `HTTP ${resp.status}`)
      throw new Error(errText)
    }
    
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      
      // SSE 解析
      const lines = buffer.split('\n\n')
      buffer = lines.pop() || ''
      
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const evt = JSON.parse(line.slice(6))
          // ── 新版事件处理：outline/写作section_key映射 ──
          if (evt.type === 'outline' && evt.sections) {
            // OutlineAgent 产出结构化大纲 → 更新大纲导航面板
            outline.value = evt.sections
            if (evt.sections.length > 0 && !activeSection.value) {
              activeSection.value = evt.sections[0].id
            }
          } else if (evt.type === 'content' && evt.text) {
            assistantMsg.content += evt.text
            // 如果带 section_key，写入对应章节编辑器并异步持久化到后端
            if (evt.section_key) {
              const existing = sectionContents.value[evt.section_key] || ''
              sectionContents.value[evt.section_key] = existing + evt.text
              // 更新大纲 wordCount
              const sec = outline.value.find(s => s.id === evt.section_key)
              if (sec) {
                sec.wordCount = (sectionContents.value[evt.section_key] || '').length
                sec.status = 'completed'
              }
              // 异步持久化到后端 SQLite（防抖：每 5 秒最多一次）
              if (!_sseSaveTimers[evt.section_key]) {
                _sseSaveTimers[evt.section_key] = setTimeout(() => {
                  if (project.value?.id) {
                    fetch(`/api/scholar/projects/${project.value.id}/section/${evt.section_key}`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ content: sectionContents.value[evt.section_key] || '' }),
                    }).catch(() => {})
                  }
                  delete _sseSaveTimers[evt.section_key]
                }, 5000)
              }
            }
          } else if (evt.type === 'thinking' && evt.message) {
            // thinking 事件不进正文, 只在事件日志显示
            events.value.push({ type: 'thinking', message: evt.message, time: Date.now() })
          } else if (evt.type === 'review') {
            // ReviewerAgent 审稿报告（单 Agent 模式）
            assistantMsg.content += `\n\n${evt.report || ''}`
            events.value.push({ type: 'done', message: `审稿完成：综合评分 ${evt.score || '?'}/10，${evt.total_issues || 0} 个问题`, time: Date.now() })
          } else if (evt.type === 'citation_replace') {
            // 真引用替换结果
            citationReplaced.value = evt.count || 0
            citationReplacedList.value = evt.citations || []
            events.value.push({ type: 'done', message: `真引用替换: ${evt.count || 0}篇真实文献`, time: Date.now() })
          } else if (evt.type === 'citation_verify' && evt.results) {
            // 引用核查结果 → 更新面板
            citationResults.value = evt.results || []
            if ((evt.errors || 0) + (evt.warnings || 0) > 0) {
              activeRightPanel.value = 'citation'
            }
            events.value.push({ type: 'done', message: `引用核查: ${evt.errors || 0}错误 ${evt.warnings || 0}警告`, time: Date.now() })
          } else if (evt.type === 'done') {
            // done 事件可能携带 paper_count → 触发文献刷新
            if (evt.papers != null) {
              refreshLiterature().catch(()=>{})
            }
            // 刷新所有 SSE 防抖定时器，确保内容已落库后再调共识度
            if (project.value?.id) {
              flushSseSaves().then(() => {
                if (hasSectionContent()) {
                  runConsensusAnalysis().catch(()=>{})
                }
              })
            }
          } else if (evt.type === 'error') {
            assistantMsg.content += `\n\n⚠️ ${evt.message}`
          }
        } catch {}
      }
    }
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      assistantMsg.content += '\n\n⏸️ 已停止生成'
    } else {
      assistantMsg.content = `❌ 请求失败: ${e.message}\n\n请检查后端服务 (端口 9119) 是否运行, 以及 API Key 是否配置。`
    }
  } finally {
    assistantMsg.streaming = false
    aiStreaming.value = false
    aiAbortController.value = null
  }
}

const abortStreaming = () => {
  if (aiAbortController.value) {
    aiAbortController.value.abort()
  }
}

const runAICommand = (cmd) => {
  aiInput.value = cmd.prompt
  showAICommands.value = false
  sendToAI()
}

const runStormPipeline = async () => {
  if (aiStreaming.value) return

  // 用当前编辑器内容或论文主题作为全链路输入
  const topic = sectionContents.value[activeSection.value] || project.value.title || '深度学习在医学影像中的应用'
  if (!topic.trim()) {
    alert('请先输入论文主题或写一些内容')
    return
  }

  aiMessages.value.push({ role: 'user', content: `⚡ 全链路写作：${topic.slice(0, 80)}...`, time: Date.now() })
  aiStreaming.value = true

  const assistantMsg = { role: 'assistant', content: '', time: Date.now(), streaming: true }
  aiMessages.value.push(assistantMsg)

  try {
    const resp = await fetch('/api/scholar/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: topic,
        project_id: project.value.id || 0,
        pipeline: true,
        client_id: clientId.value,
      }),
    })

    if (!resp.ok) {
      const errText = await resp.text().catch(() => `HTTP ${resp.status}`)
      throw new Error(errText)
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const evt = JSON.parse(line.slice(6))
          // ── 新版事件处理：outline/stages/section_key映射 ──
          if (evt.type === 'outline' && evt.sections) {
            outline.value = evt.sections
            if (evt.sections.length > 0 && !activeSection.value) {
              activeSection.value = evt.sections[0].id
            }
          } else if (evt.type === 'stage' && evt.pipeline) {
            // pipeline 进度：更新阶段指示器
            const stageId = evt.stage
            const isDone = evt.pipeline === 'done'
            const stageObj = stages.value.find(s => s.id === stageId)
            if (stageObj) {
              stageObj.completed = isDone
            }
            events.value.push({ type: 'stage', message: `${stageId} ${isDone ? '✅' : '▶'}`, time: Date.now() })
          } else if (evt.type === 'content' && evt.text) {
            assistantMsg.content += evt.text
            // 如果带 section_key，写入对应章节编辑器并异步持久化到后端
            if (evt.section_key) {
              const existing = sectionContents.value[evt.section_key] || ''
              sectionContents.value[evt.section_key] = existing + evt.text
              const sec = outline.value.find(s => s.id === evt.section_key)
              if (sec) {
                sec.wordCount = (sectionContents.value[evt.section_key] || '').length
                sec.status = 'completed'
              }
              // 异步持久化到后端 SQLite（防抖：每 5 秒最多一次）
              if (!_sseSaveTimers[evt.section_key]) {
                _sseSaveTimers[evt.section_key] = setTimeout(() => {
                  if (project.value?.id) {
                    fetch(`/api/scholar/projects/${project.value.id}/section/${evt.section_key}`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ content: sectionContents.value[evt.section_key] || '' }),
                    }).catch(() => {})
                  }
                  delete _sseSaveTimers[evt.section_key]
                }, 5000)
              }
            }
          } else if (evt.type === 'review') {
            // ReviewerAgent 审稿报告
            assistantMsg.content += `\n\n${evt.report || ''}`
            events.value.push({ type: 'done', message: `审稿完成：综合评分 ${evt.score || '?'}/10，${evt.total_issues || 0} 个问题`, time: Date.now() })
          } else if (evt.type === 'thinking' && evt.message) {
            events.value.push({ type: 'thinking', message: evt.message, time: Date.now() })
          } else if (evt.type === 'searching' && evt.message) {
            events.value.push({ type: 'searching', message: evt.message, time: Date.now() })
          } else if (evt.type === 'writing' && evt.message) {
            events.value.push({ type: 'writing', message: evt.message, time: Date.now() })
          } else if (evt.type === 'citation_replace') {
            citationReplaced.value = evt.count || 0
            citationReplacedList.value = evt.citations || []
            events.value.push({ type: 'done', message: `真引用替换: ${evt.count || 0}篇真实文献`, time: Date.now() })
          } else if (evt.type === 'citation_verify' && evt.results) {
            citationResults.value = evt.results || []
            if ((evt.errors || 0) + (evt.warnings || 0) > 0) {
              activeRightPanel.value = 'citation'
            }
            events.value.push({ type: 'done', message: `引用核查: ${evt.errors || 0}错误 ${evt.warnings || 0}警告`, time: Date.now() })
          } else if (evt.type === 'checkpoint') {
            // 分步确认：暂停 pipeline，等待用户确认继续下一阶段
            aiStreaming.value = false
            assistantMsg.streaming = false
            events.value.push({
              type: 'checkpoint',
              message: evt.message || `${evt.completed}完成，继续${evt.next}？`,
              stage: evt.stage,
              next: evt.next,
              remaining: evt.remaining || [],
              time: Date.now(),
            })
            showCheckpointConfirm.value = true
            pendingCheckpoint.value = evt
            return
          } else if (evt.type === 'error') {
            assistantMsg.content += `\n\n⚠️ ${evt.message}`
          } else if (evt.type === 'done') {
            assistantMsg.content += `\n\n---\n✅ ${evt.message || '全链路写作完成'}`
            // done 事件可能携带 paper_count → 触发文献刷新
            if (evt.papers != null) {
              refreshLiterature().catch(()=>{})
            }
            // 刷新所有 SSE 防抖定时器，确保内容已落库后再调共识度
            if (project.value?.id) {
              flushSseSaves().then(() => {
                // 全链路写作完成后，自动拉共识度（确保有内容）
                if (hasSectionContent()) {
                  runConsensusAnalysis().catch(()=>{})
                }
              })
            }
          }
        } catch {}
      }
    }
  } catch (e) {
    assistantMsg.content = `❌ 全链路请求失败: ${e.message}\n\n请检查后端服务是否运行, 以及 API Key 是否配置。`
  } finally {
    assistantMsg.streaming = false
    aiStreaming.value = false
  }
}

// Checkpoint resume: 从断点继续后续阶段
const resumePipeline = async () => {
  const cp = pendingCheckpoint.value
  if (!cp || !project.value?.id) { showCheckpointConfirm.value = false; return }
  showCheckpointConfirm.value = false
  aiStreaming.value = true
  const assistantMsg = aiMessages.value[aiMessages.value.length - 1]
  if (assistantMsg) { assistantMsg.streaming = true }
  try {
    const resp = await fetch('/api/scholar/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: project.value.title || '继续写作',
        project_id: project.value.id,
        pipeline: true,
        continue_from: cp.stage,
        client_id: clientId.value,
      }),
    })
    if (!resp.ok) throw new Error(await resp.text())
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const e = JSON.parse(line.slice(6))
          if (e.type === 'content' && e.text) {
            assistantMsg.content += e.text
            if (e.section_key) {
              sectionContents.value[e.section_key] = (sectionContents.value[e.section_key] || '') + e.text
            }
          } else if (e.type === 'stage') {
            const s = stages.value.find(x => x.id === e.stage)
            if (s) s.completed = e.pipeline === 'done'
            events.value.push({ type: 'stage', message: e.stage + (e.pipeline === 'done' ? ' ✅' : ' ▶'), time: Date.now() })
          } else if (e.type === 'checkpoint') {
            pendingCheckpoint.value = e
            showCheckpointConfirm.value = true
            return
          } else if (e.type === 'done') {
            assistantMsg.content += '\n\n---\n✅ ' + (e.message || '全链路写作完成')
            if (e.papers != null) refreshLiterature().catch(()=>{})
          } else if (e.type === 'error') {
            assistantMsg.content += '\n\n⚠️ ' + e.message
          }
        } catch {}
      }
    }
  } catch (e) {
    if (assistantMsg) assistantMsg.content += '\n\n❌ 继续失败: ' + e.message
  } finally {
    aiStreaming.value = false
    if (assistantMsg) assistantMsg.streaming = false
    pendingCheckpoint.value = null
  }
}


const runAIAction = (action) => {
  // 预设动作 → 填入输入框 + 自动发送（按 action 属性路由到对应 Agent）
  aiInput.value = action.prompt + '：'
  sendToAI({ agent: action.agent, section: action.section })
}

// ═══════════════════════════════════════════════════════════════════
// 导出
// ═══════════════════════════════════════════════════════════════════
const showExportPanel = ref(false)

const copyRichText = async () => {
  // 渲染 Markdown → HTML，写入 clipboard (Phase 1.4)
  // 先强制保存当前章节到 DB，确保内容不丢失
  if (activeSection.value && currentContent.value.trim()) {
    sectionContents.value[activeSection.value] = currentContent.value
    await saveCurrentSection()
  }
  const md = buildFullPaper()
  if (!md || md.trim() === `# ${project.value.title || '未命名论文'}`) {
    alert('论文内容为空，请先写作再复制')
    return
  }
  // 简单 MD→HTML 渲染（同 renderedContent 逻辑，但不需要 Vue reactivity）
  let html = md
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/^### (.+)$/gm, '<h3 style="font-size:16px;font-weight:600;margin:12px 0 6px">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 style="font-size:18px;font-weight:700;margin:16px 0 8px">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 style="font-size:20px;font-weight:700;margin:20px 0 10px">$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\[(\d+)\]/g, '<sup>[$1]</sup>')
    .replace(/\n\n/g, '</p><p style="margin:8px 0;line-height:1.7">')
  html = '<div style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:14px;color:#1a1a1a;max-width:800px"><p style="margin:8px 0;line-height:1.7">' + html + '</p></div>'
  try {
    const blob = new Blob([html], { type: 'text/html' })
    const item = new ClipboardItem({ 'text/html': blob, 'text/plain': new Blob([md], { type: 'text/plain' }) })
    await navigator.clipboard.write([item])
    alert('✅ 全文已复制（富文本），可直接粘贴到 Word/飞书/Notion 保留格式')
  } catch (e) {
    // 回退：仅纯文本
    try {
      await navigator.clipboard.writeText(md)
      alert('✅ 全文已复制（纯文本）')
    } catch (e2) {
      alert('❌ 复制失败，请手动复制')
    }
  }
}

const buildFullPaper = () => {
  // 组装完整论文：标题 + 各章节内容 + 参考文献
  let parts = []
  parts.push(`# ${project.value.title || '未命名论文'}`)
  parts.push('')
  
  // 先保存当前编辑框内容到 sectionContents（确保当前章节不丢失）
  if (activeSection.value && currentContent.value.trim()) {
    sectionContents.value[activeSection.value] = currentContent.value
  }
  
  // 按大纲顺序拼接各章节
  for (const sec of outline.value) {
    const content = sectionContents.value[sec.id] || ''
    if (content.trim()) {
      parts.push(`## ${sec.number ? sec.number + ' ' : ''}${sec.title}`)
      parts.push('')
      parts.push(content)
      parts.push('')
    }
  }
  
  // 参考文献
  if (literature.value.length > 0) {
    parts.push('## 参考文献')
    parts.push('')
    literature.value.forEach((lit, idx) => {
      const authors = lit.authors?.join(', ') || 'Unknown'
      parts.push(`[${idx + 1}] ${authors}. ${lit.title}. ${lit.venue || ''} ${lit.year || ''}`)
    })
  }
  
  return parts.join('\n')
}

const doExport = async () => {}  // Moved to WriterExportModal

// ═══════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════
const formatTime = (ts) => {
  const d = new Date(ts)
  return `${d.getHours()}:${d.getMinutes().toString().padStart(2, '0')}`
}

const addSection = () => {
  const newId = 'section-' + Date.now()
  outline.value.push({
    id: newId,
    number: String(outline.value.length + 1),
    title: '新章节',
    wordCount: 0,
    status: 'pending'
  })
  activeSection.value = newId
}

// 章节编辑状态
const editingSectionId = ref(null)
const editSectionNumber = ref('')
const editSectionTitle = ref('')

const startEditSection = (section) => {
  editingSectionId.value = section.id
  editSectionNumber.value = section.number
  editSectionTitle.value = section.title
}

const saveEditSection = (section) => {
  section.number = editSectionNumber.value
  section.title = editSectionTitle.value || '未命名章节'
  editingSectionId.value = null
  scheduleAutosave()
}

const cancelEditSection = () => {
  editingSectionId.value = null
}

const deleteSection = (sectionId) => {
  const idx = outline.value.findIndex(s => s.id === sectionId)
  if (idx === -1) return
  const section = outline.value[idx]
  if (!confirm(`确定删除「${section.title}」？`)) return
  outline.value.splice(idx, 1)
  delete sectionContents.value[sectionId]
  if (activeSection.value === sectionId) {
    activeSection.value = outline.value[0]?.id || null
    currentContent.value = sectionContents.value[activeSection.value] || ''
  }
  // 同步删除后端
  if (project.value.id) {
    fetch(`/api/scholar/projects/${project.value.id}/section/${sectionId}`, { method: 'DELETE' }).catch(()=>{})
  }
}

// ── 逐段修改弹窗（已拆分为 WriterRewriteModal）──
const showRewriteModal = ref(false)
const rewriteTarget = ref({ key: '', title: '' })

const rewriteSection = (sectionId) => {
  const section = outline.value.find(s => s.id === sectionId)
  if (!section) return
  const content = sectionContents.value[sectionId]
  if (!content || content.trim().length < 50) {
    alert('该章节内容太短，无法修改')
    return
  }
  rewriteTarget.value = { key: sectionId, title: section.title || '未命名章节' }
  showRewriteModal.value = true
}

const onRewriteDone = ({ sectionKey, text }) => {
  sectionContents.value[sectionKey] = text
  if (activeSection.value === sectionKey) currentContent.value = text
  const section = outline.value.find(s => s.id === sectionKey)
  if (section) section.wordCount = text.length
}

onMounted(async () => {
  // 动态加载 Agent 列表（不再硬编码）
  await loadStages()
  // 加载搜索源（免费+付费，动态从后端获取）
  await loadSearchSources()
  // 加载项目列表
  await loadProjects()
})

// 加载项目列表
const loadProjects = async () => {
  try {
    const r = await fetch('/api/scholar/projects')
    if (r.ok) {
      const d = await r.json()
      projects.value = d.projects || []
    }
  } catch (e) { console.error('load projects', e) }
}

// 切换项目
const switchProject = async (p) => {
  showProjectSwitcher.value = false
  if (project.value?.id === p.id) return
  isLoadingProject.value = true
  try {
    const r = await fetch(`/api/scholar/projects/${p.id}`)
    if (r.ok) {
      const proj = await r.json()
      project.value = {
        id: proj.id,
        title: proj.title,
        type: proj.paper_type,
        targetWords: proj.target_words,
      }
      outline.value = (proj.outline || []).map(o => ({
        id: o.section_key,
        number: o.section_number,
        title: o.section_title,
        wordCount: o.word_count,
        status: o.status,
      }))
      sectionContents.value = proj.contents || {}
      // P1-7: 恢复上次编辑章节，否则选第一个
      const lastSection = proj.last_section_key || (outline.value.length > 0 ? outline.value[0].id : null)
      if (lastSection && sectionContents.value[lastSection] !== undefined) {
        activeSection.value = lastSection
      } else if (outline.value.length > 0) {
        activeSection.value = outline.value[0].id
      }
      currentContent.value = sectionContents.value[activeSection.value] || ''
      // 文献也同步加载
      const litRes = await fetch(`/api/scholar/projects/${p.id}/literature`)
      if (litRes.ok) {
        const litData = await litRes.json()
        literature.value = (litData.literatures || []).map(l => ({
          id: l.id, title: l.title, authors: l.authors, year: l.year,
          venue: l.venue, abstract: l.abstract, url: l.url, doi: l.doi,
        }))
      }
      // 消息加载
      const msgRes = await fetch(`/api/scholar/projects/${p.id}/messages`)
      if (msgRes.ok) {
        const msgData = await msgRes.json()
        aiMessages.value = (msgData.messages || []).map(m => ({
          role: m.role, content: m.content, time: m.created_at * 1000,
        }))
        if (!aiMessages.value.length) {
          aiMessages.value = [{ role: 'assistant', content: '你好！我是这个论文项目的写作助手。', time: Date.now() }]
        }
      }
      // Load agent-provider bindings
      loadAgentProviders()
      // Load persisted citation verifications
      try {
        const cvRes = await fetch(`/api/scholar/projects/${p.id}/citation-verifications`)
        if (cvRes.ok) {
          const cvData = await cvRes.json()
          if (cvData.verifications?.length) citationResults.value = cvData.verifications
        }
      } catch (_) {}
    }
  } catch (e) { console.error('switch project', e) }
  finally { isLoadingProject.value = false }
}

// 项目选择回调（ProjectList emit）
const onProjectSelected = (proj) => {
  switchProject(proj)
}

// 删除项目（项目切换器中调用）
const deleteProject = async (p) => {
  if (!confirm(`确定删除「${p.title}」？该操作不可恢复。`)) return
  try {
    await fetch(`/api/scholar/projects/${p.id}`, { method: 'DELETE' })
    if (project.value?.id === p.id) backToProjectList()
    await loadProjects()
  } catch (e) { console.error('delete', e) }
}

// 刷新文献（Agent 完成后触发）
const refreshLiterature = async () => {
  if (!project.value?.id) return
  try {
    const litRes = await fetch(`/api/scholar/projects/${project.value.id}/literature`)
    if (litRes.ok) {
      const litData = await litRes.json()
      const existingIds = new Set(literature.value.map(l => l.id))
      for (const l of (litData.literatures || [])) {
        if (!existingIds.has(l.id)) {
          literature.value.push({
            id: l.id, title: l.title, authors: l.authors, year: l.year,
            venue: l.venue, abstract: l.abstract, url: l.url, doi: l.doi,
          })
        }
      }
    }
  } catch (e) { /* 静默失败 */ }
}

// 返回项目列表
const backToProjectList = () => {
  showProjectSwitcher.value = false
  project.value = {}
  outline.value = []
  sectionContents.value = {}
  activeSection.value = null
  currentContent.value = ''
  literature.value = []
  aiMessages.value = []
}

// 防抖自动保存
let saveTimer = null
const _sseSaveTimers = {}  // SSE 写作防抖持久化定时器, key=section_key
const saveStatus = ref('saved')  // 'saved' | 'saving' | 'unsaved'
// P1-6: 引用编号计数器 (citeKey → number)
const citationIndex = ref({})
const citationMaxNum = ref(0)

// 立即刷新所有 SSE 防抖定时器，并保存当前编辑框内容
const flushSseSaves = async () => {
  if (!project.value?.id) return
  const promises = []
  for (const [key, timer] of Object.entries(_sseSaveTimers)) {
    clearTimeout(timer)
    promises.push(
      fetch(`/api/scholar/projects/${project.value.id}/section/${key}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: sectionContents.value[key] || '' }),
      }).catch(() => {})
    )
    delete _sseSaveTimers[key]
  }
  await Promise.all(promises)
  await saveCurrentSection()
}
const scheduleAutosave = () => {
  clearTimeout(saveTimer)
  saveStatus.value = 'unsaved'
  saveTimer = setTimeout(() => {
    saveStatus.value = 'saving'
    saveCurrentSection().finally(() => { saveStatus.value = 'saved' })
  }, 800)
}
const saveNow = () => {
  clearTimeout(saveTimer)
  saveStatus.value = 'saving'
  saveCurrentSection().finally(() => { saveStatus.value = 'saved' })
}
const saveCurrentSection = async () => {
  if (!project.value?.id || !activeSection.value) return
  try {
    await fetch(`/api/scholar/projects/${project.value.id}/section/${activeSection.value}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: currentContent.value }),
    })
  } catch (e) { /* 静默 */ }
}

// 切换章节时同步内容
watch(activeSection, (newKey) => {
  if (newKey) {
    currentContent.value = sectionContents.value[newKey] || ''
  }
})

// 点击外部关闭项目切换器和阶段下拉
const handleClickOutside = (e) => {
  if (projectSwitcherRef.value && !projectSwitcherRef.value.contains(e.target)) {
    showProjectSwitcher.value = false
  }
  // 点击外部关闭 Agent 模型下拉
  if (openAgentDropdown.value && !e.target.closest('.agent-dropdown-anchor')) {
    openAgentDropdown.value = null
  }
  // 点击外部关闭阶段下拉
  if (showStageDropdown.value && !e.target.closest('.stage-dropdown-anchor')) {
    showStageDropdown.value = false
  }
  // P1: 点击外部关闭"更多工具"下拉
  if (showMoreToolsDropdown.value && moreToolsRef.value && !moreToolsRef.value.contains(e.target)) {
    showMoreToolsDropdown.value = false
  }
}
onMounted(() => {
  document.addEventListener('click', handleClickOutside)
})
onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
})

// 辅助函数
const typeIcon = (type) => {
  const m = { '本科论文': '🎓', '硕士论文': '📚', '博士论文': '🔬', '期刊论文': '📰', '会议论文': '🎤', '综述论文': '📖', '开题报告': '📝', '课程论文': '📋', '调研报告': '🔍', '实验报告': '🧪', '案例分析': '💼', '毕业设计': '🎯' }
  return m[type] || '📄'
}
const formatRelativeTime = (ts) => {
  if (!ts) return ''
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return '刚刚'
  if (diff < 3600) return Math.floor(diff / 60) + '分钟前'
  if (diff < 86400) return Math.floor(diff / 3600) + '小时前'
  if (diff < 604800) return Math.floor(diff / 86400) + '天前'
  return new Date(ts * 1000).toLocaleDateString()
}

// 检查是否有章节内容（用于防御性调用评分/查重/共识度等需要论文内容的 API）
const hasSectionContent = () => {
  if (currentContent.value?.trim()) return true
  for (const v of Object.values(sectionContents.value)) {
    if (v?.trim()) return true
  }
  return false
}

// 论文类型
const paperTypes = [
  { id: 'undergrad', name: '本科论文', icon: '🎓', desc: '8000-15000字', defaultWords: 10000 },
  { id: 'course', name: '课程论文', icon: '📋', desc: '3000-6000字', defaultWords: 4000 },
  { id: 'master', name: '硕士论文', icon: '📚', desc: '3-5万字', defaultWords: 30000 },
  { id: 'phd', name: '博士论文', icon: '🔬', desc: '8-15万字', defaultWords: 80000 },
  { id: 'journal', name: '期刊论文', icon: '📰', desc: '5000-10000字', defaultWords: 8000 },
  { id: 'conference', name: '会议论文', icon: '🎤', desc: '4-8页', defaultWords: 5000 },
  { id: 'review', name: '综述论文', icon: '📖', desc: '1-2万字', defaultWords: 15000 },
  { id: 'proposal', name: '开题报告', icon: '📝', desc: '5000-8000字', defaultWords: 6000 },
  { id: 'survey', name: '调研报告', icon: '🔍', desc: '5000-10000字', defaultWords: 8000 },
  { id: 'experiment', name: '实验报告', icon: '🧪', desc: '3000-6000字', defaultWords: 5000 },
  { id: 'case_study', name: '案例分析', icon: '💼', desc: '5000-10000字', defaultWords: 8000 },
  { id: 'graduation_project', name: '毕业设计', icon: '🎯', desc: '10000-20000字', defaultWords: 15000 },
]

// 示例论文
const exampleTitles = [
  '基于深度学习的图像识别研究',
  '大语言模型在教育中的应用',
  '区块链与隐私保护机制综述',
]

// (旧 createProject 逻辑已被 switchProject 后的 API 加载取代)
</script>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
