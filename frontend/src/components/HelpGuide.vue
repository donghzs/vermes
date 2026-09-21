<script setup>
import { ref } from 'vue'

const emit = defineEmits(['close'])
const activeSection = ref('start')

const sections = [
  { id: 'start', icon: '🚀', title: '快速开始' },
  { id: 'modules', icon: '🗺️', title: '功能地图' },
  { id: 'agents', icon: '🤖', title: 'Agent 与技能' },
  { id: 'models', icon: '🔑', title: '模型与额度' },
  { id: 'shortcuts', icon: '⌨️', title: '快捷操作' },
  { id: 'faq', icon: '❓', title: '常见问题' },
]

const modules = [
  { icon: '💬', title: '智能对话', desc: '多轮上下文记忆、Markdown 渲染、代码高亮、图片/文件识别、工具调用（搜索/执行代码/读写文件/浏览网页）' },
  { icon: '⛩️', title: '神魔堂', desc: '多 Agent 群聊（诸神会晤）+ 请神/造神（神魔架）+ 蜂群看板，组织编排你的 AI 团队' },
  { icon: '🤖', title: 'Agent 管理', desc: '自造神、封神榜、已装技能/工具/MCP/专家/记忆/知识库，全宽管理界面' },
  { icon: '🧱', title: '积木市场', desc: '技能/工具/模块/软件/MCP 的发现与一键安装，含热门榜' },
  { icon: '📝', title: '论文写作', desc: 'ScholarForge：文献搜索 → AI 全链路写作 → 查重 + AIGC 检测 → 评分 → Word 导出' },
  { icon: '🎨', title: '创作工作室', desc: '文档/表格/PPT/简历/思维导图等场景化创作' },
  { icon: '🏭', title: '3D 建模', desc: '文本描述生成 3D 模型，可视化工作室' },
  { icon: '🔀', title: '工作流编排', desc: '可视化 DAG 编辑器 + 触发器配置，把多步骤任务串成自动化流水线' },
  { icon: '🌱', title: '成长系统', desc: '成长轨迹 / 能力自检 / 我眼里的你，Agent 越用越懂你' },
  { icon: '📊', title: 'Benchmark', desc: '工具接线大盘 + pipeline 回归干跑，可视化观测 Agent 能力' },
  { icon: '📱', title: '移动接入', desc: 'Telegram / 飞书等渠道接入，桌面任务移动端接续' },
  { icon: '🔄', title: '自动更新', desc: '有新版本自动提示，一键升级' },
]

const agents = [
  { icon: '⛩️', title: '神魔架 · 请神登堂', desc: '把任意可被驱动的 Agent（CLI/MCP/HTTP）接入 Vermes 统一调度，一条通路就能登堂' },
  { icon: '✨', title: '自造神', desc: '自定义 Agent 人设、模型、技能，造出专属的领域专家' },
  { icon: '🔥', title: '封神榜', desc: '预置 Agent 食谱库 + 本机已装 Agent 自动发现，一键登堂' },
  { icon: '🧩', title: '技能（Skill）', desc: '已装技能管理：启停、查看、按需加载正文（skill_view），技能是 Agent 的"招式"' },
  { icon: '🛠️', title: '工具（Toolset）', desc: '工具集总览与启停，控制 Agent 可调用的能力范围' },
  { icon: '🔌', title: 'MCP Server', desc: '已装 MCP server 管理 + 安全校验 + 调用监控，扩展外部工具' },
  { icon: '🧠', title: '记忆', desc: '跨会话、跨渠道的共享记忆底座，Agent 记住你的偏好与上下文' },
  { icon: '📚', title: '知识库', desc: '本地知识库 RAG，让 Agent 基于你的资料回答' },
]

const modelTips = [
  { icon: '🆓', title: '免费体验（推荐新手）', desc: '微信扫码登录即用，默认走 Agnes AI 免费模型（文本/图片/视频全模态免费），无需自备 API Key，无需配置' },
  { icon: '🔑', title: '配置自己的 API Key', desc: '设置页支持 40+ 家模型厂商（DeepSeek / Agnes / 小米 MiMo / OpenAI / Anthropic / Gemini / 通义千问 / Ollama 本地 等），填入 Key → 同步模型 → 设为当前' },
  { icon: '🏠', title: '本地模型', desc: 'Ollama 本地运行，数据不离开电脑，完全免费离线可用' },
  { icon: '🎯', title: '多模型切换', desc: '对话中可随时切换模型（Cmd/Ctrl+/），不同任务用不同模型' },
]

const faqs = [
  { q: '白屏 / 打不开？', a: 'Windows: 以管理员身份运行 | macOS: 系统设置 → 隐私与安全性 → 仍要打开' },
  { q: '对话没有回复？', a: '检查网络 → 检查 API Key/额度 → 查看设置页模型状态；免费体验用户确认微信已登录' },
  { q: '图片识别不工作？', a: '确保使用支持视觉的模型（Agnes、GPT-4o、Claude、Gemini 等）' },
  { q: '免费额度怎么算？', a: '微信扫码登录走 Agnes 免费模型，无次数限制；也可配置自己的 API Key，用多少扣多少' },
  { q: '技能太多找不到？', a: '在 Agent 管理 → 技能 tab 管理；对话中让 Agent 用 skill_view 按需加载，不必一次全装' },
  { q: '如何卸载？', a: 'Windows: 控制面板卸载 | macOS: 删除 Applications 中的 Vermes.app' },
]

