<script setup>
// 通用 JSON Schema → 表单组件。
//
// 决策 #5（用户 2026-07-25 评审）：
//  - P0c-1 支持 string / integer / boolean + enum。
//  - project_id 字段：从 useScholarStore.currentProjectId 自动填充，UI 不渲染输入框。
//
// P0c-2 扩展（按 22 工具 schema 实测类型分布 string×48/integer×34/boolean×6/number×7/array×1/object×3）：
//  - number → number input（step=any，接受小数，如 p_value/cohens_d）
//  - array（items:string，仅 intervention_elements 1 处）→ 文本框按换行/逗号分隔
//  - object（stats/design_info 等自由字典）→ JSON 文本框 + 解析校验
//    注：3 处 object 均为自由 key 字典而非固定嵌套 schema，JSON 输入比嵌套表单更合适。
import { ref, reactive, computed, watch } from 'vue'
import { useScholarStore } from '../../stores/scholar'

const props = defineProps({
  schema: { type: Object, required: true },
})
const emit = defineEmits(['submit'])

const scholar = useScholarStore()

const properties = computed(() => (props.schema?.parameters?.properties) || {})
const required = computed(() => (props.schema?.parameters?.required) || [])
const propEntries = computed(() => Object.entries(properties.value).filter(([k]) => k !== 'project_id'))

const form = reactive({})
const error = ref('')

function initForm() {
  // 清空并依据 schema 初始化默认值
  for (const k of Object.keys(form)) delete form[k]
  for (const [k, v] of Object.entries(properties.value)) {
    if (k === 'project_id') continue
    if (v.default !== undefined) {
      // object/array 默认值序列化为文本供编辑
      if (v.type === 'object') form[k] = JSON.stringify(v.default, null, 2)
      else if (v.type === 'array') form[k] = Array.isArray(v.default) ? v.default.join('\n') : ''
      else form[k] = v.default
    }
    else if (v.type === 'boolean') form[k] = false
    else if (v.type === 'integer' || v.type === 'number') form[k] = (v.minimum !== undefined ? v.minimum : '')
    else form[k] = ''
  }
}
watch(() => props.schema, initForm, { immediate: true })

function fieldType(v) {
  if (v.enum) return 'enum'
  return v.type || 'string'
}

function buildArgs() {
  const args = {}
  for (const [k, v] of Object.entries(properties.value)) {
    if (k === 'project_id') {
      if (scholar.currentProjectId != null) args[k] = scholar.currentProjectId
      continue
    }
    let val = form[k]
    if (v.type === 'integer' || v.type === 'number') {
      if (val === '' || val === null || val === undefined) {
        if (required.value.includes(k)) throw new Error(`请填写必填项：${k}`)
        continue
      }
      val = Number(val)
      if (Number.isNaN(val)) throw new Error(`${k} 不是有效数字`)
    } else if (v.type === 'boolean') {
      val = !!val
    } else if (v.type === 'array') {
      // 文本框 → 按换行或逗号分隔为字符串数组
      const items = String(val || '')
        .split(/[\n,，]/)
        .map((s) => s.trim())
        .filter(Boolean)
      if (!items.length) {
        if (required.value.includes(k)) throw new Error(`请填写必填项：${k}`)
        continue
      }
      val = items
    } else if (v.type === 'object') {
      const text = String(val || '').trim()
      if (!text) {
        if (required.value.includes(k)) throw new Error(`请填写必填项：${k}`)
        continue
      }
      try {
        val = JSON.parse(text)
      } catch (e) {
        throw new Error(`${k} 不是合法 JSON：${e.message}`)
      }
      if (val === null || typeof val !== 'object' || Array.isArray(val)) {
        throw new Error(`${k} 需要是 JSON 对象（形如 {"key": value}）`)
      }
    } else {
      if (val === '' || val === null || val === undefined) {
        if (required.value.includes(k)) throw new Error(`请填写必填项：${k}`)
        continue
      }
    }
    args[k] = val
  }
  return args
}

function onSubmit() {
  error.value = ''
  try {
    const args = buildArgs()
    emit('submit', args)
  } catch (e) {
    error.value = e.message
  }
}

function onReset() {
  error.value = ''
  initForm()
}
</script>

<template>
  <form class="space-y-3" @submit.prevent="onSubmit">
    <div
      v-for="[key, def] in propEntries"
      :key="key"
      class="flex flex-col gap-1"
    >
      <label class="text-xs font-medium text-gray-600 dark:text-gray-300">
        {{ key }}
        <span v-if="required.includes(key)" class="text-red-500">*</span>
        <span class="text-gray-400 font-normal">（{{ def.type || 'string' }}）</span>
      </label>

      <!-- enum → select -->
      <select
        v-if="fieldType(def) === 'enum'"
        v-model="form[key]"
        class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm"
      >
        <option v-for="opt in def.enum" :key="opt" :value="opt">{{ opt }}</option>
      </select>

      <!-- boolean → checkbox -->
      <label v-else-if="def.type === 'boolean'" class="inline-flex items-center gap-2 text-sm">
        <input type="checkbox" v-model="form[key]" class="rounded" />
        <span class="text-gray-500">{{ def.description }}</span>
      </label>

      <!-- integer / number → number input（number 允许小数） -->
      <input
        v-else-if="def.type === 'integer' || def.type === 'number'"
        type="number"
        v-model="form[key]"
        :min="def.minimum"
        :max="def.maximum"
        :step="def.type === 'number' ? 'any' : 1"
        class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm"
      />

      <!-- array（items:string）→ 换行/逗号分隔文本框 -->
      <textarea
        v-else-if="def.type === 'array'"
        v-model="form[key]"
        rows="3"
        placeholder="每行一项，或用逗号分隔"
        class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm font-mono"
      ></textarea>

      <!-- object → JSON 文本框 -->
      <textarea
        v-else-if="def.type === 'object'"
        v-model="form[key]"
        rows="4"
        placeholder='JSON 对象，如 {"p_value": 0.03, "cohens_d": 0.5}'
        class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm font-mono"
      ></textarea>

      <!-- string → textarea -->
      <textarea
        v-else
        v-model="form[key]"
        rows="4"
        :placeholder="def.description"
        class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm font-mono"
      ></textarea>

      <p v-if="def.description && def.type !== 'boolean'" class="text-xs text-gray-400 leading-snug">
        {{ def.description }}
      </p>
    </div>

    <p v-if="error" class="text-sm text-red-500">{{ error }}</p>

    <div class="flex gap-2 pt-1">
      <button
        type="submit"
        class="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium transition"
      >
        运行工具
      </button>
      <button
        type="button"
        @click="onReset"
        class="px-4 py-2 rounded-lg bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 text-sm transition"
      >
        重置
      </button>
    </div>
  </form>
</template>
