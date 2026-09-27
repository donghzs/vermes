<template>
  <!-- T3 ⊕ 拉群：选人显式建群（可选搭组织 applyBotOrg），不走秘书隐式拉人 -->
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
    @click.self="$emit('close')"
  >
    <div class="w-[28rem] max-w-[92vw] max-h-[85vh] overflow-y-auto rounded-2xl bg-white dark:bg-gray-800 p-5 shadow-2xl">
      <div class="flex items-start justify-between gap-3 mb-1">
        <div>
          <h3 class="text-base font-semibold">⊕ 拉群</h3>
          <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            选联系人建群；从 1:1 发起会把对方预选进来
          </p>
        </div>
        <button
          class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-lg leading-none px-1"
          @click="$emit('close')"
        >✕</button>
      </div>

      <!-- 群名 -->
      <label class="block mt-4 text-xs font-medium text-gray-500 dark:text-gray-400">群名称</label>
      <input
        v-model="name"
        type="text"
        class="mt-1 w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 outline-none focus:ring-2 focus:ring-indigo-400"
        placeholder="例如：论文攻坚"
        @keydown.enter.prevent="submit"
      />

      <!-- 联系人多选 -->
      <div class="mt-4 flex items-center justify-between">
        <span class="text-xs font-medium text-gray-500 dark:text-gray-400">
          联系人 · 已选 {{ selected.length }}
        </span>
        <button
          v-if="contacts.length"
          class="text-[11px] text-indigo-500 hover:underline"
          @click="toggleAll"
        >{{ allSelected ? '清空' : '全选' }}</button>
      </div>
      <div v-if="loading" class="mt-2 text-xs text-gray-400">加载联系人…</div>
      <div v-else-if="!contacts.length" class="mt-2 text-sm text-gray-400 py-6 text-center">
        还没有联系人，先「＋ 添加联系人」请神登堂
      </div>
      <div v-else class="mt-2 max-h-52 overflow-y-auto space-y-1">
        <label
          v-for="c in contacts"
          :key="c.id"
          class="flex items-center gap-2.5 px-2 py-1.5 rounded-lg hover:bg-indigo-50 dark:hover:bg-indigo-900/25 cursor-pointer"
        >
          <input
            type="checkbox"
            class="accent-indigo-500"
            :checked="selected.includes(c.id)"
            @change="toggle(c.id)"
          />
          <span
            class="w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold text-white shrink-0"
            :style="{ background: `hsl(${c.hue || 210}, 55%, 48%)` }"
          >{{ (c.name || '?').slice(0, 1) }}</span>
          <span class="min-w-0 flex-1">
            <span class="block text-sm truncate">{{ c.name }}</span>
            <span class="block text-[11px] text-gray-400 truncate">
              {{ c.transport === 'acp' ? 'ACP' : '本地' }}<template v-if="c.provider"> · {{ c.provider }}</template>
            </span>
          </span>
          <span
            v-if="c.id === preselectId"
            class="text-[10px] px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-600 dark:bg-indigo-900/50 dark:text-indigo-300 shrink-0"
          >1:1</span>
        </label>
      </div>

      <!-- 可选：搭组织（复用 applyBotOrg / ORG_TEMPLATES） -->
      <label class="mt-4 flex items-center gap-2 text-sm cursor-pointer">
        <input v-model="useOrg" type="checkbox" class="accent-indigo-500" />
        <span>搭组织（按模板把选中的人坐岗，显式拉人）</span>
      </label>
      <div v-if="useOrg" class="mt-2">
        <div v-if="loadingOrg" class="text-xs text-gray-400">加载模板…</div>
        <div v-else class="flex flex-wrap gap-1.5">
          <button
            v-for="t in templates"
            :key="t.key"
            class="px-2.5 py-1 text-xs rounded-full border transition"
            :class="orgKey === t.key
              ? 'border-indigo-500 bg-indigo-50 text-indigo-600 dark:bg-indigo-900/40 dark:text-indigo-300'
              : 'border-gray-200 dark:border-gray-600 text-gray-500 hover:border-indigo-300'"
            @click="orgKey = t.key"
          >{{ t.title || t.key }}</button>
        </div>
        <p class="mt-1.5 text-[11px] text-gray-400 leading-relaxed">
          岗位将从已勾选联系人中按模板自动坐满；未勾选的不进群。
        </p>
      </div>

      <!-- 动作 -->
      <div class="mt-5 flex items-center justify-end gap-2">
        <button
          class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600"
          @click="$emit('close')"
        >取消</button>
        <button
          class="px-4 py-1.5 text-sm rounded-lg bg-indigo-500 hover:bg-indigo-600 text-white disabled:opacity-50 disabled:cursor-not-allowed transition"
          :disabled="submitting || !name.trim() || !selected.length"
          @click="submit"
        >{{ submitting ? '建群中…' : `建群（${selected.length} 人）` }}</button>
      </div>

      <p v-if="error" class="mt-2 text-xs text-rose-500">{{ error }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../services/api'
import { toast } from '../utils/toast'

const props = defineProps({
  /** 从 1:1 发起时预选对方 */
  preselectId: { type: String, default: '' },
  preselectName: { type: String, default: '' },
})
const emit = defineEmits(['close', 'created'])

const name = ref('')
const contacts = ref([])
const selected = ref([])
const loading = ref(false)
const submitting = ref(false)
const error = ref('')

const useOrg = ref(false)
const templates = ref([])
const orgKey = ref('')
const loadingOrg = ref(false)

const allSelected = computed(() =>
  contacts.value.length > 0 && selected.value.length === contacts.value.length
)

function toggle(id) {
  const i = selected.value.indexOf(id)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(id)
}
function toggleAll() {
  selected.value = allSelected.value ? [] : contacts.value.map(c => c.id)
}

async function load() {
  loading.value = true
  try {
    const r = await api.listAgentContacts().catch(() => null)
    contacts.value = (r && r.contacts) || (r && r.profiles) || []
    if (props.preselectId && !selected.value.includes(props.preselectId)) {
      selected.value = [props.preselectId, ...selected.value]
    }
    if (props.preselectName && !name.value) {
      name.value = `${props.preselectName} 群`
    }
  } catch (e) {
    error.value = e.message || '加载联系人失败'
  } finally {
    loading.value = false
  }
}

async function loadTemplates() {
  loadingOrg.value = true
  try {
    const tr = await api.getBotOrgTemplates().catch(() => null)
    templates.value = (tr && tr.templates) || []
    if (templates.value.length && !orgKey.value) orgKey.value = templates.value[0].key
  } catch {
    templates.value = []
  } finally {
    loadingOrg.value = false
  }
}

/**
 * 显式建群：createBotRoom(name, selected) 直接拉人；
 * 勾「搭组织」才 applyBotOrg（后端显式拉坐岗），绝不走秘书隐式造神。
 */
async function submit() {
  const gname = (name.value || '').trim()
  if (!gname || !selected.value.length || submitting.value) return
  submitting.value = true
  error.value = ''
  try {
    const r = await api.createBotRoom(gname, [...selected.value])
    if (!(r && r.ok && (r.room_id || r.id))) {
      error.value = (r && r.error) || '建群失败'
      return
    }
    const roomId = r.room_id || r.id
    let orgOk = false
    if (useOrg.value && orgKey.value) {
      const tpl = templates.value.find(t => t.key === orgKey.value)
      const roles = ((tpl && tpl.roles) || [])
        .slice(0, selected.value.length)
        .map((role, i) => ({ ...role, profile_id: selected.value[i] }))
        .filter(role => role.profile_id)
      if (roles.length) {
        const orgRes = await api.applyBotOrg(roomId, roles)
        orgOk = !!(orgRes && orgRes.ok)
        if (!orgOk) toast.error((orgRes && orgRes.error) || '群已建，组织搭建失败')
      }
    }
    toast.success(orgOk ? '群已建，组织已就绪 ✓' : '群已建 ✓')
    // 建群反馈：抛给父级做选中 + 刷新左栏
    emit('created', { id: roomId, name: gname })
    emit('close')
  } catch (e) {
    error.value = e.message || '建群失败'
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  load()
  loadTemplates()
})

defineExpose({ submit, toggle })
</script>
