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

      <!-- 中间：写作阶段 + Agent 模型选择器 (Vermes 风格紧凑下拉) -->
      <div class="flex items-center gap-1">
        <div v-for="(stage, idx) in stages" :key="stage.id" class="relative agent-dropdown-anchor">
          <button
            @click="activeStage = stage.id"
            :class="[
              'px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all flex items-center gap-1.5',
              activeStage === stage.id
                ? 'bg-green-600 text-white shadow-sm'
                : stage.completed
                  ? 'text-green-600 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-900/20'
                  : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'
            ]"
          >
            <span v-if="stage.completed" class="text-xs">✓</span>
            <span v-else class="text-[10px] w-3.5 h-3.5 rounded-full bg-current/20 flex items-center justify-center">{{ idx + 1 }}</span>
            <span class="whitespace-nowrap">{{ stage.name }}</span>
          </button>
          <button
            @click.stop="toggleAgentDropdown(stage.id, $event)"
            :class="[
              'text-[9px] px-1.5 py-0.5 rounded cursor-pointer flex items-center gap-0.5 transition-colors',
              activeStage === stage.id
                ? 'bg-green-500 text-white'
                : agentProviders[stage.id] ? 'bg-gray-200 dark:bg-gray-600 text-gray-600 dark:text-gray-200 hover:bg-gray-300' : 'bg-gray-100 dark:bg-gray-700 text-gray-400 hover:bg-gray-200'
            ]"
            :title="getAgentProviderTitle(stage.id)"
          >
            <span class="whitespace-nowrap">{{ agentProviders[stage.id] ? agentProviderLabel(stage.id) : '选模型' }}</span>
            <svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7"/></svg>
          </button>

          <!-- 紧凑下拉 (Vermes 主 Agent 风格: 只列已配置厂商的模型, 扁平 list) -->
          <div v-if="openAgentDropdown === stage.id"
            class="absolute top-full right-0 mt-1 w-[220px] bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl z-50 max-h-[320px] flex flex-col overflow-hidden">
            <div class="px-2 py-1.5 border-b border-gray-200 dark:border-gray-700">
              <input
                v-model="providerSearch"
                @click.stop
                placeholder="🔍 搜索模型..."
                class="w-full px-2 py-1 text-[11px] bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded text-gray-700 dark:text-gray-200 focus:outline-none focus:border-green-500"
              />
            </div>
            <div class="flex-1 overflow-y-auto py-0.5">
              <div v-if="!availableProviders.length" class="text-[11px] text-gray-400 py-4 text-center">加载中...</div>
              <div v-else-if="!configuredProviders.length" class="text-[11px] text-gray-400 py-4 px-3 text-center">
                暂未配置任何模型 Key<br/>
                <span class="text-[10px] text-gray-500">请在 Vermes <b>设置</b> 中添加 API Key</span>
              </div>
              <template v-for="p in filteredProviders" :key="p.key">
                <div class="px-2 pt-1.5 pb-0.5 text-[9px] uppercase tracking-wider text-gray-400 font-semibold sticky top-0 bg-white dark:bg-gray-800 z-10">
                  {{ p.key }}
                  <span v-if="p.recommended" class="text-green-500 ml-0.5">★</span>
                </div>
                <button
                  v-for="m in getModelsForProvider(p.key)"
                  :key="p.key + '-' + m"
                  @click="setAgentProvider(stage.id, p.key, m); closeAgentDropdown()"
                  :class="[
                    'w-full text-left px-2.5 py-1 text-[11px] transition-colors flex items-center justify-between',
                    agentProviders[stage.id]?.provider === p.key && agentProviders[stage.id]?.model === m
                      ? 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300 font-medium'
                      : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'
                  ]"
                >
                  <span class="truncate">{{ m }}</span>
                  <span v-if="agentProviders[stage.id]?.provider === p.key && agentProviders[stage.id]?.model === m" class="text-green-600 shrink-0 ml-1">✓</span>
                </button>
              </template>
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧：协作与导出 -->
      <div class="flex items-center gap-2 flex-1 justify-end">
        <button @click="showLiteraturePanel = !showLiteraturePanel" 
          :class="['px-3 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5', showLiteraturePanel ? 'bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400' : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700']">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/></svg>
          文献库
          <span v-if="literatureCount > 0" class="bg-blue-600 text-white text-[10px] px-1.5 py-0 rounded-full">{{ literatureCount }}</span>
        </button>
        <button @click="showAIPanel = !showAIPanel"
          :class="['px-3 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5', showAIPanel ? 'bg-purple-50 text-purple-600 dark:bg-purple-900/20 dark:text-purple-400' : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700']">
          <span class="text-sm">🤖</span>
          AI 助手
        </button>
        <div class="h-5 w-px bg-gray-200 dark:bg-gray-600 mx-1"></div>
        <button @click="showExportPanel = true" class="px-3 py-1.5 bg-green-600 hover:bg-green-700 text-white rounded-lg text-xs font-medium flex items-center gap-1.5 transition-colors">
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
           │ 左栏：大纲导航 (Scrivener 风格)
           └─────────────────────────────────────────────────────────────┘ -->
      <aside class="w-64 border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 flex flex-col shrink-0">
        <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">论文结构</span>
          <button @click="addSection" class="p-1 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-400 hover:text-gray-600">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
          </button>
        </div>
        <div class="flex-1 overflow-y-auto py-2">
          <div class="space-y-0.5">
            <div v-for="(section, idx) in outline" :key="section.id"
              @click="activeSection = section.id"
              :class="[
                'px-3 py-2 cursor-pointer text-sm transition-colors border-l-2',
                activeSection === section.id 
                  ? 'bg-white dark:bg-gray-700 border-green-500 text-gray-900 dark:text-gray-100' 
                  : 'border-transparent text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700/50'
              ]"
            >
              <div class="flex items-center gap-2">
                <span class="text-xs text-gray-400 font-mono">{{ section.number }}</span>
                <span class="truncate flex-1">{{ section.title || '未命名章节' }}</span>
                <span v-if="section.wordCount" class="text-[10px] text-gray-400">{{ section.wordCount }}字</span>
              </div>
              <div v-if="section.status === 'writing'" class="mt-1 flex items-center gap-1">
                <span class="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></span>
                <span class="text-[10px] text-green-600">AI 写作中...</span>
              </div>
              <div v-else-if="section.status === 'completed'" class="mt-1 flex items-center gap-1">
                <span class="text-[10px] text-gray-400">✓ 已完成</span>
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
      </aside>

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
            <textarea
              ref="editorRef"
              v-model="currentContent"
              @input="onEditorInput"
              class="flex-1 w-full p-4 resize-none outline-none font-mono text-sm leading-relaxed text-gray-800 dark:text-gray-200 bg-white dark:bg-gray-900"
              placeholder="开始写作..."
              spellcheck="false"
            ></textarea>
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
           │ 右栏：文献库 / AI 助手 (可切换)
           └─────────────────────────────────────────────────────────────┘ -->
      <aside v-if="showLiteraturePanel || showAIPanel" class="w-80 border-l border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 flex flex-col shrink-0">
        
        <!-- 文献库面板 -->
        <template v-if="showLiteraturePanel">
          <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">文献库</span>
            <button @click="searchLiterature" class="p-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs flex items-center gap-1">
              <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
              检索
            </button>
          </div>
          <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700">
            <input v-model="literatureSearch" placeholder="搜索文献..." class="w-full px-3 py-1.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-xs focus:outline-none focus:border-blue-500 dark:text-gray-100"/>
          </div>
          <div class="flex-1 overflow-y-auto py-2">
            <div v-for="paper in filteredLiterature" :key="paper.id" 
              @click="selectLiterature(paper)"
              :class="['px-3 py-2 cursor-pointer border-b border-gray-100 dark:border-gray-700/50 hover:bg-white dark:hover:bg-gray-700/50 transition-colors', selectedLiterature?.id === paper.id ? 'bg-blue-50 dark:bg-blue-900/10 border-l-2 border-l-blue-500' : '']">
              <div class="text-xs font-medium text-gray-800 dark:text-gray-200 line-clamp-2">{{ paper.title }}</div>
              <div class="text-[10px] text-gray-500 mt-1">{{ paper.authors?.[0] }} 等 · {{ paper.year }}</div>
              <div class="flex items-center gap-2 mt-1.5">
                <span class="text-[10px] px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-gray-500">{{ paper.venue }}</span>
                <button @click.stop="insertCitation(paper)" class="text-[10px] text-blue-600 hover:text-blue-700">引用</button>
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
      </aside>
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
                <label class="text-xs text-gray-500 mb-1 block">模板</label>
                <select class="w-full px-3 py-2 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-sm dark:text-gray-100">
                  <option>IEEEtran</option>
                  <option>ACM</option>
                  <option>Springer LNCS</option>
                  <option>国标 GB/T 7714</option>
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
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import ProjectList from './ProjectList.vue'

