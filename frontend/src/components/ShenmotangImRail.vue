<template>
  <!-- 神魔堂 IM 左栏（T1）：联系人 / 群 常驻混合列表 + ⊕ 入口
       P3：与全局侧栏并排时收窄到 w-56，少占 32px，减轻双左栏横向挤压 -->
  <aside class="w-56 shrink-0 h-full flex flex-col border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/60">
    <!-- 顶：搜索 + ⊕ -->
    <div class="p-3 space-y-2 border-b border-gray-200 dark:border-gray-700">
      <div class="flex items-center gap-2">
        <input
          v-model="q"
          type="search"
          placeholder="搜索联系人 / 群"
          class="flex-1 px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:ring-2 focus:ring-indigo-400"
        />
        <button
          @click="$emit('create-group')"
          title="拉群 / 建群（建群即建组织）"
          class="w-9 h-9 shrink-0 rounded-lg bg-indigo-500 hover:bg-indigo-600 text-white text-lg leading-none shadow-sm transition"
        >＋</button>
      </div>
      <button
        @click="$emit('add-contact')"
        class="w-full px-3 py-1.5 text-xs rounded-lg border border-dashed border-gray-300 dark:border-gray-600 text-gray-500 hover:border-indigo-400 hover:text-indigo-500 transition"
      >＋ 添加联系人（请神登堂）</button>
    </div>

    <!-- 列表 -->
    <div class="flex-1 overflow-y-auto py-2">
      <!-- 群 -->
      <div v-if="filteredRooms.length" class="mb-3">
        <div class="px-3 mb-1 text-[11px] font-semibold text-gray-400 uppercase tracking-wide">群聊 · {{ filteredRooms.length }}</div>
        <button
          v-for="r in filteredRooms"
          :key="r.id || r.room_id"
          @click="$emit('select-room', r)"
          class="w-full px-3 py-2 flex items-center gap-2.5 hover:bg-indigo-50 dark:hover:bg-indigo-900/30 transition text-left"
          :class="selectedKey === ('room:' + (r.id || r.room_id)) ? 'bg-indigo-100 dark:bg-indigo-900/40' : ''"
        >
          <span class="w-9 h-9 rounded-xl bg-indigo-100 dark:bg-indigo-900/50 text-indigo-600 dark:text-indigo-300 flex items-center justify-center text-base shrink-0">▣</span>
          <span class="min-w-0 flex-1">
            <span class="block text-sm font-medium truncate text-gray-900 dark:text-gray-100">{{ r.name || r.title || '未命名群' }}</span>
            <span class="block text-[11px] text-gray-400 truncate">
              {{ (r.members || []).length ? `${(r.members || []).length} 成员` : (r.announcement || '群聊') }}
            </span>
          </span>
        </button>
      </div>

      <!-- 联系人 -->
      <div v-if="filteredContacts.length">
        <div class="px-3 mb-1 text-[11px] font-semibold text-gray-400 uppercase tracking-wide">联系人 · {{ filteredContacts.length }}</div>
        <button
          v-for="c in filteredContacts"
          :key="c.id"
          @click="$emit('select-contact', c)"
          class="w-full px-3 py-2 flex items-center gap-2.5 hover:bg-emerald-50 dark:hover:bg-emerald-900/25 transition text-left"
          :class="selectedKey === ('contact:' + c.id) ? 'bg-emerald-100 dark:bg-emerald-900/35' : ''"
        >
          <span class="relative shrink-0">
            <span
              class="w-9 h-9 rounded-full flex items-center justify-center text-sm font-semibold text-white"
              :style="{ background: `hsl(${c.hue || 210}, 55%, 48%)` }"
            >{{ (c.name || '?').slice(0, 1) }}</span>
            <!-- 在线状态点（T2）：有 key / ACP = 在线绿点，本地 = 琥珀，否则灰 -->
            <span
              class="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-white dark:border-gray-900"
              :class="c.has_api_key || c.transport === 'acp'
                ? 'bg-emerald-400'
                : (c.transport === 'cli' || c.transport === 'native' ? 'bg-amber-400' : 'bg-gray-300')"
            />
          </span>
          <span class="min-w-0 flex-1">
            <span class="block text-sm font-medium truncate text-gray-900 dark:text-gray-100">{{ c.name }}</span>
            <span class="block text-[11px] text-gray-400 truncate">
              {{ c.transport === 'acp' ? 'ACP' : '本地' }}
              <template v-if="c.provider"> · {{ c.provider }}</template>
            </span>
          </span>
        </button>
      </div>

      <!-- 空态 -->
      <div v-if="!filteredRooms.length && !filteredContacts.length" class="px-4 py-8 text-center">
        <div class="text-2xl mb-2">⛩️</div>
        <p class="text-xs text-gray-400 leading-relaxed">
          {{ q ? '无匹配' : '还没有联系人 / 群' }}<br/>
          {{ q ? '' : '点上方 ＋ 开始拉群或请神' }}
        </p>
      </div>
    </div>

    <!-- 底：用量入口（T7 预留） -->
    <button
      @click="$emit('open-usage')"
      class="px-4 py-2.5 border-t border-gray-200 dark:border-gray-700 text-left text-xs text-gray-500 hover:text-indigo-500 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
    >📈 用量与成本</button>
  </aside>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../services/api'

const props = defineProps({
  selectedKey: { type: String, default: '' },
})
defineEmits(['select-contact', 'select-room', 'create-group', 'add-contact', 'open-usage'])

const q = ref('')
const contacts = ref([])
const rooms = ref([])

function match(s) {
  const t = (s || '').toLowerCase()
  return !q.value || t.includes(q.value.toLowerCase())
}

const filteredContacts = computed(() =>
  contacts.value.filter(c => match(c.name) || match(c.provider) || match(c.model))
)
const filteredRooms = computed(() => {
  // UX：dm-* 是 1:1 私聊房（T2 ensureDmRoom），不进「群聊」列表——
  // 否则会和联系人区重复冒出「1:1 · Codex」
  const groups = rooms.value.filter(r => {
    const id = String(r.id || r.room_id || '')
    const name = String(r.name || r.title || '')
    return !id.startsWith('dm-') && !name.startsWith('1:1 ·')
  })
  return groups.filter(r => match(r.name) || match(r.title) || match(r.announcement))
})

async function load() {
  try {
    const [ct, rs] = await Promise.all([
      api.listAgentContacts().catch(() => null),
      api.listBotRooms().catch(() => null),
    ])
    contacts.value = (ct && ct.contacts) || (ct && ct.profiles) || []
    rooms.value = (rs && rs.rooms) || (rs && Array.isArray(rs) ? rs : []) || []
  } catch (e) {
    console.error('[ShenmotangImRail] load failed', e)
  }
}

onMounted(load)
defineExpose({ reload: load })
</script>