const shortcuts = [
  { key: 'Enter', desc: '发送消息' },
  { key: 'Shift + Enter', desc: '换行' },
  { key: '⌘/Ctrl + K', desc: '命令面板（搜索页面/会话/动作）' },
  { key: '⌘/Ctrl + N', desc: '新建会话' },
  { key: '⌘/Ctrl + B', desc: '切换侧边栏' },
  { key: '⌘/Ctrl + ,', desc: '打开设置' },
  { key: '⌘/Ctrl + /', desc: '快速切换模型' },
  { key: '⌘/Ctrl + Shift + S', desc: '消息搜索' },
  { key: '⌘/Ctrl + Shift + E', desc: '导出当前会话' },
  { key: 'Escape', desc: '停止生成' },
]

const quickStarts = [
  { icon: '💬', text: '帮我分析这个行业趋势' },
  { icon: '⛩️', text: '造一个懂我的研究助手' },
  { icon: '📝', text: '帮我写一篇论文提纲' },
  { icon: '💻', text: '写一段 Python 代码' },
  { icon: '🧱', text: '帮我找一个能搜文献的技能' },
  { icon: '📊', text: '把这个 Excel 做成图表' },
]
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" @click.self="emit('close')">
    <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden">
      
      <!-- 头部 -->
      <div class="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 bg-green-500 rounded-xl flex items-center justify-center text-white font-bold text-lg">V</div>
          <div>
            <h2 class="text-lg font-bold text-gray-800 dark:text-gray-100">Vermes 使用指南</h2>
            <p class="text-xs text-gray-500 dark:text-gray-400">可自学习、自进化、可定制的桌面级 AI Agent 助手</p>
          </div>
        </div>
        <button @click="emit('close')" class="p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition">
          <svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
          </svg>
        </button>
      </div>

      <div class="flex flex-1 overflow-hidden">
        <!-- 左侧导航 -->
        <div class="w-44 border-r border-gray-200 dark:border-gray-700 p-3 space-y-1 overflow-y-auto shrink-0">
          <button v-for="s in sections" :key="s.id"
            @click="activeSection = s.id"
            class="w-full text-left px-3 py-2 rounded-lg text-sm transition flex items-center gap-2"
            :class="activeSection === s.id ? 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300 font-medium' : 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'">
            <span>{{ s.icon }}</span>
            <span>{{ s.title }}</span>
          </button>
        </div>

        <!-- 右侧内容 -->
        <div class="flex-1 overflow-y-auto p-6">
          
          <!-- 快速开始 -->
          <div v-if="activeSection === 'start'">
            <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-4">🚀 快速开始</h3>
            
            <div class="bg-green-50 dark:bg-green-900/20 rounded-xl p-4 mb-6 border border-green-200 dark:border-green-800">
              <p class="text-sm text-green-700 dark:text-green-300 font-medium mb-2">三步开始：</p>
              <div class="space-y-1 text-sm text-green-600 dark:text-green-400">
                <p>1️⃣ 打开 Vermes，微信扫码登录（走 Agnes 免费模型）</p>
                <p>2️⃣ 在输入框输入问题</p>
                <p>3️⃣ 按 Enter 发送</p>
              </div>
            </div>

            <p class="text-sm text-gray-600 dark:text-gray-400 mb-3">试试这些：</p>
            <div class="grid grid-cols-2 gap-2">
              <div v-for="item in quickStarts" :key="item.text"
                class="p-3 bg-gray-50 dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600">
                <span class="text-lg mr-2">{{ item.icon }}</span>
                <span class="text-sm text-gray-700 dark:text-gray-300">"{{ item.text }}"</span>
              </div>
            </div>

            <div class="mt-6 p-4 bg-blue-50 dark:bg-blue-900/20 rounded-xl border border-blue-200 dark:border-blue-800">
              <p class="text-sm text-blue-700 dark:text-blue-300">
                💡 <strong>不只是聊天</strong>：Vermes 是能自进化的 Agent —— 左侧边栏藏着论文写作、3D 建模、工作流编排、神魔堂多 Agent 协作、积木市场等完整生态，点击「功能地图」了解全貌。
              </p>
            </div>
          </div>

          <!-- 功能地图 -->
          <div v-if="activeSection === 'modules'">
            <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-4">🗺️ 功能地图</h3>
            <div class="grid grid-cols-1 gap-3">
              <div v-for="m in modules" :key="m.title"
                class="p-4 bg-gray-50 dark:bg-gray-700 rounded-xl border border-gray-200 dark:border-gray-600">
                <div class="flex items-start gap-3">
                  <span class="text-2xl">{{ m.icon }}</span>
                  <div>
                    <h4 class="font-medium text-gray-800 dark:text-gray-200">{{ m.title }}</h4>
                    <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ m.desc }}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Agent 与技能 -->
          <div v-if="activeSection === 'agents'">
            <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-4">🤖 Agent 与技能</h3>
            <p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
              Vermes 的核心是 <strong class="text-gray-700 dark:text-gray-200">Agent 生态</strong>：技能（Skill）是 Agent 的"招式"，工具（Toolset）是"武器"，MCP 是"外援"，记忆是"经验"，神魔堂让你编排一支 Agent 团队。
            </p>
            <div class="space-y-3">
              <div v-for="a in agents" :key="a.title"
                class="p-4 bg-gray-50 dark:bg-gray-700 rounded-xl border border-gray-200 dark:border-gray-600">
                <div class="flex items-start gap-3">
                  <span class="text-2xl">{{ a.icon }}</span>
                  <div>
                    <h4 class="font-medium text-gray-800 dark:text-gray-200">{{ a.title }}</h4>
                    <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ a.desc }}</p>
                  </div>
                </div>
              </div>
            </div>
            <div class="mt-4 p-4 bg-purple-50 dark:bg-purple-900/20 rounded-xl border border-purple-200 dark:border-purple-800">
              <p class="text-sm text-purple-700 dark:text-purple-300">
                🧭 <strong>入口</strong>：🤖 Agent 管理（已装管理）· 🧱 积木市场（发现安装）· ⛩️ 神魔堂（多 Agent 协作）
              </p>
            </div>
          </div>

          <!-- 模型与额度 -->
          <div v-if="activeSection === 'models'">
            <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-4">🔑 模型与额度</h3>
            <div class="space-y-3">
              <div v-for="t in modelTips" :key="t.title"
                class="p-4 bg-gray-50 dark:bg-gray-700 rounded-xl border border-gray-200 dark:border-gray-600">
                <div class="flex items-start gap-3">
                  <span class="text-2xl">{{ t.icon }}</span>
                  <div>
                    <h4 class="font-medium text-gray-800 dark:text-gray-200">{{ t.title }}</h4>
                    <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ t.desc }}</p>
                  </div>
                </div>
              </div>
            </div>
            <div class="mt-4 p-4 bg-gray-50 dark:bg-gray-700 rounded-xl border border-gray-200 dark:border-gray-600">
              <h4 class="font-medium text-gray-800 dark:text-gray-200 mb-2">输出上限 (max_tokens)</h4>
              <p class="text-sm text-gray-500 dark:text-gray-400">在设置页配置。不设置 = 让模型自己决定（推荐）。</p>
            </div>
          </div>

          <!-- 快捷操作 -->
          <div v-if="activeSection === 'shortcuts'">
            <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-4">⌨️ 快捷操作</h3>
            <div class="space-y-2">
              <div v-for="s in shortcuts" :key="s.key"
                class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600">
                <span class="text-sm text-gray-700 dark:text-gray-300">{{ s.desc }}</span>
                <kbd class="px-2 py-1 bg-gray-200 dark:bg-gray-600 rounded text-xs font-mono text-gray-600 dark:text-gray-300">{{ s.key }}</kbd>
              </div>
            </div>

            <div class="mt-6 p-4 bg-purple-50 dark:bg-purple-900/20 rounded-xl border border-purple-200 dark:border-purple-800">
              <h4 class="font-medium text-purple-800 dark:text-purple-200 mb-2">💡 小技巧</h4>
              <ul class="text-sm text-purple-600 dark:text-purple-400 space-y-1">
                <li>• ⌘K 命令面板：不点鼠标也能跳转任意页面</li>
                <li>• 拖拽图片/文件到输入框直接识别</li>
                <li>• 多轮对话自动记住上下文</li>
                <li>• 左侧边栏管理多个会话</li>
                <li>• 点击消息右上角可复制</li>
              </ul>
            </div>
          </div>

          <!-- 常见问题 -->
          <div v-if="activeSection === 'faq'">
            <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-4">❓ 常见问题</h3>
            <div class="space-y-3">
              <div v-for="f in faqs" :key="f.q"
                class="p-4 bg-gray-50 dark:bg-gray-700 rounded-xl border border-gray-200 dark:border-gray-600">
                <h4 class="font-medium text-gray-800 dark:text-gray-200 mb-2">{{ f.q }}</h4>
                <p class="text-sm text-gray-500 dark:text-gray-400">{{ f.a }}</p>
              </div>
            </div>
          </div>

        </div>
      </div>

      <!-- 底部 -->
      <div class="px-6 py-3 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
        <div class="flex items-center justify-between">
          <p class="text-xs text-gray-400 dark:text-gray-500">
            Vermes AI Agent © Vbit.top
          </p>
          <a href="https://vbit.top" target="_blank" class="text-xs text-green-500 hover:text-green-600 transition">
            vbit.top ↗
          </a>
        </div>
      </div>
    </div>
  </div>
</template>