const router = useRouter()

// ═══════════════════════════════════════════════════════════════════
// 项目状态
// ═══════════════════════════════════════════════════════════════════
const currentModel = ref('')
const currentProvider = ref('')

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
    // 引用标记
    .replace(/\[(\d+)\]/g, '<sup class="text-blue-600 cursor-pointer hover:underline">[$1]</sup>')
    // 段落
    .replace(/\n\n/g, '</p><p class="text-gray-700 dark:text-gray-300 text-sm leading-relaxed my-2">')
  
  return '<p class="text-gray-700 dark:text-gray-300 text-sm leading-relaxed my-2">' + html + '</p>'
})

const editor = {
  format: (type) => {
    // 格式化操作
    console.log('Format:', type)
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

// ═══════════════════════════════════════════════════════════════════
// 文献库
// ═══════════════════════════════════════════════════════════════════
const showLiteraturePanel = ref(true)
const literatureSearch = ref('')
const selectedLiterature = ref(null)

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
  const cite = paper ? `[${paper.id}]` : '[?]'
  // 在光标位置插入
  console.log('Insert citation:', cite)
}

const searchLiterature = () => {
  // 打开文献检索对话框
  console.log('Search literature...')
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
  { id: 'outline', name: '生成大纲', icon: '📋' },
  { id: 'abstract', name: '写摘要', icon: '📄' },
  { id: 'intro', name: '写引言', icon: '🚀' },
  { id: 'related', name: '文献综述', icon: '📚' },
  { id: 'method', name: '方法描述', icon: '🔬' },
  { id: 'check', name: '语法检查', icon: '✓' }
]

const sendToAI = async () => {
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
        agent: activeStage.value || 'writing',
        section: activeSection.value || 'intro',
        pipeline: false,
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
          if (evt.type === 'content' && evt.text) {
            assistantMsg.content += evt.text
          } else if (evt.type === 'thinking' && evt.message) {
            // thinking 事件不进正文, 只在事件日志显示
            events.value.push({ type: 'thinking', message: evt.message, time: Date.now() })
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
  aiInput.value = cmd.prompt + '：'
  showAICommands.value = false
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
        agent: 'storm',
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
          if (evt.type === 'content' && evt.text) {
            assistantMsg.content += evt.text
          } else if (evt.type === 'thinking' && evt.message) {
            events.value.push({ type: 'thinking', message: evt.message, time: Date.now() })
          } else if (evt.type === 'searching' && evt.message) {
            events.value.push({ type: 'searching', message: evt.message, time: Date.now() })
          } else if (evt.type === 'writing' && evt.message) {
            events.value.push({ type: 'writing', message: evt.message, time: Date.now() })
          } else if (evt.type === 'error') {
            assistantMsg.content += `\n\n⚠️ ${evt.message}`
          } else if (evt.type === 'done') {
            assistantMsg.content += `\n\n---\n✅ ${evt.message || 'STORM 全链路完成'}`
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
  // 预设动作 → 填入输入框 + 自动发送
  aiInput.value = action.prompt + '：'
  sendToAI()
}

// ═══════════════════════════════════════════════════════════════════
// 导出
// ═══════════════════════════════════════════════════════════════════
const showExportPanel = ref(false)
const exportFormat = ref('pdf')
const exportFilename = computed(() => `${project.value.title || '未命名论文'}.${exportFormat.value}`)

const exportFormats = [
  { id: 'pdf', name: 'PDF 文档', icon: '📄', desc: '适合提交和打印' },
  { id: 'latex', name: 'LaTeX', icon: '📐', desc: '学术标准格式' },
  { id: 'word', name: 'Word', icon: '📝', desc: '便于后续编辑' },
  { id: 'markdown', name: 'Markdown', icon: '⬇️', desc: '保留源格式' },
  { id: 'bibtex', name: 'BibTeX', icon: '📚', desc: '仅导出参考文献' }
]

const doExport = async () => {
  const fmt = exportFormat.value
  showExportPanel.value = false

  // PDF/LaTeX/Word 尚未实现，提示用户
  if (fmt === 'pdf' || fmt === 'latex' || fmt === 'word') {
    alert(`📦 ${fmt.toUpperCase()} 导出功能开发中，即将支持。当前请使用 Markdown 或 BibTeX 格式。`)
    return
  }

  try {
    const pid = project.value?.id || 0
    const params = new URLSearchParams({
      format: fmt,
      title: project.value?.title || '未命名论文',
    })
    // project_id 可选：持久化项目传 id，内存项目用默认 ctx
    const r = await fetch(`/api/scholar/export?${params}`)
    if (!r.ok) {
      const err = await r.text()
      alert('导出失败: ' + err)
      return
    }
    const data = await r.json()
    const ext = fmt === 'bibtex' ? 'bib' : 'md'
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
    number: '',
    title: '新章节',
    wordCount: 0,
    status: 'pending'
  })
  activeSection.value = newId
}

// ═════════════════════════════════════════════════════════════════
// 生命周期 + 项目列表加载
// ═════════════════════════════════════════════════════════════════
onMounted(async () => {
  // 动态加载 Agent 列表（不再硬编码）
  await loadStages()
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
