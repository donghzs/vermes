<!--
  ScholarForge Pro — 专业论文写作界面
  融合 Overleaf 双栏预览 + Scrivener 大纲导航 + Zotero 文献管理
  目标用户：本科/研究生/博士/研究人员
-->
<template>
  <div class="h-full flex flex-col overflow-hidden bg-white dark:bg-gray-900">
    <!-- ═══════════════════════════════════════════════════════════════
         顶部导航栏 — 项目级操作
         ═══════════════════════════════════════════════════════════════ -->
    <header class="h-12 border-b border-gray-200 dark:border-gray-700 flex items-center px-4 bg-white dark:bg-gray-800 shrink-0">
      <!-- 左侧：项目信息 -->
      <div class="flex items-center gap-3 flex-1">
        <button @click="$router.push('/')" class="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg text-gray-500">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"/></svg>
        </button>
        <div class="h-5 w-px bg-gray-200 dark:bg-gray-600"></div>
        <div class="flex items-center gap-2">
          <span class="text-lg">📚</span>
          <div class="relative" ref="projectSwitcherRef">
            <button @click="showProjectSwitcher = !showProjectSwitcher"
              class="flex items-center gap-1.5 px-2 py-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg text-sm font-medium text-gray-800 dark:text-gray-100">
              <span class="max-w-[180px] truncate">{{ project.title || '未命名项目' }}</span>
              <svg class="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
            </button>
            <!-- 项目下拉 -->
            <div v-if="showProjectSwitcher" class="absolute left-0 top-full mt-1 w-80 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl z-50 py-1 max-h-96 overflow-y-auto">
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
        <span class="text-xs px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-700 text-gray-500">{{ project.type }}</span>
      </div>

      <!-- 中间：写作阶段 + 模型选择器 (精简版) -->
      <div class="flex items-center gap-1">
        <!-- 阶段指示器 (只显示当前激活) -->
        <div class="flex items-center bg-gray-100 dark:bg-gray-700 rounded-lg px-2 py-1">
          <span class="text-[10px] text-gray-400 mr-1.5">阶段</span>
          <div class="flex items-center gap-1">
            <button v-for="(stage, idx) in stages" :key="stage.id"
              @click="activeStage = stage.id"
              :class="['w-6 h-6 rounded flex items-center justify-center text-[10px] font-medium transition-colors', activeStage === stage.id ? 'bg-green-600 text-white' : stage.completed ? 'text-green-600 hover:bg-gray-200' : 'text-gray-400 hover:text-gray-600']"
              :title="stage.name"
            >
              {{ stage.completed ? '✓' : idx + 1 }}
            </button>
          </div>
        </div>
        
        <!-- 当前阶段模型选择器 (只显示当前激活阶段的模型) -->
        <div class="relative agent-dropdown-anchor">
          <button @click.stop="toggleAgentDropdown(activeStage, $event)"
            class="flex items-center gap-1 px-2 py-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600 rounded-lg text-[11px] hover:border-green-500 transition-colors"
            :title="getAgentProviderTitle(activeStage)"
          >
            <span class="text-gray-400">{{ stages.find(s=>s.id===activeStage)?.name }}</span>
            <span class="text-gray-600 dark:text-gray-200">{{ agentProviders[activeStage] ? agentProviderLabel(activeStage) : '选模型' }}</span>
            <svg class="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
          </button>
          
          <!-- 模型下拉 -->
          <div v-if="openAgentDropdown === activeStage"
            class="absolute top-full left-0 mt-1 w-[200px] bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl z-50 max-h-[280px] flex flex-col overflow-hidden">
            <div class="flex-1 overflow-y-auto py-0.5">
              <div v-if="!configuredProviders.length" class="text-[11px] text-gray-400 py-4 px-3 text-center">
                暂未配置模型 Key<br/><span class="text-[10px]">请在设置中添加</span>
              </div>
              <template v-for="p in configuredProviders" :key="p.key">
                <div class="px-2 pt-1.5 pb-0.5 text-[9px] uppercase text-gray-400 font-semibold">{{ p.key }}</div>
                <button v-for="m in getModelsForProvider(p.key)" :key="p.key + '-' + m"
                  @click="setAgentProvider(activeStage, p.key, m); closeAgentDropdown()"
                  :class="['w-full text-left px-2.5 py-1 text-[11px] transition-colors', agentProviders[activeStage]?.provider === p.key && agentProviders[activeStage]?.model === m ? 'bg-green-50 text-green-700 font-medium' : 'text-gray-600 hover:bg-gray-100']"
                >
                  {{ m }}
                </button>
              </template>
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧：精简工具栏 -->
      <div class="flex items-center gap-1.5 flex-1 justify-end">
        <!-- 文献 (图标+数字) -->
        <button @click="showLiteraturePanel = !showLiteraturePanel; showAIPanel = false" 
          :class="['p-2 rounded-lg transition-colors relative', showLiteraturePanel ? 'bg-blue-50 text-blue-600' : 'text-gray-500 hover:bg-gray-100']"
          title="文献库">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/></svg>
          <span v-if="literatureCount > 0" class="absolute -top-0.5 -right-0.5 w-4 h-4 bg-blue-600 text-white text-[9px] rounded-full flex items-center justify-center">{{ literatureCount }}</span>
        </button>
        
        <!-- AI 助手 (图标) -->
        <button @click="showAIPanel = !showAIPanel; showLiteraturePanel = false"
          :class="['p-2 rounded-lg transition-colors relative', showAIPanel ? 'bg-purple-50 text-purple-600' : 'text-gray-500 hover:bg-gray-100']"
          title="AI 助手">
          <span class="text-base">🤖</span>
        </button>
        
        <!-- 引用核查 (图标+警告数字) -->
        <button @click="showCitationPanel = !showCitationPanel; showConsensusPanel = false"
          :class="['p-2 rounded-lg transition-colors relative', showCitationPanel ? 'bg-amber-50 text-amber-600' : 'text-gray-500 hover:bg-gray-100']"
          title="引用核查结果">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
          <span v-if="citationErrors + citationWarnings > 0" 
            :class="['absolute -top-0.5 -right-0.5 w-4 h-4 text-white text-[9px] rounded-full flex items-center justify-center', citationErrors > 0 ? 'bg-red-500' : 'bg-amber-500']">
            {{ citationErrors + citationWarnings }}
          </span>
        </button>

        <!-- 共识度 (图标+徽章) -->
        <button @click="showConsensusPanel = !showConsensusPanel; showCitationPanel = false"
          :class="['p-2 rounded-lg transition-colors relative', showConsensusPanel ? 'bg-green-50 text-green-600' : 'text-gray-500 hover:bg-gray-100']"
          title="共识度分析">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
          <span v-if="consensusResults.length > 0" 
            class="absolute -top-0.5 -right-0.5 w-4 h-4 bg-green-500 text-white text-[9px] rounded-full flex items-center justify-center">
            {{ consensusResults.length }}
          </span>
        </button>

        <!-- 查重 + AIGC 检测 (P0-2) -->
        <button @click="runPlagcheck" :disabled="plagLoading"
          :class="['p-2 rounded-lg transition-colors relative', showPlagPanel ? 'bg-purple-50 text-purple-600' : 'text-gray-500 hover:bg-gray-100']"
          title="查重 + AIGC 检测">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"/></svg>
          <span v-if="plagResult" 
            :class="['absolute -top-0.5 -right-0.5 px-1 text-white text-[9px] rounded-full flex items-center justify-center', plagResult.overall_similarity > 0.3 ? 'bg-red-500' : plagResult.aigc_overall_ratio > 0.4 ? 'bg-amber-500' : 'bg-green-500']">
            {{ Math.round(Math.max(plagResult.overall_similarity, plagResult.aigc_overall_ratio) * 100) }}%
          </span>
        </button>
        
        <div class="h-4 w-px bg-gray-200 mx-1"></div>
        
        <!-- 一键复制富文本 (Phase 1.4 — 粘贴到 Word/飞书/Notion 零格式损失) -->
        <button @click="copyRichText" class="px-3 py-1.5 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-600 dark:text-gray-300 rounded-lg text-xs font-medium flex items-center gap-1.5 transition-colors" title="复制为富文本，粘贴到 Word/飞书/Notion 保留格式">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
          复制富文本
        </button>

        <!-- 导出 -->
        <button @click="showExportPanel = true" class="px-3 py-1.5 bg-green-600 hover:bg-green-700 text-white rounded-lg text-xs font-medium flex items-center gap-1.5">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
          导出
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
    <div v-else class="flex-1 flex overflow-hidden">
      
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
            <button @click="editor.format('bold')" class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-600 dark:text-gray-300" title="粗体">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 12h8a4 4 0 100-8H6v8zm0 0h10a4 4 0 110 8H6v-8z"/></svg>
            </button>
            <button @click="editor.format('italic')" class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-600 dark:text-gray-300" title="斜体">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>
            </button>
            <button @click="editor.format('heading')" class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-600 dark:text-gray-300" title="标题">
              <span class="text-xs font-bold">H</span>
            </button>
          </div>
          <div class="h-4 w-px bg-gray-300 dark:bg-gray-600 mx-1"></div>
          <div class="flex items-center gap-0.5">
            <button @click="insertCitation" class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-600 dark:text-gray-300 flex items-center gap-1" title="插入引用">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"/></svg>
              <span class="text-xs">引用</span>
            </button>
            <button @click="insertFormula" class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-600 dark:text-gray-300" title="插入公式">
              <span class="text-xs font-serif italic">∑</span>
            </button>
            <button @click="insertTable" class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-600 dark:text-gray-300" title="插入表格">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
            </button>
          </div>
          <div class="flex-1"></div>
          <div class="flex items-center gap-2">
            <button @click="viewMode = 'edit'" :class="['px-2 py-1 rounded text-xs', viewMode === 'edit' ? 'bg-white dark:bg-gray-700 shadow-sm text-gray-800 dark:text-gray-100' : 'text-gray-500 hover:text-gray-700']">编辑</button>
            <button @click="viewMode = 'split'" :class="['px-2 py-1 rounded text-xs', viewMode === 'split' ? 'bg-white dark:bg-gray-700 shadow-sm text-gray-800 dark:text-gray-100' : 'text-gray-500 hover:text-gray-700']">分栏</button>
            <button @click="viewMode = 'preview'" :class="['px-2 py-1 rounded text-xs', viewMode === 'preview' ? 'bg-white dark:bg-gray-700 shadow-sm text-gray-800 dark:text-gray-100' : 'text-gray-500 hover:text-gray-700']">预览</button>
          </div>
        </div>

        <!-- 编辑器主体 -->
        <div class="flex-1 flex overflow-hidden">
          <!-- 编辑区 -->
          <div :class="['flex flex-col', viewMode === 'split' ? 'w-1/2' : viewMode === 'edit' ? 'w-full' : 'hidden']">
            <div class="flex items-center gap-2 px-3 py-1.5 bg-gray-100 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 text-xs">
              <button @click="pasteAndParse" class="flex items-center gap-1 px-2 py-0.5 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 rounded hover:bg-green-200 transition-colors">
                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"/></svg>
                粘贴并识别结构
              </button>
              <span class="text-gray-400">支持自动识别：标题/章节/参考文献</span>
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
            ></textarea>
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
          <div v-if="viewMode !== 'edit'" :class="['border-l border-gray-200 dark:border-gray-700 flex flex-col bg-white dark:bg-gray-900', viewMode === 'split' ? 'w-1/2' : 'w-full']">
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
            <button 
              @click="sendToAI" 
              :disabled="!aiInput.trim() || aiStreaming"
              class="px-5 py-2.5 bg-green-600 hover:bg-green-700 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl text-sm font-medium flex items-center gap-2 transition-all"
            >
              <span v-if="aiStreaming" class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
              <span v-else>🚀</span>
              {{ aiStreaming ? '生成中...' : 'AI 写作' }}
            </button>
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
           │ 右栏：文献库 / AI 助手 (可折叠)
           └─────────────────────────────────────────────────────────────┘ -->
      <div :class="['border-l border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 flex flex-col shrink-0 transition-all duration-200', rightCollapsed ? 'w-10' : 'w-80']">
        <!-- 折叠按钮 -->
        <button @click="toggleRightBar" class="h-8 border-b border-gray-200 dark:border-gray-700 flex items-center justify-center text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors" title="面板">
          <svg :class="['w-4 h-4 transition-transform', rightCollapsed ? 'rotate-180' : '']" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" width="18" height="18" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
        </button>
        <template v-if="!rightCollapsed">
        
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
        <template v-if="showLiteraturePanel">
          <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">文献库</span>
            <div class="flex items-center gap-1">
              <button @click="showSourceSelector = !showSourceSelector" class="p-1 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-400 hover:text-gray-600 text-[10px]" title="搜索源">
                {{ activeSources.length }}/{{ allSearchSources.length }} 源
              </button>
              <button @click="searchLiterature" class="p-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs flex items-center gap-1">
                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
                检索
              </button>
            </div>
          </div>
          <!-- 搜索源选择器 -->
          <div v-if="showSourceSelector" class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-900 space-y-1">
            <div class="text-[10px] text-gray-400 mb-1">搜索源（{{ activeSources.length }}/{{ allSearchSources.length }}）</div>
            <label v-for="src in allSearchSources" :key="src.id"
              :class="['flex items-center gap-1.5 px-1.5 py-0.5 rounded text-[10px] cursor-pointer', src.paid && !src.configured ? 'opacity-50' : '']">
              <input type="checkbox" :value="src.id" v-model="activeSources"
                :disabled="src.paid && !src.configured"
                class="w-3 h-3 rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
              <span :class="src.paid ? 'text-amber-600' : 'text-gray-600 dark:text-gray-300'">
                {{ src.icon }} {{ src.label }}
                <span v-if="!src.accessible && !src.paid" class="text-[8px] text-red-400 ml-0.5">离线</span>
              </span>
              <button v-if="src.paid && !src.configured" @click.stop.prevent="openPaidSourceConfig(src)"
                class="ml-auto text-[9px] px-1.5 py-0.5 bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 rounded hover:bg-amber-200">
                配置
              </button>
              <span v-else-if="src.paid" class="ml-auto text-[9px] text-green-600">✓已配置</span>
            </label>
          </div>
          <!-- 付费源配置弹窗 -->
          <div v-if="showPaidSourceConfig" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="showPaidSourceConfig = false">
            <div class="bg-white dark:bg-gray-800 rounded-xl p-6 w-96 max-w-[90vw] shadow-2xl">
              <div class="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-1">
                {{ paidSourceConfigTarget?.icon }} 配置 {{ paidSourceConfigTarget?.label }}
              </div>
              <div class="text-xs text-gray-500 mb-3">{{ paidSourceConfigTarget?.paid ? '付费文献源，需填入 API Key' : '' }}</div>
              
              <!-- API Key -->
              <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">API Key</label>
              <input v-model="paidSourceApiKey" type="password" placeholder="输入 API Key..."
                class="w-full px-3 py-2 text-xs bg-gray-50 dark:bg-gray-900 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:border-amber-500 dark:text-gray-100 mb-3" />
              
              <!-- CNKI 网关 URL（仅 CNKI） -->
              <template v-if="paidSourceConfigTarget?.needsGatewayUrl">
                <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">网关 URL</label>
                <input v-model="paidSourceGatewayUrl" type="text" placeholder="https://your-cnki-gateway.com/api"
                  class="w-full px-3 py-2 text-xs bg-gray-50 dark:bg-gray-900 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:border-amber-500 dark:text-gray-100 mb-3" />
                <div class="text-[10px] text-gray-400 mb-3">知网无公开API，需自建网关服务。可参考开源网关方案。</div>
              </template>
              
              <!-- 注册链接 -->
              <div v-if="paidSourceConfigTarget?.registerUrl" class="text-[10px] text-blue-500 mb-3">
                <a :href="paidSourceConfigTarget.registerUrl" target="_blank" class="hover:underline">📎 前往注册获取 API Key →</a>
              </div>
              
              <div class="flex gap-2 justify-end">
                <button @click="showPaidSourceConfig = false" class="px-3 py-1.5 text-xs text-gray-500 hover:text-gray-700 rounded-lg">取消</button>
                <button @click="savePaidSourceKey" :disabled="!paidSourceApiKey.trim()"
                  class="px-4 py-1.5 bg-amber-600 hover:bg-amber-700 text-white rounded-lg text-xs font-medium disabled:opacity-40">激活</button>
              </div>
            </div>
          </div>
          <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700">
            <input v-model="literatureSearch" placeholder="搜索文献..." class="w-full px-3 py-1.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-xs focus:outline-none focus:border-blue-500 dark:text-gray-100"/>
          </div>
          <div class="flex-1 overflow-y-auto py-2">
            <div v-if="!literature.length" class="px-3 py-8 text-center">
              <div class="text-3xl mb-2">📭</div>
              <p class="text-xs text-gray-400 mb-2">文献库为空</p>
              <p class="text-[10px] text-gray-400 mb-3">运行「文献综述」Agent 或点击上方检索按钮</p>
              <button @click="searchLiterature" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs">🔍 开始检索</button>
            </div>
            <div v-for="paper in filteredLiterature" :key="paper.id" 
              @click="togglePaperExpand(paper)"
              :class="['px-3 py-2.5 cursor-pointer transition-all', expandedPaper?.id === paper.id ? 'bg-blue-50 dark:bg-blue-900/20' : 'hover:bg-white dark:hover:bg-gray-700/50 border-b border-gray-100 dark:border-gray-700/50']">
              <div class="flex items-start gap-2">
                <span class="text-[10px] px-1 py-0.5 rounded bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 shrink-0 mt-0.5 font-mono">{{ paper.year || '?' }}</span>
                <div class="flex-1 min-w-0">
                  <div class="text-xs font-medium text-gray-800 dark:text-gray-200 line-clamp-2 leading-snug">{{ paper.title }}</div>
                  <div class="text-[10px] text-gray-500 mt-1">{{ paper.authors?.slice(0, 3).join(', ') || '未知作者' }}{{ paper.authors?.length > 3 ? ' 等' : '' }}</div>
                  <div class="flex items-center gap-2 mt-1.5">
                    <span v-if="paper.venue" class="text-[10px] px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-gray-500 truncate max-w-[120px]">{{ paper.venue }}</span>
                    <span class="text-[10px] px-1.5 py-0.5 rounded text-gray-400" :class="sourceBadgeClass(paper.source)">{{ paper.source || '未知源' }}</span>
                    <button @click.stop="copyBibtex(paper)" class="text-[10px] text-gray-400 hover:text-blue-600" title="复制 BibTeX">📋</button>
                    <button @click.stop="insertCitation(paper)" class="text-[10px] text-blue-600 hover:text-blue-700">引用</button>
                  </div>
                </div>
                <svg :class="['w-3 h-3 text-gray-400 mt-1 shrink-0 transition-transform', expandedPaper?.id === paper.id ? 'rotate-180' : '']" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
              </div>
              <!-- 展开摘要 + BibTeX -->
              <div v-if="expandedPaper?.id === paper.id" class="mt-2 pt-2 border-t border-gray-100 dark:border-gray-700/50">
                <p v-if="paper.abstract" class="text-[10px] text-gray-500 dark:text-gray-400 leading-relaxed mb-2 max-h-24 overflow-y-auto">{{ paper.abstract }}</p>
                <div class="bg-gray-950 text-green-400 text-[10px] p-2 rounded font-mono relative group">
                  <button @click.stop="copyBibtex(paper)" class="absolute top-1 right-1 px-1.5 py-0.5 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-[9px] opacity-0 group-hover:opacity-100 transition-opacity">复制</button>
                  <pre class="whitespace-pre-wrap" v-text="'@article{' + (paper.citeKey || 'ref') + ',\n  title={' + (paper.title || '') + '},\n  author={' + (paper.authors?.join(' and ') || '') + '},\n  year={' + (paper.year || '') + '},\n  journal={' + (paper.venue || '') + '}\n}'"></pre>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- 引用核查结果面板 -->
        <template v-if="showCitationPanel">
          <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">引用核查</span>
            <div class="flex items-center gap-1">
              <span v-if="citationErrors > 0" class="w-2 h-2 rounded-full bg-red-500"></span>
              <span v-if="citationWarnings > 0" class="w-2 h-2 rounded-full bg-amber-500"></span>
              <span class="text-[10px] text-gray-400">
                <span v-if="citationErrors > 0" class="text-red-500">{{ citationErrors }}错误</span>
                <span v-if="citationErrors > 0 && citationWarnings > 0"> / </span>
                <span v-if="citationWarnings > 0" class="text-amber-500">{{ citationWarnings }}警告</span>
                <span v-if="citationErrors === 0 && citationWarnings === 0">全部通过</span>
              </span>
            </div>
          </div>
          <div class="flex-1 overflow-y-auto py-2">
            <div v-if="!citationResults.length" class="px-3 py-8 text-center">
              <div class="text-3xl mb-2">🔍</div>
              <p class="text-xs text-gray-400">尚未运行引用核查</p>
              <p class="text-[10px] text-gray-400 mt-1">运行「润色」Agent 将自动核查</p>
            </div>
            <div v-for="r in citationResults" :key="r.ref"
              :class="['mx-2 mb-1 px-2.5 py-2 rounded-lg text-xs', r.score >= 7 ? 'bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800' : r.score >= 3 ? 'bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800' : 'bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800']">
              <div class="flex items-start gap-1.5">
                <span :class="['shrink-0 font-mono text-[10px] px-1 py-0.5 rounded', r.score >= 7 ? 'bg-green-200 dark:bg-green-800 text-green-800 dark:text-green-200' : r.score >= 3 ? 'bg-amber-200 dark:bg-amber-800 text-amber-800 dark:text-amber-200' : 'bg-red-200 dark:bg-red-800 text-red-800 dark:text-red-200']">[{{ r.ref }}]</span>
                <div class="flex-1 min-w-0">
                  <div :class="r.score >= 7 ? 'text-green-700 dark:text-green-300' : r.score >= 3 ? 'text-amber-700 dark:text-amber-300' : 'text-red-700 dark:text-red-300'">{{ r.reason }}</div>
                  <div class="flex items-center gap-2 mt-1">
                    <div class="flex-1 h-1 bg-gray-200 dark:bg-gray-700 rounded-full">
                      <div :class="['h-1 rounded-full', r.score >= 7 ? 'bg-green-500' : r.score >= 3 ? 'bg-amber-500' : 'bg-red-500']" :style="{ width: (r.score * 10) + '%' }"></div>
                    </div>
                    <span class="text-[9px] text-gray-400">{{ r.score }}/10</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- 共识度分析面板 -->
        <template v-if="showConsensusPanel">
          <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">共识度分析</span>
            <button @click="runConsensusAnalysis" :disabled="consensusLoading"
              class="px-2 py-0.5 bg-green-600 hover:bg-green-700 disabled:opacity-40 text-white rounded text-[10px] flex items-center gap-1">
              <span v-if="consensusLoading" class="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
              <span v-else>🔄</span>
              分析
            </button>
          </div>
          <div class="flex-1 overflow-y-auto py-2">
            <div v-if="!consensusResults.length && !consensusLoading" class="px-3 py-8 text-center">
              <div class="text-3xl mb-2">📊</div>
              <p class="text-xs text-gray-400">尚未分析共识度</p>
              <p class="text-[10px] text-gray-400 mt-1">点击上方「分析」按钮，评估文献对论文论断的支持度</p>
            </div>
            <div v-for="(r, idx) in consensusResults" :key="idx" class="mx-2 mb-2 bg-white dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600 overflow-hidden">
              <!-- 论断 -->
              <div class="px-2.5 py-2 border-b border-gray-100 dark:border-gray-600">
                <p class="text-xs text-gray-800 dark:text-gray-200 leading-relaxed">{{ r.claim }}</p>
              </div>
              <!-- 共识度柱 -->
              <div class="px-2.5 py-2">
                <div class="flex items-center gap-2 mb-2">
                  <div class="flex-1 h-2.5 bg-gray-100 dark:bg-gray-600 rounded-full overflow-hidden flex">
                    <div v-if="r.support > 0" :style="{ width: (r.support / r.total * 100) + '%' }"
                      class="h-full bg-green-500 transition-all" :title="'支持 ' + r.support"></div>
                    <div v-if="r.neutral > 0" :style="{ width: (r.neutral / r.total * 100) + '%' }"
                      class="h-full bg-gray-400 transition-all" :title="'中立 ' + r.neutral"></div>
                    <div v-if="r.oppose > 0" :style="{ width: (r.oppose / r.total * 100) + '%' }"
                      class="h-full bg-red-500 transition-all" :title="'反对 ' + r.oppose"></div>
                  </div>
                  <span :class="['text-xs font-bold', r.confidence === 'high' ? 'text-green-600' : r.confidence === 'medium' ? 'text-amber-600' : 'text-red-600']">
                    {{ r.consensus_pct }}%
                  </span>
                </div>
                <!-- 数字徽章 -->
                <div class="flex items-center gap-3 text-[10px]">
                  <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-green-500 inline-block"></span> 👍 {{ r.support }}</span>
                  <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-gray-400 inline-block"></span> 😐 {{ r.neutral }}</span>
                  <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-red-500 inline-block"></span> 👎 {{ r.oppose }}</span>
                </div>
                <!-- 置信度 -->
                <div class="mt-1.5 flex items-center gap-1">
                  <span class="text-[9px] text-gray-400">置信度:</span>
                  <span :class="['text-[10px] font-medium px-1.5 py-0.5 rounded', r.confidence === 'high' ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400' : r.confidence === 'medium' ? 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400' : 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400']">
                    {{ r.confidence === 'high' ? '高共识' : r.confidence === 'medium' ? '中共识' : '低共识' }}
                  </span>
                </div>
                <!-- 逐文献立场（可展开） -->
                <button v-if="r.per_paper?.length" @click="r._expanded = !r._expanded"
                  class="mt-1.5 text-[9px] text-gray-400 hover:text-gray-600 flex items-center gap-1">
                  <svg :class="['w-3 h-3 transition-transform', r._expanded ? 'rotate-180' : '']" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                  逐文献详情 ({{ r.per_paper.length }}篇)
                </button>
                <div v-if="r._expanded && r.per_paper?.length" class="mt-1.5 space-y-1 max-h-48 overflow-y-auto">
                  <div v-for="pp in r.per_paper" :key="pp.ref"
                    :class="['px-2 py-1 rounded text-[10px] flex items-start gap-1.5', pp.stance === 'support' ? 'bg-green-50 dark:bg-green-900/20' : pp.stance === 'oppose' ? 'bg-red-50 dark:bg-red-900/20' : 'bg-gray-50 dark:bg-gray-800']">
                    <span :class="['shrink-0 font-mono text-[9px]', pp.stance === 'support' ? 'text-green-600' : pp.stance === 'oppose' ? 'text-red-600' : 'text-gray-500']">[{{ pp.ref }}]</span>
                    <span :class="pp.stance === 'support' ? 'text-green-700 dark:text-green-400' : pp.stance === 'oppose' ? 'text-red-700 dark:text-red-400' : 'text-gray-600 dark:text-gray-400'">{{ pp.reason }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- AI 助手面板 -->
        <template v-if="showAIPanel">
          <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">AI 写作助手</span>
            <div class="flex items-center gap-1">
              <span class="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
              <span class="text-[10px] text-green-600">在线</span>
            </div>
          </div>
          <div class="flex-1 overflow-y-auto p-3 space-y-3">
            <!-- AI 对话历史 -->
            <div v-for="(msg, idx) in aiMessages" :key="idx" 
              :class="['text-xs', msg.role === 'user' ? 'ml-4' : 'mr-4']">
              <div :class="['p-2.5 rounded-lg', msg.role === 'user' ? 'bg-green-100 dark:bg-green-900/30 text-gray-800 dark:text-gray-200' : 'bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 text-gray-700 dark:text-gray-300']">
                <div class="flex items-center gap-1 mb-1 text-[10px] text-gray-400">
                  <span>{{ msg.role === 'user' ? '👤 你' : '🤖 AI' }}</span>
                  <span>{{ formatTime(msg.time) }}</span>
                </div>
                <div class="whitespace-pre-wrap">{{ msg.content }}</div>
              </div>
            </div>
          </div>
          <!-- AI 快捷操作 -->
          <div class="p-3 border-t border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-700/50">
            <!-- 研究深度选择器 -->
            <div class="mb-2 flex items-center gap-1.5">
              <span class="text-[10px] text-gray-500">研究深度:</span>
              <button v-for="d in researchDepths" :key="d.value" @click="researchDepth = d.value"
                :class="['px-2 py-1 rounded text-[10px] transition-colors', researchDepth === d.value ? 'bg-purple-600 text-white' : 'bg-white dark:bg-gray-600 text-gray-500 hover:text-gray-700']">
                {{ d.label }}
              </button>
            </div>
            <!-- STORM 全链路按钮 -->
            <button @click="runStormPipeline" :disabled="aiStreaming"
              class="w-full mb-2 px-3 py-2 bg-gradient-to-r from-purple-600 to-indigo-500 hover:from-purple-500 hover:to-indigo-400 text-white rounded-lg text-xs font-semibold transition-all disabled:opacity-40 flex items-center justify-center gap-1.5">
              <span>⚡</span> STORM 全链路写作
            </button>
            <div class="grid grid-cols-2 gap-2">
              <button v-for="action in aiQuickActions" :key="action.id" @click="runAIAction(action)"
                class="px-2 py-1.5 bg-white dark:bg-gray-600 border border-gray-200 dark:border-gray-500 rounded text-[10px] text-gray-600 dark:text-gray-300 hover:border-purple-500 hover:text-purple-600 transition-colors text-left">
                {{ action.icon }} {{ action.name }}
              </button>
            </div>
          </div>
        </template>

        <!-- P0-2: 查重 + AIGC 检测面板 -->
        <template v-if="showPlagPanel">
          <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">查重 + AIGC</span>
            <button @click="runPlagcheck" :disabled="plagLoading"
              class="px-2 py-0.5 bg-purple-600 hover:bg-purple-700 disabled:opacity-40 text-white rounded text-[10px] flex items-center gap-1">
              <span v-if="plagLoading" class="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
              <span v-else>🔄</span>
              检测
            </button>
          </div>
          <div class="flex-1 overflow-y-auto py-2">
            <div v-if="!plagResult" class="px-3 py-8 text-center">
              <div class="text-3xl mb-2">🔍</div>
              <p class="text-xs text-gray-400">尚未检测</p>
              <p class="text-[10px] text-gray-400 mt-1">点击上方「检测」按钮进行查重和 AIGC 分析</p>
            </div>
            <template v-if="plagResult">
              <!-- 综合评分卡片 -->
              <div class="mx-2 mb-3 p-3 bg-white dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600">
                <div class="flex items-center gap-3 mb-2">
                  <!-- 重复率环 -->
                  <div class="relative w-14 h-14 flex-shrink-0">
                    <svg class="w-14 h-14 -rotate-90">
                      <circle cx="28" cy="28" r="24" fill="none" stroke="currentColor" stroke-width="4" class="text-gray-200 dark:text-gray-600" />
                      <circle cx="28" cy="28" r="24" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"
                        :stroke="plagResult.overall_similarity > 0.3 ? '#ef4444' : '#22c55e'"
                        :stroke-dasharray="Math.round(plagResult.overall_similarity * 151) + ' 151'" />
                    </svg>
                    <span class="absolute inset-0 flex items-center justify-center text-xs font-bold text-gray-700 dark:text-gray-200">{{ Math.round(plagResult.overall_similarity * 100) }}%</span>
                  </div>
                  <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 mb-1">
                      <span class="text-sm font-semibold text-gray-800 dark:text-gray-100">查重率</span>
                      <span :class="['text-xs px-1.5 py-0.5 rounded', plagResult.overall_similarity > 0.3 ? 'bg-red-100 text-red-700' : plagResult.overall_similarity > 0.15 ? 'bg-amber-100 text-amber-700' : 'bg-green-100 text-green-700']">
                        {{ plagResult.overall_similarity > 0.3 ? '偏高' : plagResult.overall_similarity > 0.15 ? '中等' : '良好' }}
                      </span>
                    </div>
                    <div class="text-[10px] text-gray-500">{{ plagResult.total_chars.toLocaleString() }} 字 · {{ plagResult.total_paragraphs }} 段</div>
                  </div>
                </div>
                <!-- AIGC 痕迹 -->
                <div class="mt-2 pt-2 border-t border-gray-100 dark:border-gray-600">
                  <div class="flex items-center justify-between mb-1">
                    <span class="text-[11px] text-gray-600 dark:text-gray-300">🤖 AIGC 痕迹</span>
                    <span :class="['text-xs font-bold', plagResult.aigc_overall_ratio > 0.4 ? 'text-red-600' : plagResult.aigc_overall_ratio > 0.2 ? 'text-amber-600' : 'text-green-600']">
                      {{ Math.round(plagResult.aigc_overall_ratio * 100) }}%
                    </span>
                  </div>
                  <div class="w-full h-1.5 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden">
                    <div :class="['h-full rounded-full transition-all', plagResult.aigc_overall_ratio > 0.4 ? 'bg-red-500' : plagResult.aigc_overall_ratio > 0.2 ? 'bg-amber-500' : 'bg-green-500']"
                      :style="{ width: Math.round(plagResult.aigc_overall_ratio * 100) + '%' }"></div>
                  </div>
                  <div class="flex justify-between text-[9px] text-gray-400 mt-0.5">
                    <span>人类写作</span><span>AI 辅助</span><span>AI 生成</span>
                  </div>
                </div>
              </div>

              <!-- 重复片段 -->
              <div v-if="plagResult.plag_results?.length" class="mx-2 mb-2">
                <div class="flex items-center gap-1.5 mb-1.5">
                  <span class="text-[10px] font-medium text-red-600">🔴 重复段落 ({{ plagResult.plag_results.length }})</span>
                </div>
                <div v-for="(p, i) in plagResult.plag_results" :key="'plag'+i"
                  class="mb-1.5 p-2 bg-red-50 dark:bg-red-900/15 rounded border border-red-200 dark:border-red-800">
                  <p class="text-xs text-red-800 dark:text-red-300 line-clamp-3">{{ p.text }}</p>
                  <div class="mt-1 text-[9px] text-red-500">相似度 {{ Math.round(p.score * 100) }}% · {{ p.length }}字</div>
                </div>
              </div>

              <!-- AIGC 段落 -->
              <div v-if="plagResult.aigc_results?.length" class="mx-2 mb-2">
                <div class="flex items-center gap-1.5 mb-1.5">
                  <span class="text-[10px] font-medium text-amber-600">🟡 AI 痕迹段落 ({{ plagResult.aigc_results.length }})</span>
                </div>
                <div v-for="(a, i) in plagResult.aigc_results" :key="'aigc'+i"
                  class="mb-1.5 p-2 bg-amber-50 dark:bg-amber-900/15 rounded border border-amber-200 dark:border-amber-800">
                  <div class="flex items-center justify-between mb-1">
                    <div class="flex items-center gap-1">
                      <span v-for="f in a.features" :key="f" class="text-[8px] px-1 py-0.5 bg-amber-200 dark:bg-amber-800 text-amber-700 dark:text-amber-300 rounded">{{ f }}</span>
                    </div>
                    <span class="text-[9px] font-bold text-amber-600">{{ Math.round(a.aigc_probability * 100) }}%</span>
                  </div>
                  <p class="text-xs text-amber-800 dark:text-amber-300 line-clamp-3">{{ a.text }}</p>
                </div>
              </div>

              <!-- 建议 -->
              <div v-if="plagResult.suggestions?.length" class="mx-2 mb-2">
                <div class="text-[10px] font-medium text-gray-500 mb-1.5">💡 改进建议</div>
                <div v-for="(s, i) in plagResult.suggestions" :key="'sug'+i"
                  class="mb-1 p-2 bg-blue-50 dark:bg-blue-900/15 rounded text-[11px] text-blue-700 dark:text-blue-300 leading-relaxed">
                  {{ s }}
                </div>
              </div>
            </template>
          </div>
        </template>
        </template>
      </div>
    </div>

    <!-- ═══════════════════════════════════════════════════════════════
         导出面板 (Modal)
         ═══════════════════════════════════════════════════════════════ -->
    <Teleport to="body">
      <div v-if="showExportPanel" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" @click.self="showExportPanel = false">
        <div class="bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-[600px] max-h-[80vh] flex flex-col">
          <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <h3 class="text-sm font-semibold text-gray-800 dark:text-gray-100">导出论文</h3>
            <button @click="showExportPanel = false" class="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded text-gray-400">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="p-4 flex-1 overflow-y-auto">
            <div class="grid grid-cols-3 gap-3 mb-4">
              <button v-for="fmt in exportFormats" :key="fmt.id" @click="exportFormat = fmt.id"
                :class="['p-3 border rounded-lg text-left transition-all', exportFormat === fmt.id ? 'border-green-500 bg-green-50 dark:bg-green-900/20' : 'border-gray-200 dark:border-gray-700 hover:border-gray-300']">
                <div class="text-2xl mb-1">{{ fmt.icon }}</div>
                <div class="text-xs font-medium text-gray-800 dark:text-gray-200">{{ fmt.name }}</div>
                <div class="text-[10px] text-gray-500 mt-0.5">{{ fmt.desc }}</div>
              </button>
            </div>
            <div class="space-y-3">
              <div>
                <label class="text-xs text-gray-500 mb-1 block">文件名</label>
                <input v-model="exportFilename" class="w-full px-3 py-2 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-sm dark:text-gray-100"/>
              </div>
              <div v-if="exportFormat === 'latex'">
                <label class="text-xs text-gray-500 mb-1 block">LaTeX 模板（{{ latexTemplates.length }} 个期刊/会议）</label>
                <select v-model="latexTemplate" class="w-full px-3 py-2 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-sm dark:text-gray-100">
                  <optgroup label="📰 国际期刊">
                    <option v-for="t in latexTemplates.filter(t=>t.category==='journal')" :key="t.id" :value="t.id">{{ t.name }} — {{ t.desc }}</option>
                  </optgroup>
                  <optgroup label="🎤 国际会议">
                    <option v-for="t in latexTemplates.filter(t=>t.category==='conference')" :key="t.id" :value="t.id">{{ t.name }} — {{ t.desc }}</option>
                  </optgroup>
                  <optgroup label="🇨🇳 国内期刊（国标）">
                    <option v-for="t in latexTemplates.filter(t=>t.category==='chinese')" :key="t.id" :value="t.id">{{ t.name }} — {{ t.desc }}</option>
                  </optgroup>
                </select>
              </div>
            </div>
          </div>
          <div class="px-4 py-3 border-t border-gray-200 dark:border-gray-700 flex justify-end gap-2">
            <button @click="showExportPanel = false" class="px-4 py-2 text-xs text-gray-600 hover:text-gray-800">取消</button>
            <button @click="doExport" class="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-xs font-medium">导出</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- P0-3: 逐段修改弹窗 -->
    <Teleport to="body">
      <div v-if="showRewriteModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50" @click.self="showRewriteModal=false">
        <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
          <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <h3 class="text-lg font-semibold text-gray-800 dark:text-gray-100">修改：{{ rewriteTarget.title }}</h3>
            <button @click="showRewriteModal=false" class="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded text-gray-400">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="p-6 space-y-4">
            <!-- 模式选择 -->
            <div>
              <label class="block text-xs text-gray-500 mb-2">修改模式</label>
              <div class="grid grid-cols-4 gap-2">
                <button v-for="m in REWRITE_MODES" :key="m.key" @click="rewriteMode = m.key"
                  :class="['px-3 py-2 rounded-xl text-sm font-medium text-center transition-all',
                    rewriteMode === m.key 
                      ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 ring-2 ring-green-500/30' 
                      : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600']">
                  <span class="block text-lg">{{ m.icon }}</span>
                  <span class="text-xs">{{ m.label }}</span>
                </button>
              </div>
            </div>
            <!-- 额外指令 -->
            <div>
              <label class="block text-xs text-gray-500 mb-1">额外要求（可选）</label>
              <input v-model="rewriteInstruction" placeholder="如：增加数据支撑 / 更口语化 / 加入案例..."
                class="w-full px-3 py-2 bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-xl text-sm focus:outline-none focus:border-green-500 dark:text-gray-100"
                @keydown.enter="doRewriteSection" />
            </div>
          </div>
          <div class="px-6 py-4 bg-gray-50 dark:bg-gray-750 border-t border-gray-200 dark:border-gray-700 flex justify-end gap-2">
            <button @click="showRewriteModal=false" class="px-4 py-2 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-xl">取消</button>
            <button @click="doRewriteSection" :disabled="rewriteLoading"
              class="px-5 py-2 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white rounded-xl text-sm font-medium flex items-center gap-2">
              <span v-if="rewriteLoading" class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
              {{ rewriteLoading ? '修改中...' : '开始修改' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, reactive } from 'vue'
import { useRouter } from 'vue-router'
import DOMPurify from 'dompurify'
import ProjectList from './ProjectList.vue'

const router = useRouter()

// ═══════════════════════════════════════════════════════════════════
// 项目状态
// ═══════════════════════════════════════════════════════════════════
const currentModel = ref('')
const currentProvider = ref('')
const events = ref([])  // 事件日志
const showEventLog = ref(false)

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

const stages = ref([])  // 动态从 /api/scholar/agents 加载
const activeStage = ref('')

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

// 简单的 Markdown 渲染
const renderedContent = computed(() => {
  let html = currentContent.value
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/^### (.+)$/gm, '<h3 class="text-base font-semibold text-gray-800 dark:text-gray-100 mt-4 mb-2">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 class="text-lg font-bold text-gray-800 dark:text-gray-100 mt-5 mb-3">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="text-xl font-bold text-gray-800 dark:text-gray-100 mt-6 mb-4">$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="text-gray-900 dark:text-gray-100">$1</strong>')
    .replace(/\*(.+?)\*/g, '<em class="text-gray-700 dark:text-gray-300">$1</em>')
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="bg-gray-950 p-3 rounded-lg overflow-x-auto text-xs my-3"><code class="text-gray-300">$2</code></pre>')
    .replace(/`([^`]+)`/g, '<code class="bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 rounded text-xs">$1</code>')
    // 表格
    .replace(/\|(.+)\|/g, (match) => {
      if (match.includes('---')) return ''
      const cells = match.split('|').filter(c => c.trim())
      return '<tr>' + cells.map(c => `<td class="border px-2 py-1 text-xs">${c.trim()}</td>`).join('') + '</tr>'
    })
    // 列表
    .replace(/^- (.+)$/gm, '<li class="ml-4 list-disc text-gray-700 dark:text-gray-300 text-sm">$1</li>')
    .replace(/^\d+\. (.+)$/gm, '<li class="ml-4 list-decimal text-gray-700 dark:text-gray-300 text-sm">$1</li>')
    // 引用标记 — 根据核查结果着色
    .replace(/\[(\d+)\]/g, (match, num) => {
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
    // 段落
    .replace(/\n\n/g, '</p><p class="text-gray-700 dark:text-gray-300 text-sm leading-relaxed my-2">')
  
  return DOMPurify.sanitize('<p class="text-gray-700 dark:text-gray-300 text-sm leading-relaxed my-2">' + html + '</p>')
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
    
    // 识别参考文献节
    if (/^#{1,3}\s*参[考考]文[献献]|^#{1,3}\s*References/i.test(line)) {
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
      // 匹配 [n] 作者. 标题. 期刊, 年份 格式
      const refMatch = line.match(/^\[(\d+)\]\s*(.+?)(?:\.|$)/)
      if (refMatch) {
        const refText = refMatch[2]
        // 尝试提取作者、标题、年份
        const parts = refText.split(/\.\s*/)
        result.references.push({
          authors: parts[0] ? parts[0].split(',').map(s => s.trim()) : [],
          title: parts[1] || refText,
          year: refText.match(/(\d{4})/)?.[1] || '',
          venue: parts[2] || ''
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
const showLiteraturePanel = ref(true)
const literatureSearch = ref('')
const selectedLiterature = ref(null)
const expandedPaper = ref(null)

// 研究深度选择器
const researchDepth = ref(2)  // 1=快速, 2=标准(默认), 3=深度
const depthOptions = [
  { value: 1, label: '快速', icon: '⚡', desc: '单轮检索，适合快速了解' },
  { value: 2, label: '标准', icon: '🔍', desc: '多视角检索+聚合' },
  { value: 3, label: '深度', icon: '🕸️', desc: '3轮递归+缺口分析+STORM' },
]

const literature = ref([])

const filteredLiterature = computed(() => {
  if (!literatureSearch.value) return literature.value
  const q = literatureSearch.value.toLowerCase()
  return literature.value.filter(p => 
    p.title.toLowerCase().includes(q) || 
    p.authors.some(a => a.toLowerCase().includes(q))
  )
})

const literatureCount = computed(() => literature.value.length)

const selectLiterature = (paper) => {
  selectedLiterature.value = paper
}

const insertCitation = (paper) => {
  if (!editorRef.value) return
  const ta = editorRef.value
  const start = ta.selectionStart
  if (paper) {
    const citeKey = paper.citeKey || `ref${paper.id || Date.now()}`
    const cite = `[@${citeKey}]`
    currentContent.value = currentContent.value.slice(0, start) + cite + currentContent.value.slice(start)
    ta.setSelectionRange(start + cite.length, start + cite.length)
  } else {
    const cite = '[@?]'
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
// 引用核查状态
// ═══════════════════════════════════════════════════════════════════
const showCitationPanel = ref(false)
const citationResults = ref([])      // [{ref, score, reason}]
const citationErrors = computed(() => citationResults.value.filter(r => r.score < 3).length)
const citationWarnings = computed(() => citationResults.value.filter(r => r.score >= 3 && r.score < 7).length)

// ═══════════════════════════════════════════════════════════════════
// 共识度分析
// ═══════════════════════════════════════════════════════════════════
const showConsensusPanel = ref(false)
const consensusLoading = ref(false)
const consensusResults = ref([])     // [{claim, support, oppose, neutral, total, consensus_pct, confidence, per_paper, _expanded}]

// ── P0-2: 查重 + AIGC 检测 ──
const showPlagPanel = ref(false)
const plagLoading = ref(false)
const plagResult = ref(null)

const runPlagcheck = async () => {
  showPlagPanel.value = true
  if (!project.value?.id) return
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

const searchLiterature = async () => {
  const q = literatureSearch.value.trim() || project.value.title || ''
  if (!q) {
    alert('请输入检索关键词')
    return
  }
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
      events.value.push({ type: 'done', message: `找到 ${newPapers.length} 篇新文献`, time: Date.now() })
    }
  } catch (e) {
    events.value.push({ type: 'error', message: `检索失败: ${e.message}`, time: Date.now() })
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
const showAIPanel = ref(false)
const leftCollapsed = ref(false)   // 左栏大纲折叠
const rightCollapsed = ref(false)  // 右栏文献/AI默认展开
const toggleLeftBar = () => { leftCollapsed.value = !leftCollapsed.value }
const toggleRightBar = () => { 
  rightCollapsed.value = !rightCollapsed.value
  if (!rightCollapsed.value && !showLiteraturePanel.value && !showAIPanel.value) {
    showLiteraturePanel.value = true
  }
}
const aiInput = ref('')
const aiStreaming = ref(false)
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
            // 如果带 section_key，写入对应章节编辑器
            if (evt.section_key) {
              const existing = sectionContents.value[evt.section_key] || ''
              sectionContents.value[evt.section_key] = existing + evt.text
              // 更新大纲 wordCount
              const sec = outline.value.find(s => s.id === evt.section_key)
              if (sec) {
                sec.wordCount = (sectionContents.value[evt.section_key] || '').length
                sec.status = 'completed'
              }
            }
          } else if (evt.type === 'thinking' && evt.message) {
            // thinking 事件不进正文, 只在事件日志显示
            events.value.push({ type: 'thinking', message: evt.message, time: Date.now() })
          } else if (evt.type === 'citation_verify' && evt.results) {
            // 引用核查结果 → 更新面板
            citationResults.value = evt.results || []
            if ((evt.errors || 0) + (evt.warnings || 0) > 0) {
              showCitationPanel.value = true
            }
            events.value.push({ type: 'done', message: `引用核查: ${evt.errors || 0}错误 ${evt.warnings || 0}警告`, time: Date.now() })
          } else if (evt.type === 'done') {
            // done 事件可能携带 paper_count → 触发文献刷新
            if (evt.papers != null) {
              refreshLiterature().catch(()=>{})
            }
            // 全链路写作完成后，自动拉共识度（异步，不阻塞）
            if (project.value?.id) {
              runConsensusAnalysis().catch(()=>{})
            }
          } else if (evt.type === 'error') {
            assistantMsg.content += `\n\n⚠️ ${evt.message}`
          }
        } catch {}
      }
    }
  } catch (e) {
    assistantMsg.content = `❌ 请求失败: ${e.message}\n\n请检查后端服务 (端口 9119) 是否运行, 以及 API Key 是否配置。`
  } finally {
    assistantMsg.streaming = false
    aiStreaming.value = false
  }
}

const runAICommand = (cmd) => {
  aiInput.value = cmd.prompt
  showAICommands.value = false
  sendToAI()
}

const runStormPipeline = async () => {
  if (aiStreaming.value) return

  // 用当前编辑器内容或论文主题作为 STORM 输入
  const topic = sectionContents.value[activeSection.value] || project.value.title || '深度学习在医学影像中的应用'
  if (!topic.trim()) {
    alert('请先输入论文主题或写一些内容')
    return
  }

  aiMessages.value.push({ role: 'user', content: `⚡ STORM 全链路：${topic.slice(0, 80)}...`, time: Date.now() })
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
            // 如果带 section_key，写入对应章节编辑器
            if (evt.section_key) {
              const existing = sectionContents.value[evt.section_key] || ''
              sectionContents.value[evt.section_key] = existing + evt.text
              const sec = outline.value.find(s => s.id === evt.section_key)
              if (sec) {
                sec.wordCount = (sectionContents.value[evt.section_key] || '').length
                sec.status = 'completed'
              }
            }
          } else if (evt.type === 'thinking' && evt.message) {
            events.value.push({ type: 'thinking', message: evt.message, time: Date.now() })
          } else if (evt.type === 'searching' && evt.message) {
            events.value.push({ type: 'searching', message: evt.message, time: Date.now() })
          } else if (evt.type === 'writing' && evt.message) {
            events.value.push({ type: 'writing', message: evt.message, time: Date.now() })
          } else if (evt.type === 'citation_verify' && evt.results) {
            citationResults.value = evt.results || []
            if ((evt.errors || 0) + (evt.warnings || 0) > 0) {
              showCitationPanel.value = true
            }
            events.value.push({ type: 'done', message: `引用核查: ${evt.errors || 0}错误 ${evt.warnings || 0}警告`, time: Date.now() })
          } else if (evt.type === 'error') {
            assistantMsg.content += `\n\n⚠️ ${evt.message}`
          } else if (evt.type === 'done') {
            assistantMsg.content += `\n\n---\n✅ ${evt.message || 'STORM 全链路完成'}`
            // done 事件可能携带 paper_count → 触发文献刷新
            if (evt.papers != null) {
              refreshLiterature().catch(()=>{})
            }
            // STORM 全链路完成后，自动拉共识度
            if (project.value?.id) {
              runConsensusAnalysis().catch(()=>{})
            }
          }
        } catch {}
      }
    }
  } catch (e) {
    assistantMsg.content = `❌ STORM 请求失败: ${e.message}\n\n请检查后端服务是否运行, 以及 API Key 是否配置。`
  } finally {
    assistantMsg.streaming = false
    aiStreaming.value = false
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
const exportFormat = ref('pdf')
const exportFilename = computed(() => `${project.value.title || '未命名论文'}.${exportFormat.value}`)
const latexTemplate = ref('ieee')  // Phase 1.3 — 15 模板默认 IEEE

const latexTemplates = [
  // 国际期刊
  { id: 'ieee', name: 'IEEEtran', desc: 'IEEE 期刊/会议', category: 'journal' },
  { id: 'springer-svjour', name: 'Springer SVJour', desc: 'Springer 期刊模板', category: 'journal' },
  { id: 'elsevier-elsarticle', name: 'Elsevier Elsarticle', desc: 'Elsevier 期刊', category: 'journal' },
  { id: 'nature', name: 'Nature', desc: 'Nature 期刊', category: 'journal' },
  { id: 'science', name: 'Science', desc: 'Science 期刊', category: 'journal' },
  { id: 'apa', name: 'APA 6th', desc: '心理学/社会科学', category: 'journal' },
  // 国际会议
  { id: 'acm-sigconf', name: 'ACM SigConf', desc: 'ACM 会议标准', category: 'conference' },
  { id: 'mlr', name: 'MLR/JMLR', desc: '机器学习会议/期刊', category: 'conference' },
  { id: 'neurips', name: 'NeurIPS', desc: 'NeurIPS 会议', category: 'conference' },
  { id: 'icml', name: 'ICML', desc: 'ICML 会议', category: 'conference' },
  { id: 'cvpr', name: 'CVPR/ICCV', desc: '计算机视觉会议', category: 'conference' },
  // 国内期刊（国标）
  { id: 'gbt7714', name: 'GB/T 7714', desc: '中国学术期刊通用', category: 'chinese' },
  { id: 'acta-physica', name: '物理学报', desc: '中国物理学会', category: 'chinese' },
  { id: 'jcs', name: '计算机学报', desc: '中国计算机学会', category: 'chinese' },
  { id: 'jsi', name: '软件学报', desc: '中国计算机学会', category: 'chinese' },
]

const exportFormats = [
  { id: 'pdf', name: 'PDF 文档', icon: '📄', desc: '适合提交和打印' },
  { id: 'latex', name: 'LaTeX', icon: '📐', desc: '学术标准格式' },
  { id: 'word', name: 'Word', icon: '📝', desc: '便于后续编辑' },
  { id: 'markdown', name: 'Markdown', icon: '⬇️', desc: '保留源格式' },
  { id: 'bibtex', name: 'BibTeX', icon: '📚', desc: '仅导出参考文献' }
]

const copyRichText = async () => {
  // 渲染 Markdown → HTML，写入 clipboard (Phase 1.4)
  const md = buildFullPaper()
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

const doExport = async () => {
  const fmt = exportFormat.value
  showExportPanel.value = false

  try {
    const pid = project.value?.id || 0
    const params = new URLSearchParams({
      format: fmt,
      title: project.value?.title || '未命名论文',
    })
    if (fmt === 'latex') params.set('template', latexTemplate.value)
    const r = await fetch(`/api/scholar/export?${params}`)
    if (!r.ok) {
      const err = await r.text()
      alert('导出失败: ' + err)
      return
    }

    // 二进制格式 (PDF/Word) — 从 Content-Disposition 拿文件名
    if (fmt === 'pdf' || fmt === 'word' || fmt === 'docx') {
      const blob = await r.blob()
      // 尝试从 header 读 filename
      let filename = `${project.value?.title || '论文'}.${fmt === 'word' ? 'docx' : fmt}`
      const dispo = r.headers.get('Content-Disposition') || ''
      const m = dispo.match(/filename\*?=["']?([^";]+)/i)
      if (m) filename = decodeURIComponent(m[1])
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
      return
    }

    // 文本格式 (Markdown/LaTeX/BibTeX)
    const data = await r.json()
    const extMap = { bibtex: 'bib', latex: 'tex', markdown: 'md' }
    const ext = extMap[fmt] || 'md'
    const filename = `${project.value?.title || '论文'}.${ext}`
    const blob = new Blob([data.content], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    console.error('Export error', e)
    alert('导出失败: ' + e.message)
  }
}

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

// ── P0-3: 逐段修改 ──
const showRewriteModal = ref(false)
const rewriteTarget = ref({ key: '', title: '' })
const rewriteMode = ref('polish')
const rewriteInstruction = ref('')
const rewriteLoading = ref(false)

const REWRITE_MODES = [
  { key: 'polish', icon: '✨', label: '润色' },
  { key: 'expand', icon: '📖', label: '扩写' },
  { key: 'shorten', icon: '✂️', label: '精简' },
  { key: 'restructure', icon: '🔀', label: '重组' },
  { key: 'add_data', icon: '📊', label: '加数据' },
  { key: 'academic', icon: '🎓', label: '学术化' },
  { key: 'plain', icon: '💬', label: '通俗化' },
]

const rewriteSection = (sectionId) => {
  const section = outline.value.find(s => s.id === sectionId)
  if (!section) return
  const content = sectionContents.value[sectionId]
  if (!content || content.trim().length < 50) {
    alert('该章节内容太短，无法修改')
    return
  }
  rewriteTarget.value = { key: sectionId, title: section.title || '未命名章节' }
  rewriteMode.value = 'polish'
  rewriteInstruction.value = ''
  showRewriteModal.value = true
}

const doRewriteSection = async () => {
  if (!rewriteTarget.value.key || !project.value?.id || rewriteLoading.value) return
  rewriteLoading.value = true
  const sectionKey = rewriteTarget.value.key
  try {
    const r = await fetch(`/api/scholar/projects/${project.value.id}/rewrite-section`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        section_key: sectionKey,
        mode: rewriteMode.value,
        instruction: rewriteInstruction.value,
      }),
    })
    if (!r.ok) throw new Error(await r.text())
    // 解析 SSE
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const evt = JSON.parse(line.slice(6))
            if (evt.type === 'rewrite_done') {
              sectionContents.value[sectionKey] = evt.text
              if (activeSection.value === sectionKey) {
                currentContent.value = evt.text
              }
              const section = outline.value.find(s => s.id === sectionKey)
              if (section) section.wordCount = evt.text.length
              showRewriteModal.value = false
            } else if (evt.type === 'thinking') {
              // 忽略中间状态
            } else if (evt.type === 'error') {
              throw new Error(evt.message)
            }
          } catch (e) { if (e.message && !e.message.includes('JSON')) throw e }
        }
      }
    }
  } catch (e) {
    alert('修改失败: ' + e.message)
  } finally {
    rewriteLoading.value = false
  }
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
const scheduleAutosave = () => {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(saveCurrentSection, 800)
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

// 点击外部关闭项目切换器
const handleClickOutside = (e) => {
  if (projectSwitcherRef.value && !projectSwitcherRef.value.contains(e.target)) {
    showProjectSwitcher.value = false
  }
  // 点击外部关闭 Agent 模型下拉
  if (openAgentDropdown.value && !e.target.closest('.agent-dropdown-anchor')) {
    openAgentDropdown.value = null
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
  const m = { '本科论文': '🎓', '硕士论文': '📚', '博士论文': '🔬', '期刊论文': '📰' }
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

// 论文类型
const paperTypes = [
  { id: 'undergrad', name: '本科论文', icon: '🎓', desc: '8000-15000字', defaultWords: 10000 },
  { id: 'master', name: '硕士论文', icon: '📚', desc: '3-5万字', defaultWords: 30000 },
  { id: 'phd', name: '博士论文', icon: '🔬', desc: '8-15万字', defaultWords: 80000 },
  { id: 'journal', name: '期刊论文', icon: '📰', desc: '5000-10000字', defaultWords: 8000 }
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
